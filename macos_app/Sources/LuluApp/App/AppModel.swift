import AppKit
import Foundation

@MainActor
final class AppModel: ObservableObject {
    @Published var connectionStatus: String = "Starting backend..."
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
    @Published var invocationSummary = "No invocation path yet."
    @Published var memoryHitCount = 0
    @Published var actionSummary = "No action summary yet."
    @Published var currentToolStatus = "idle"
    @Published var recentWakeAttempts: [String] = []
    @Published var recentRuntimeEvents: [String] = []
    @Published var voicePreflight = VoicePreflightSnapshot()
    @Published var runtimeActive = false

    private let backend = BackendServiceCoordinator()
    private var eventTask: Task<Void, Never>?

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
            connectionStatus = "Backend healthy. Connecting runtime events..."
            await attachEvents()
            connectionStatus = "Backend healthy. Loading runtime state..."
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
            }
            connectionStatus = "Desktop shell connected to Lulu backend. Start a voice runtime when ready."
            appendEvent("Desktop shell bootstrapped successfully.")
        } catch {
            backendHealthy = false
            websocketConnected = false
            runtimeState = nil
            dependencyHealth = nil
            runtimeActive = false
            connectionStatus = "Failed to bootstrap backend."
            diagnosticsNote = error.localizedDescription
            appendEvent("Bootstrap failed: \(error.localizedDescription)")
        }
    }

    func shutdown() async {
        eventTask?.cancel()
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
                ? "Saved to \(update.configPath). Restart required."
                : "Saved."
            settings = try await backend.fetchSettings()
            if let settings {
                settingsDraft = SettingsDraft(from: settings)
            }
            appendEvent("Settings saved for desktop shell preview.")
        } catch {
            settingsSaveMessage = "Save failed: \(error.localizedDescription)"
            appendEvent("Settings save failed: \(error.localizedDescription)")
        }
    }

    private func attachEvents() async {
        eventTask?.cancel()
        eventTask = await backend.connectEvents { [weak self] envelope in
            Task { @MainActor in
                self?.apply(event: envelope)
            }
        }
        websocketConnected = true
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
            connectionStatus = "Runtime stopped."
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
            connectionStatus = "Starting \(mode) runtime..."
            runtimeState = try await backend.startRuntime(mode: mode)
            if let diagnostics = try? await backend.fetchRuntimeDiagnostics() {
                apply(diagnostics: diagnostics)
            }
            connectionStatus = successMessage
            appendEvent(successMessage)
        } catch {
            diagnosticsNote = "Runtime start failed: \(error.localizedDescription)"
            connectionStatus = "Failed to start \(mode) runtime."
            appendEvent("Runtime start failed: \(error.localizedDescription)")
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
        connectionStatus = "Backend unavailable."
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
}
