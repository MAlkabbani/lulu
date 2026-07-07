import AppKit
import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published var connectionStatus: String = "Starting local backend..."
    @Published var backendHealthy = false
    @Published var websocketConnected = false
    @Published var runtimeState: RuntimeStateResponse?
    @Published var dependencyHealth: DependencyHealthResponse?
    @Published var settings: SettingsResponse?
    @Published var settingsDraft = SettingsDraft()
    @Published var settingsSaveMessage = ""
    @Published var transcript = ""
    @Published var response = ""
    @Published var eventLog: [String] = []
    @Published var diagnosticsNote = ""
    @Published var wakeGuidance = "Say the wake phrase, pause briefly, then speak your request."
    @Published var conversationWindowRemaining: Double?
    @Published var cooldownRemaining: Double?
    @Published var wakeAttempt = WakeAttemptSnapshot()
    @Published var wakeSignal = WakeSignalSnapshot()
    @Published var latencies: [String: Double] = [:]
    @Published var ttsProgress = TTSProgressSnapshot()
    @Published var savedMemories: [String] = []
    @Published var invocationSummary = UserFacingText.noActivityYet
    @Published var memoryHitCount = 0
    @Published var actionSummary = UserFacingText.noActivityYet
    @Published var currentToolStatus = UserFacingText.noActivityYet
    @Published var recentWakeAttempts: [String] = []
    @Published var recentRuntimeEvents: [String] = []
    @Published var voicePreflight = VoicePreflightSnapshot()
    @Published var runtimeActive = false
    @Published var pdfDraft = PDFJobDraft()
    @Published var pdfJob: PDFJobResponse?
    @Published var pdfStatusMessage = ""
    @Published var pdfWorkflowBusy = false

    private let backend = BackendServiceCoordinator()
    private var eventTask: Task<Void, Never>?
    private var pdfPollingTask: Task<Void, Never>?

    func bootstrap() async {
        eventTask?.cancel()
        websocketConnected = false
        backendHealthy = false
        runtimeActive = false
        diagnosticsNote = ""
        do {
            connectionStatus = "Launching local backend..."
            try await backend.launchIfNeeded()
            try await backend.waitUntilHealthy()
            backendHealthy = true
            connectionStatus = "Backend ready. Connecting runtime events..."
            await attachEvents()
            connectionStatus = "Backend ready. Loading runtime state..."
            async let fetchedSettings = backend.fetchSettings()
            async let fetchedDependencies = backend.fetchDependencies()
            async let fetchedState = backend.fetchRuntimeState()
            async let fetchedDiagnostics = backend.fetchRuntimeDiagnostics()
            settings = try await fetchedSettings
            dependencyHealth = try await fetchedDependencies
            runtimeState = try await fetchedState
            apply(diagnostics: try await fetchedDiagnostics)
            await refreshMicrophoneStatus()
            if let settings {
                settingsDraft = SettingsDraft(from: settings)
                applyDefaultPDFOutputDirectory(from: settings)
            }
            connectionStatus = "Desktop shell connected to Lulu. Start a voice runtime when ready."
            appendEvent("Desktop shell bootstrapped successfully.")
        } catch {
            backendHealthy = false
            websocketConnected = false
            runtimeState = nil
            dependencyHealth = nil
            runtimeActive = false
            connectionStatus = "Failed to start the local backend."
            diagnosticsNote = error.localizedDescription
            appendEvent("Bootstrap failed: \(error.localizedDescription)")
        }
    }

    func shutdown() async {
        eventTask?.cancel()
        pdfPollingTask?.cancel()
        websocketConnected = false
        await backend.shutdown()
    }

    func refreshDiagnostics() async {
        guard await ensureBackendReady(for: "Diagnostics refresh", allowBootstrapRetry: true) else {
            return
        }
        do {
            async let fetchedDependencies = backend.fetchDependencies()
            async let fetchedState = backend.fetchRuntimeState()
            async let fetchedDiagnostics = backend.fetchRuntimeDiagnostics()
            dependencyHealth = try await fetchedDependencies
            runtimeState = try await fetchedState
            apply(diagnostics: try await fetchedDiagnostics)
            await refreshMicrophoneStatus()
            if let settings {
                applyDefaultPDFOutputDirectory(from: settings)
            }
            diagnosticsNote = await backend.capturedLogs()
        } catch {
            backendHealthy = false
            websocketConnected = false
            runtimeActive = false
            connectionStatus = "Failed to refresh backend state."
            diagnosticsNote = "Refresh failed: \(error.localizedDescription)"
        }
    }

    func saveSettings() async {
        guard await ensureBackendReady(for: "Settings save") else {
            return
        }
        do {
            let update = try await backend.saveSettings(settingsDraft)
            settingsSaveMessage = update.restartRequired
                ? "Settings saved to \(update.configPath). Restart the app to apply every change."
                : "Settings saved."
            settings = try await backend.fetchSettings()
            if let settings {
                settingsDraft = SettingsDraft(from: settings)
                applyDefaultPDFOutputDirectory(from: settings)
            }
            appendEvent("Settings saved for the desktop app.")
        } catch {
            settingsSaveMessage = "Save failed: \(error.localizedDescription)"
            appendEvent("Settings save failed: \(error.localizedDescription)")
        }
    }

    private func attachEvents() async {
        eventTask?.cancel()
        do {
            eventTask = try await backend.connectEvents { [weak self] envelope in
                Task { @MainActor in
                    self?.apply(event: envelope)
                }
            }
            websocketConnected = true
        } catch {
            websocketConnected = false
            appendEvent("Event stream setup failed: \(error.localizedDescription)")
        }
    }

    func startContinuousVoiceMode() async {
        guard await ensureMicrophoneAccessForVoiceMode() else { return }
        await startRuntime(mode: "continuous", successMessage: "Continuous voice runtime started.")
    }

    func startTurnBasedVoiceMode() async {
        guard await ensureMicrophoneAccessForVoiceMode() else { return }
        await startRuntime(mode: "turn-based", successMessage: "Turn-based voice runtime started.")
    }

    func stopRuntime() async {
        guard await ensureBackendReady(for: "Runtime stop") else {
            return
        }
        do {
            runtimeState = try await backend.stopRuntime()
            runtimeActive = false
            connectionStatus = "Voice runtime stopped."
            appendEvent("Runtime stopped from desktop shell.")
        } catch {
            diagnosticsNote = "Stop failed: \(error.localizedDescription)"
            appendEvent("Runtime stop failed: \(error.localizedDescription)")
        }
    }

    func requestMicrophoneAccess() async {
        voicePreflight.microphoneStatus = await MicrophoneAuthorizationService.requestAccessIfNeeded()
        voicePreflight.guidance = guidanceForMicrophoneStatus(voicePreflight.microphoneStatus)
        appendEvent("Microphone access status: \(voicePreflight.microphoneStatus)")
    }

    func openPrivacySettings() {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone") {
            NSWorkspace.shared.open(url)
        }
    }

    func submitPDFJob() async {
        guard await ensureBackendReady(for: "PDF export", allowBootstrapRetry: true) else {
            return
        }

        let selectedOutputDir = pdfDraft.outputDir.trimmingCharacters(in: .whitespacesAndNewlines)
        if !selectedOutputDir.isEmpty {
            do {
                try await persistPDFExportRootIfNeeded(selectedOutputDir)
            } catch {
                pdfWorkflowBusy = false
                pdfStatusMessage = "Could not save the export folder: \(error.localizedDescription)"
                appendEvent("PDF export root save failed: \(error.localizedDescription)")
                return
            }
        }

        let request = pdfDraft.createRequest
        guard !request.pdfPath.isEmpty else {
            pdfStatusMessage = "Choose a PDF file before starting a job."
            return
        }
        let effectiveOutputDir = request.outputDir ?? settings?.exportsPath ?? ""
        guard !effectiveOutputDir.isEmpty else {
            pdfStatusMessage = "Choose an export folder before starting a job."
            return
        }
        if !request.dryRun && request.portableFormat != "none" && dependencyHealth?.ffmpegAvailable == false {
            pdfStatusMessage = "Portable \(request.portableFormat.uppercased()) export requires ffmpeg in PATH. Choose None for AIFF-only export or install ffmpeg first."
            return
        }

        pdfWorkflowBusy = true
        pdfStatusMessage = request.dryRun ? "Submitting PDF dry run..." : "Submitting PDF export job..."
        do {
            let response = try await backend.createPDFJob(request)
            pdfJob = response
            pdfStatusMessage = response.dryRun ? "PDF dry run queued." : "PDF audiobook export queued."
            appendEvent("PDF job queued: \(response.jobID)")
            startPollingPDFJob(jobID: response.jobID)
        } catch {
            pdfWorkflowBusy = false
            pdfStatusMessage = "PDF job failed to start: \(error.localizedDescription)"
            appendEvent("PDF job submission failed: \(error.localizedDescription)")
        }
    }

    func refreshPDFJobStatus() async {
        guard let jobID = pdfJob?.jobID else {
            pdfStatusMessage = UserFacingText.noActivityYet
            return
        }
        guard await ensureBackendReady(for: "PDF status refresh", allowBootstrapRetry: true) else {
            return
        }
        await fetchPDFJob(jobID: jobID, updateBusyState: false)
    }

    func resetPDFWorkflow() {
        pdfPollingTask?.cancel()
        pdfPollingTask = nil
        pdfJob = nil
        pdfStatusMessage = ""
        pdfWorkflowBusy = false
    }

    private func apply(event: RuntimeEventEnvelope) {
        switch event.eventType {
        case "service.connected":
            websocketConnected = true
            appendEvent("Runtime event stream connected.")
        case "runtime.state_changed":
            if
                let mode = event.payload["mode"]?.stringValue,
                let runtimeMode = event.payload["runtime_mode"]?.stringValue,
                let statusLine = event.payload["status_line"]?.stringValue
            {
                runtimeState = RuntimeStateResponse(
                    apiVersion: event.apiVersion,
                    mode: mode,
                    runtimeMode: runtimeMode,
                    statusLine: statusLine,
                    degraded: runtimeState?.degraded ?? false,
                    lastError: runtimeState?.lastError ?? ""
                )
            }
            conversationWindowRemaining = event.payload["conversation_window_remaining"]?.numberValue
            cooldownRemaining = event.payload["cooldown_remaining"]?.numberValue
            wakeGuidance = event.payload["wake_guidance"]?.stringValue ?? wakeGuidance
            if let accepted = intValue(event.payload["accepted_wake_attempts"]) {
                wakeAttempt.acceptedCount = accepted
            }
            if let rejected = intValue(event.payload["rejected_wake_attempts"]) {
                wakeAttempt.rejectedCount = rejected
            }
            if let score = event.payload["last_wake_score"]?.numberValue {
                wakeAttempt.score = score
            }
            if let decision = event.payload["last_wake_decision"]?.stringValue {
                wakeAttempt.decision = decision
            }
            runtimeActive = runtimeState?.mode != "idle"
            appendEvent("Runtime changed: \(event.payload["status_line"]?.stringValue ?? event.eventType)")
        case "transcript.updated":
            transcript = event.payload["transcript"]?.stringValue ?? transcript
        case "response.partial", "response.final":
            response = event.payload["text"]?.stringValue ?? response
            if event.eventType == "response.final" {
                appendEvent("Received final backend response.")
            }
        case "memory.saved":
            let savedItems = event.payload["saved_items"]?.stringArrayValue ?? []
            savedMemories = Array((savedItems + savedMemories).prefix(8))
            appendEvent("Memory saved: \(savedItems.joined(separator: ", "))")
        case "router.invocation_updated":
            invocationSummary = event.payload["invocation_summary"]?.stringValue ?? invocationSummary
            memoryHitCount = intValue(event.payload["memory_hit_count"]) ?? memoryHitCount
            actionSummary = event.payload["action_summary"]?.stringValue ?? actionSummary
            currentToolStatus = event.payload["current_tool_status"]?.stringValue ?? currentToolStatus
            appendEvent(invocationSummary)
        case "tool.activity":
            actionSummary = event.payload["action_summary"]?.stringValue ?? actionSummary
            currentToolStatus = event.payload["current_tool_status"]?.stringValue ?? currentToolStatus
            if let detail = event.payload["detail"]?.stringValue {
                appendEvent(detail)
            }
        case "wake.guidance_updated":
            wakeGuidance = event.payload["guidance"]?.stringValue ?? wakeGuidance
        case "wake.signal_metrics":
            wakeSignal.confidence = event.payload["confidence"]?.numberValue
            wakeSignal.threshold = event.payload["threshold"]?.numberValue
            wakeSignal.acousticScore = event.payload["acoustic_score"]?.numberValue
            wakeSignal.dtwScore = event.payload["dtw_score"]?.numberValue
            wakeSignal.snrDB = event.payload["snr_db"]?.numberValue
            wakeSignal.featureFrames = intValue(event.payload["feature_frames"]) ?? wakeSignal.featureFrames
        case "wake.attempt":
            wakeAttempt.transcript = event.payload["transcript"]?.stringValue ?? wakeAttempt.transcript
            wakeAttempt.reason = event.payload["reason"]?.stringValue ?? wakeAttempt.reason
            wakeAttempt.score = event.payload["score"]?.numberValue ?? wakeAttempt.score
            wakeAttempt.accepted = event.payload["accepted"]?.boolValue ?? wakeAttempt.accepted
            wakeAttempt.decision = event.payload["last_wake_decision"]?.stringValue ?? wakeAttempt.decision
            wakeAttempt.acceptedCount = intValue(event.payload["accepted_wake_attempts"]) ?? wakeAttempt.acceptedCount
            wakeAttempt.rejectedCount = intValue(event.payload["rejected_wake_attempts"]) ?? wakeAttempt.rejectedCount
            recentWakeAttempts = Array(([wakeAttempt.decision] + recentWakeAttempts).prefix(10))
            appendEvent("Wake attempt: \(wakeAttempt.decision)")
        case "latency.snapshot":
            if let all = event.payload["latencies_ms"]?.objectValue {
                for (key, value) in all {
                    if let milliseconds = value.numberValue {
                        latencies[key] = milliseconds
                    }
                }
            } else if
                let label = event.payload["label"]?.stringValue,
                let milliseconds = event.payload["milliseconds"]?.numberValue
            {
                latencies[label] = milliseconds
            }
        case "tts.chunk_emitted":
            ttsProgress.lastEmittedChunk = event.payload["chunk"]?.stringValue ?? ttsProgress.lastEmittedChunk
            ttsProgress.emittedChunkCount = intValue(event.payload["emitted_chunk_count"]) ?? ttsProgress.emittedChunkCount
            ttsProgress.emittedCharCount = intValue(event.payload["emitted_char_count"]) ?? ttsProgress.emittedCharCount
        case "tts.chunk_spoken":
            ttsProgress.lastSpokenChunk = event.payload["chunk"]?.stringValue ?? ttsProgress.lastSpokenChunk
            ttsProgress.spokenChunkCount = intValue(event.payload["spoken_chunk_count"]) ?? ttsProgress.spokenChunkCount
            ttsProgress.spokenCharCount = intValue(event.payload["spoken_char_count"]) ?? ttsProgress.spokenCharCount
        case "error.reported":
            diagnosticsNote = event.payload["detail"]?.stringValue ?? "Unknown backend error"
            appendEvent("Backend error: \(diagnosticsNote)")
        default:
            appendEvent("Event: \(event.eventType)")
        }
    }

    private func startRuntime(mode: String, successMessage: String) async {
        guard await ensureBackendReady(for: "Runtime start") else {
            return
        }
        do {
            connectionStatus = "Starting \(UserFacingText.runtimeModeLabel(mode))..."
            runtimeState = try await backend.startRuntime(mode: mode)
            if let diagnostics = try? await backend.fetchRuntimeDiagnostics() {
                apply(diagnostics: diagnostics)
            }
            connectionStatus = successMessage
            appendEvent(successMessage)
        } catch {
            diagnosticsNote = "Runtime start failed: \(error.localizedDescription)"
            connectionStatus = "Failed to start \(UserFacingText.runtimeModeLabel(mode))."
            appendEvent("Runtime start failed: \(error.localizedDescription)")
        }
    }

    private func startPollingPDFJob(jobID: String) {
        pdfPollingTask?.cancel()
        pdfPollingTask = Task { @MainActor [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                await self.fetchPDFJob(jobID: jobID, updateBusyState: true)
                let status = self.pdfJob?.status ?? "unknown"
                if ["completed", "failed"].contains(status) {
                    self.pdfWorkflowBusy = false
                    return
                }
                try? await Task.sleep(nanoseconds: 1_000_000_000)
            }
        }
    }

    private func fetchPDFJob(jobID: String, updateBusyState: Bool) async {
        do {
            let response = try await backend.fetchPDFJob(jobID: jobID)
            pdfJob = response
            if updateBusyState {
                pdfWorkflowBusy = ["pending", "running"].contains(response.status)
            }
            switch response.status {
            case "completed":
                pdfStatusMessage = response.dryRun ? "PDF dry run completed." : "PDF audiobook export completed."
                appendEvent("PDF job completed: \(response.jobID)")
            case "failed":
                pdfStatusMessage = response.error ?? "PDF job failed."
                appendEvent("PDF job failed: \(response.error ?? response.jobID)")
            case "running":
                pdfStatusMessage = response.dryRun ? "PDF dry run is running..." : "PDF audiobook export is running..."
            default:
                pdfStatusMessage = response.dryRun ? "PDF dry run is pending..." : "PDF audiobook export is pending..."
            }
        } catch {
            if updateBusyState {
                pdfWorkflowBusy = false
            }
            pdfStatusMessage = "PDF status refresh failed: \(error.localizedDescription)"
            appendEvent("PDF job status refresh failed: \(error.localizedDescription)")
        }
    }

    private func ensureBackendReady(for action: String, allowBootstrapRetry: Bool = false) async -> Bool {
        guard !backendHealthy else {
            return true
        }
        if allowBootstrapRetry {
            await bootstrap()
            return backendHealthy
        }
        let message = "\(action) blocked: the backend is unavailable."
        diagnosticsNote = message
        connectionStatus = UserFacingText.backendUnavailable
        appendEvent(message)
        return false
    }

    private func intValue(_ value: JSONValue?) -> Int? {
        guard let number = value?.numberValue else {
            return nil
        }
        return Int(number)
    }

    private func appendEvent(_ line: String) {
        eventLog.append(line)
        recentRuntimeEvents = Array(([line] + recentRuntimeEvents).prefix(15))
        if eventLog.count > 100 {
            eventLog.removeFirst(eventLog.count - 100)
        }
    }

    private func persistPDFExportRootIfNeeded(_ outputDir: String) async throws {
        let normalizedOutputDir = outputDir.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedOutputDir.isEmpty else {
            return
        }
        if settings?.exportsPath == normalizedOutputDir {
            return
        }
        var updatedDraft = settingsDraft
        updatedDraft.exportsPath = normalizedOutputDir
        _ = try await backend.saveSettings(updatedDraft)
        let refreshedSettings = try await backend.fetchSettings()
        settings = refreshedSettings
        settingsDraft = SettingsDraft(from: refreshedSettings)
        pdfDraft.outputDir = refreshedSettings.exportsPath
    }

    private func applyDefaultPDFOutputDirectory(from settings: SettingsResponse) {
        let current = pdfDraft.outputDir.trimmingCharacters(in: .whitespacesAndNewlines)
        if current.isEmpty {
            pdfDraft.outputDir = settings.exportsPath
        }
    }

    private func apply(diagnostics: RuntimeDiagnosticsResponse) {
        transcript = diagnostics.transcript
        response = diagnostics.response
        invocationSummary = diagnostics.invocationSummary
        actionSummary = diagnostics.actionSummary
        currentToolStatus = diagnostics.currentToolStatus
        memoryHitCount = diagnostics.memoryHitCount
        savedMemories = diagnostics.recentSaves
        recentWakeAttempts = diagnostics.recentWakeAttempts
        recentRuntimeEvents = diagnostics.recentEvents
        latencies = diagnostics.latenciesMS
        wakeGuidance = diagnostics.wakeGuidance
        wakeAttempt.score = diagnostics.lastWakeScore ?? wakeAttempt.score
        wakeAttempt.decision = diagnostics.lastWakeDecision
        wakeAttempt.acceptedCount = diagnostics.acceptedWakeAttempts
        wakeAttempt.rejectedCount = diagnostics.rejectedWakeAttempts
        wakeSignal.confidence = diagnostics.lastWakeConfidence
        wakeSignal.threshold = diagnostics.wakeScoreThreshold
        wakeSignal.acousticScore = diagnostics.lastWakeAcousticScore
        wakeSignal.dtwScore = diagnostics.lastWakeDTWScore
        wakeSignal.snrDB = diagnostics.lastWakeSNRDB
        wakeSignal.featureFrames = diagnostics.lastWakeFeatureFrames
        conversationWindowRemaining = diagnostics.conversationWindowRemaining
        cooldownRemaining = diagnostics.cooldownRemaining
        runtimeActive = diagnostics.runtimeActive
        ttsProgress.emittedChunkCount = diagnostics.emittedChunkCount
        ttsProgress.spokenChunkCount = diagnostics.spokenChunkCount
        ttsProgress.emittedCharCount = diagnostics.emittedCharCount
        ttsProgress.spokenCharCount = diagnostics.spokenCharCount
        ttsProgress.lastEmittedChunk = diagnostics.lastEmittedChunk
        ttsProgress.lastSpokenChunk = diagnostics.lastSpokenChunk
    }

    private func refreshMicrophoneStatus() async {
        voicePreflight.microphoneStatus = MicrophoneAuthorizationService.currentStatus()
        voicePreflight.backendAudioInputAvailable = dependencyHealth?.audioInputAvailable ?? false
        voicePreflight.ttsAvailable = dependencyHealth?.ttsAvailable ?? false
        voicePreflight.guidance = guidanceForMicrophoneStatus(voicePreflight.microphoneStatus)
    }

    private func ensureMicrophoneAccessForVoiceMode() async -> Bool {
        let updatedStatus = await MicrophoneAuthorizationService.requestAccessIfNeeded()
        voicePreflight.microphoneStatus = updatedStatus
        voicePreflight.backendAudioInputAvailable = dependencyHealth?.audioInputAvailable ?? false
        voicePreflight.ttsAvailable = dependencyHealth?.ttsAvailable ?? false
        voicePreflight.guidance = guidanceForMicrophoneStatus(updatedStatus)
        guard updatedStatus == "authorized" else {
            diagnosticsNote = "Microphone access is \(updatedStatus). Grant access before starting voice mode."
            connectionStatus = "Voice mode blocked until microphone access is granted."
            appendEvent("Voice mode blocked by microphone authorization: \(updatedStatus)")
            return false
        }
        return true
    }

    private func guidanceForMicrophoneStatus(_ status: String) -> String {
        switch status {
        case "authorized":
            return "Microphone access is granted. You can test continuous or turn-based voice mode."
        case "denied":
            return "Microphone access is denied. Open macOS Privacy settings and allow Lulu or the Xcode-launched app."
        case "restricted":
            return "Microphone access is restricted by macOS. Check device or policy settings."
        case "not_determined":
            return "Microphone access has not been requested yet. Start a voice mode or request access first."
        default:
            return "Microphone access state is unknown. Refresh diagnostics or retry voice startup."
        }
    }

    var voiceStartBlockedReason: String? {
        if runtimeActive {
            return "Stop the current voice runtime before starting another one."
        }
        if !backendHealthy {
            return "The local backend is unavailable."
        }
        if !voicePreflight.backendAudioInputAvailable {
            return "Audio input is unavailable."
        }
        if !voicePreflight.ttsAvailable {
            return "Text-to-speech is unavailable."
        }
        switch voicePreflight.microphoneStatus {
        case "denied":
            return "Microphone access is denied. Allow access in macOS Privacy settings first."
        case "restricted":
            return "Microphone access is restricted by macOS or device policy."
        default:
            return nil
        }
    }

    var stopRuntimeBlockedReason: String? {
        if !backendHealthy {
            return "The local backend is unavailable."
        }
        if !runtimeActive {
            return "No voice runtime is active."
        }
        return nil
    }

    var pdfSubmissionBlockedReason: String? {
        if pdfWorkflowBusy {
            return "A PDF job is already running."
        }
        if !backendHealthy {
            return "The local backend is unavailable."
        }
        if pdfDraft.pdfPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Choose a PDF file before starting a job."
        }
        let effectiveOutputDir = pdfDraft.outputDir.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? (settings?.exportsPath ?? "")
            : pdfDraft.outputDir
        if effectiveOutputDir.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return "Choose an export folder before starting a job."
        }
        if !pdfDraft.dryRun && pdfDraft.portableFormat != "none" && dependencyHealth?.ffmpegAvailable == false {
            return "Install ffmpeg or switch Portable Format to None for AIFF-only export."
        }
        return nil
    }

    var pdfStatusRefreshBlockedReason: String? {
        pdfJob == nil ? "No PDF job is available yet." : nil
    }

    var launchModeLabel: String {
        UserFacingText.launchModeLabel(settings?.pathMode)
    }

    var isPackagedMode: Bool {
        settings?.pathMode == "app_support"
    }

    var launchModeNotice: String {
        if isPackagedMode {
            return "Packaged mode uses a bundled backend runtime plus App Support and Caches for writable state. Ollama and optional ffmpeg still remain external machine prerequisites."
        }
        return "Preview mode still expects a repo checkout and a repo-local virtual environment. The packaged release path now uses a bundled backend runtime instead of the checkout .venv."
    }

    var launchModeChecklistItems: [ChecklistItem] {
        if isPackagedMode {
            return [
                ChecklistItem(
                    title: "Bundled Backend Runtime",
                    status: UserFacingText.remediationStatusLabel(ready: backendHealthy),
                    detail: backendHealthy
                        ? "The packaged app reached its bundled backend helper successfully."
                        : "If startup fails immediately, rebuild or reinstall the packaged app so Resources/backend/runtime is present.",
                    tone: backendHealthy ? .success : .danger
                ),
                ChecklistItem(
                    title: "Writable State",
                    status: "Ready",
                    detail: "Runtime state should resolve under Application Support and Caches instead of the repo checkout.",
                    tone: .success
                ),
                ChecklistItem(
                    title: "External Dependencies",
                    status: UserFacingText.remediationStatusLabel(
                        ready: dependencyHealth?.ollamaReachable == true,
                        required: true
                    ),
                    detail: dependencyHealth?.ollamaReachable == true
                        ? "Ollama remains external and is currently reachable from the packaged app."
                        : "Install or start Ollama separately before using the packaged voice runtime.",
                    tone: dependencyHealth?.ollamaReachable == true ? .success : .warning
                ),
                ChecklistItem(
                    title: "Optional PDF Export Dependencies",
                    status: UserFacingText.remediationStatusLabel(
                        ready: dependencyHealth?.ffmpegAvailable == true,
                        required: false
                    ),
                    detail: dependencyHealth?.ffmpegAvailable == true
                        ? "Portable PDF export is ready in packaged mode."
                        : "Portable PDF export still needs ffmpeg. AIFF export remains available without it.",
                    tone: dependencyHealth?.ffmpegAvailable == true ? .success : .warning
                ),
            ]
        }
        return [
            ChecklistItem(
                title: "Repo Checkout",
                status: "Required",
                detail: "Preview mode still relies on the checked-out source tree, repo-local configuration paths, and local scripts.",
                tone: .warning
            ),
            ChecklistItem(
                title: "Local Virtual Environment",
                status: "Required",
                detail: "The desktop preview still expects the repo-local .venv for backend startup instead of a bundled runtime.",
                tone: .warning
            ),
        ]
    }

    var packagedFirstRunNotice: String? {
        guard isPackagedMode else {
            return nil
        }
        if !backendHealthy {
            return "This packaged build should launch a bundled backend from the app bundle. Recover backend packaging first, then continue with Ollama, model, or microphone setup."
        }
        return "Packaged mode keeps the backend inside the app bundle, but Ollama and optional ffmpeg still require separate machine setup."
    }

    var packagedRemediationItems: [ChecklistItem] {
        guard isPackagedMode else {
            return []
        }

        let modelsReady = dependencyHealth?.chatModelAvailable == true && dependencyHealth?.embeddingModelAvailable == true
        let microphoneReady = voicePreflight.microphoneStatus == "authorized"

        return [
            ChecklistItem(
                title: "Ollama Service",
                status: UserFacingText.remediationStatusLabel(ready: dependencyHealth?.ollamaReachable == true),
                detail: dependencyHealth?.ollamaReachable == true
                    ? "Ollama is reachable from the packaged app."
                    : "Install Ollama, start `ollama serve`, then reopen Lulu or refresh diagnostics.",
                tone: dependencyHealth?.ollamaReachable == true ? .success : .warning
            ),
            ChecklistItem(
                title: "Required Models",
                status: UserFacingText.remediationStatusLabel(ready: modelsReady),
                detail: modelsReady
                    ? "The default chat and embedding models are already available."
                    : "Run `ollama pull llama3.2:3b` and `ollama pull nomic-embed-text` before using voice features.",
                tone: modelsReady ? .success : .warning
            ),
            ChecklistItem(
                title: "Microphone Permission",
                status: UserFacingText.remediationStatusLabel(ready: microphoneReady),
                detail: microphoneReady
                    ? "Microphone access is already granted for packaged voice capture."
                    : "Grant microphone access in macOS Privacy settings, then retry continuous or turn-based voice mode.",
                tone: microphoneReady ? .success : .warning
            ),
            ChecklistItem(
                title: "Portable PDF Export",
                status: UserFacingText.remediationStatusLabel(
                    ready: dependencyHealth?.ffmpegAvailable == true,
                    required: false
                ),
                detail: dependencyHealth?.ffmpegAvailable == true
                    ? "ffmpeg is available for optional WAV, M4A, or MP3 export."
                    : "Install ffmpeg only if you need portable PDF export copies. Voice runtime and AIFF export remain available without it.",
                tone: dependencyHealth?.ffmpegAvailable == true ? .success : .warning
            ),
        ]
    }

    var setupChecklistItems: [ChecklistItem] {
        [
            ChecklistItem(
                title: "Local Backend",
                status: UserFacingText.booleanStatusLabel(backendHealthy),
                detail: backendHealthy
                    ? "The desktop app can talk to Lulu's local backend service."
                    : (
                        isPackagedMode
                            ? "Recover the packaged backend runtime first. Rebuild or reinstall the app if the bundled helper is missing."
                            : "Start or recover the local backend before using voice or PDF features."
                    ),
                tone: backendHealthy ? .success : .warning
            ),
            ChecklistItem(
                title: "Microphone Access",
                status: UserFacingText.microphoneStatusLabel(voicePreflight.microphoneStatus),
                detail: voicePreflight.guidance,
                tone: voicePreflight.microphoneStatus == "authorized" ? .success :
                    (voicePreflight.microphoneStatus == "denied" || voicePreflight.microphoneStatus == "restricted" ? .danger : .warning)
            ),
            ChecklistItem(
                title: "Audio Input",
                status: UserFacingText.availabilityLabel(voicePreflight.backendAudioInputAvailable),
                detail: voicePreflight.backendAudioInputAvailable
                    ? "Audio input is ready for voice capture."
                    : "Audio input is unavailable, so voice capture cannot start yet.",
                tone: voicePreflight.backendAudioInputAvailable ? .success : .danger
            ),
            ChecklistItem(
                title: "Text-to-Speech",
                status: UserFacingText.availabilityLabel(voicePreflight.ttsAvailable),
                detail: voicePreflight.ttsAvailable
                    ? "Lulu can speak responses aloud."
                    : "Text-to-speech is unavailable, so spoken responses cannot play yet.",
                tone: voicePreflight.ttsAvailable ? .success : .danger
            ),
            ChecklistItem(
                title: "Portable PDF Export",
                status: dependencyHealth?.ffmpegAvailable == true ? "Ready" : "Optional",
                detail: dependencyHealth?.ffmpegAvailable == true
                    ? "Portable WAV, M4A, and MP3 export is available."
                    : "AIFF export still works. Install ffmpeg only if you need WAV, M4A, or MP3 copies.",
                tone: dependencyHealth?.ffmpegAvailable == true ? .success : .warning
            ),
        ]
    }

    var canRevealPDFOutput: Bool {
        let outputDir = pdfJob?.outputDir?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return !outputDir.isEmpty
    }

    var canCopyPDFManifestPath: Bool {
        let manifestPath = pdfJob?.manifestPath?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return !manifestPath.isEmpty
    }

    func revealPDFOutputInFinder() {
        guard let outputDir = pdfJob?.outputDir, !outputDir.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            pdfStatusMessage = "No output folder is available yet."
            return
        }
        NSWorkspace.shared.selectFile(nil, inFileViewerRootedAtPath: outputDir)
        appendEvent("Revealed PDF output folder in Finder.")
    }

    func copyPDFManifestPath() {
        guard let manifestPath = pdfJob?.manifestPath, !manifestPath.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            pdfStatusMessage = "No manifest path is available yet."
            return
        }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(manifestPath, forType: .string)
        pdfStatusMessage = "Copied manifest path."
        appendEvent("Copied PDF manifest path to the clipboard.")
    }

    func copyPDFOutputFolderPath() {
        guard let outputDir = pdfJob?.outputDir, !outputDir.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            pdfStatusMessage = "No output folder is available yet."
            return
        }
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(outputDir, forType: .string)
        pdfStatusMessage = "Copied output folder path."
        appendEvent("Copied PDF output folder path to the clipboard.")
    }
}
