import SwiftUI

struct DiagnosticsView: View {
    @ObservedObject var model: AppModel
    @State private var showAdvancedWakeMetrics = false
    @State private var eventFilter = "All"

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                GroupBox("Overview") {
                    VStack(alignment: .leading, spacing: 8) {
                        InlineNotice(
                            "Use Overview for quick health checks. Expand the advanced wake metrics and filter the event views when you need deeper troubleshooting.",
                            tone: .info
                        )
                        LabeledValueRow(
                            label: "Backend",
                            value: model.backendHealthy ? UserFacingText.backendReady : UserFacingText.backendUnavailable
                        )
                        LabeledValueRow(
                            label: "Event Stream",
                            value: model.websocketConnected ? "Connected" : "Disconnected"
                        )
                        LabeledValueRow(
                            label: "Voice Mode",
                            value: UserFacingText.runtimeModeLabel(model.runtimeState?.runtimeMode)
                        )
                        LabeledValueRow(
                            label: "Runtime Phase",
                            value: UserFacingText.runtimePhaseLabel(model.runtimeState?.mode)
                        )
                        LabeledValueRow(
                            label: "Status",
                            value: UserFacingText.textOrFallback(
                                model.runtimeState?.statusLine,
                                fallback: "Waiting for runtime state."
                            )
                        )
                        LabeledValueRow(label: "Runtime Active", value: model.runtimeActive ? "Yes" : "No")
                    }
                }

                GroupBox("Voice Readiness") {
                    VStack(alignment: .leading, spacing: 8) {
                        LabeledValueRow(
                            label: "Microphone Access",
                            value: UserFacingText.microphoneStatusLabel(model.voicePreflight.microphoneStatus)
                        )
                        LabeledValueRow(
                            label: "Audio Input",
                            value: UserFacingText.availabilityLabel(model.voicePreflight.backendAudioInputAvailable)
                        )
                        LabeledValueRow(
                            label: "Text-to-Speech",
                            value: UserFacingText.availabilityLabel(model.voicePreflight.ttsAvailable)
                        )
                        InlineNotice(model.voicePreflight.guidance, tone: .info, systemImage: "mic.fill")
                    }
                }

                GroupBox("Dependencies") {
                    VStack(alignment: .leading, spacing: 8) {
                        LabeledValueRow(
                            label: "Ollama",
                            value: model.dependencyHealth?.ollamaReachable == true ? "Available" : "Unavailable"
                        )
                        LabeledValueRow(
                            label: "Chat Model",
                            value: UserFacingText.availabilityLabel(model.dependencyHealth?.chatModelAvailable)
                        )
                        LabeledValueRow(
                            label: "Embedding Model",
                            value: UserFacingText.availabilityLabel(model.dependencyHealth?.embeddingModelAvailable)
                        )
                        LabeledValueRow(
                            label: "Audio Input",
                            value: UserFacingText.availabilityLabel(model.dependencyHealth?.audioInputAvailable)
                        )
                        LabeledValueRow(
                            label: "Text-to-Speech",
                            value: UserFacingText.availabilityLabel(model.dependencyHealth?.ttsAvailable)
                        )
                        LabeledValueRow(
                            label: "ffmpeg",
                            value: UserFacingText.availabilityLabel(model.dependencyHealth?.ffmpegAvailable)
                        )
                        LabeledValueRow(
                            label: "Memory Path",
                            value: UserFacingText.availabilityLabel(model.dependencyHealth?.memoryPathAvailable)
                        )
                        if let issues = model.dependencyHealth?.issues, !issues.isEmpty {
                            ForEach(issues, id: \.self) { issue in
                                InlineNotice(issue, tone: .warning)
                            }
                        }
                    }
                }

                GroupBox("Wake") {
                    VStack(alignment: .leading, spacing: 8) {
                        InlineNotice(model.wakeGuidance, tone: .neutral, systemImage: "waveform")
                        LabeledValueRow(label: "Latest Decision", value: model.wakeAttempt.decision)
                        LabeledValueRow(
                            label: "Transcript",
                            value: UserFacingText.textOrFallback(model.wakeAttempt.transcript)
                        )
                        LabeledValueRow(
                            label: "Reason",
                            value: UserFacingText.textOrFallback(model.wakeAttempt.reason)
                        )
                        LabeledValueRow(label: "Score", value: String(format: "%.2f", model.wakeAttempt.score))
                        LabeledValueRow(
                            label: "Accepted / Rejected",
                            value: "\(model.wakeAttempt.acceptedCount) / \(model.wakeAttempt.rejectedCount)"
                        )
                        DisclosureGroup("Advanced Wake Metrics", isExpanded: $showAdvancedWakeMetrics) {
                            VStack(alignment: .leading, spacing: 8) {
                                Text("These metrics help debug wake quality and audio conditions.")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                LabeledValueRow(label: "Confidence", value: formatted(model.wakeSignal.confidence))
                                LabeledValueRow(label: "Threshold", value: formatted(model.wakeSignal.threshold))
                                LabeledValueRow(label: "Acoustic Score", value: formatted(model.wakeSignal.acousticScore))
                                LabeledValueRow(label: "DTW Score", value: formatted(model.wakeSignal.dtwScore))
                                LabeledValueRow(label: "Signal-to-Noise Ratio", value: formatted(model.wakeSignal.snrDB))
                                LabeledValueRow(label: "Feature Frames", value: "\(model.wakeSignal.featureFrames)")
                            }
                            .padding(.top, 4)
                        }
                    }
                }

                GroupBox("Latency") {
                    VStack(alignment: .leading, spacing: 8) {
                        if model.latencies.isEmpty {
                            EmptyStateView(text: "No data yet.")
                        } else {
                            ForEach(model.latencies.keys.sorted(), id: \.self) { key in
                                LabeledValueRow(label: key, value: String(format: "%.1f ms", model.latencies[key] ?? 0))
                            }
                        }
                    }
                }

                GroupBox("Speech And Actions") {
                    VStack(alignment: .leading, spacing: 8) {
                        LabeledValueRow(label: "Emitted Chunks", value: "\(model.ttsProgress.emittedChunkCount)")
                        LabeledValueRow(label: "Spoken Chunks", value: "\(model.ttsProgress.spokenChunkCount)")
                        LabeledValueRow(label: "Emitted Characters", value: "\(model.ttsProgress.emittedCharCount)")
                        LabeledValueRow(label: "Spoken Characters", value: "\(model.ttsProgress.spokenCharCount)")
                        LabeledValueRow(
                            label: "Last Emitted Chunk",
                            value: UserFacingText.textOrFallback(model.ttsProgress.lastEmittedChunk)
                        )
                        LabeledValueRow(
                            label: "Last Spoken Chunk",
                            value: UserFacingText.textOrFallback(model.ttsProgress.lastSpokenChunk)
                        )
                        LabeledValueRow(label: "Action Summary", value: model.actionSummary)
                        LabeledValueRow(label: "Action Status", value: model.currentToolStatus)
                    }
                }

                GroupBox("Memory And Routing") {
                    VStack(alignment: .leading, spacing: 8) {
                        LabeledValueRow(label: "Invocation", value: model.invocationSummary)
                        LabeledValueRow(label: "Memory Hits", value: "\(model.memoryHitCount)")
                        if model.savedMemories.isEmpty {
                            EmptyStateView(text: "No activity yet.")
                        } else {
                            ForEach(model.savedMemories, id: \.self) { item in
                                Text(item)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .textSelection(.enabled)
                            }
                        }
                    }
                }

                GroupBox("Recent Wake Attempts") {
                    VStack(alignment: .leading, spacing: 8) {
                        if model.recentWakeAttempts.isEmpty {
                            EmptyStateView(text: "No activity yet.")
                        } else {
                            ForEach(model.recentWakeAttempts, id: \.self) { attempt in
                                Text(attempt)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .font(.system(.body, design: .monospaced))
                            }
                        }
                    }
                }

                GroupBox("Recent Runtime Events") {
                    VStack(alignment: .leading, spacing: 8) {
                        Picker("Event Filter", selection: $eventFilter) {
                            Text("All").tag("All")
                            Text("Warnings").tag("Warnings")
                            Text("Actions").tag("Actions")
                        }
                        .pickerStyle(.segmented)
                        .accessibilityLabel("Event Filter")
                        .accessibilityHint("Filter diagnostics events by all activity, warnings, or actions.")
                        Text("Filter runtime and event-log entries to focus on warnings or action flow when troubleshooting.")
                            .font(.caption)
                            .foregroundStyle(.secondary)

                        if filteredRuntimeEvents.isEmpty {
                            EmptyStateView(text: "No activity yet.")
                        } else {
                            ForEach(filteredRuntimeEvents, id: \.self) { event in
                                Text(event)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    }
                }

                GroupBox("Event Log") {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 6) {
                            if filteredEventLog.isEmpty {
                                EmptyStateView(text: "No activity yet.")
                            } else {
                                ForEach(Array(filteredEventLog.enumerated()), id: \.offset) { _, line in
                                    Text(line)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        .font(.system(.body, design: .monospaced))
                                }
                            }
                        }
                    }
                    .frame(minHeight: 180)
                }

                GroupBox("Backend Notes") {
                    if model.diagnosticsNote.isEmpty {
                        EmptyStateView(text: "No data yet.")
                    } else {
                        Text(model.diagnosticsNote)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .textSelection(.enabled)
                    }
                }

                HStack {
                    Spacer()
                    Button("Refresh Diagnostics") {
                        Task {
                            await model.refreshDiagnostics()
                        }
                    }
                }
            }
            .padding(20)
        }
    }

    private func formatted(_ value: Double?) -> String {
        guard let value else {
            return UserFacingText.noDataYet
        }
        return String(format: "%.2f", value)
    }

    private var filteredRuntimeEvents: [String] {
        filterEvents(model.recentRuntimeEvents)
    }

    private var filteredEventLog: [String] {
        filterEvents(model.eventLog)
    }

    private func filterEvents(_ events: [String]) -> [String] {
        switch eventFilter {
        case "Warnings":
            return events.filter { event in
                let lower = event.lowercased()
                return lower.contains("failed") || lower.contains("error") || lower.contains("blocked")
            }
        case "Actions":
            return events.filter { event in
                let lower = event.lowercased()
                return lower.contains("action") || lower.contains("saved") || lower.contains("memory")
            }
        default:
            return events
        }
    }
}
