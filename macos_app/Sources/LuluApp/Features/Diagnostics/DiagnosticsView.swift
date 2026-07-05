import SwiftUI

struct DiagnosticsView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                GroupBox("Runtime Health") {
                    VStack(alignment: .leading, spacing: 8) {
                        KeyValueRow(label: "Backend", value: model.backendHealthy ? "Healthy" : "Unavailable")
                        KeyValueRow(label: "WebSocket", value: model.websocketConnected ? "Connected" : "Disconnected")
                        KeyValueRow(label: "Mode", value: model.runtimeState?.runtimeMode ?? "unknown")
                        KeyValueRow(label: "State", value: model.runtimeState?.mode ?? "unknown")
                        KeyValueRow(label: "Status", value: model.runtimeState?.statusLine ?? "Waiting for runtime state")
                        KeyValueRow(label: "Runtime Active", value: model.runtimeActive ? "Yes" : "No")
                    }
                }

                GroupBox("Voice Preflight") {
                    VStack(alignment: .leading, spacing: 8) {
                        KeyValueRow(label: "Microphone Access", value: model.voicePreflight.microphoneStatus)
                        KeyValueRow(label: "Backend Audio Input", value: model.voicePreflight.backendAudioInputAvailable ? "Available" : "Unavailable")
                        KeyValueRow(label: "Backend TTS", value: model.voicePreflight.ttsAvailable ? "Available" : "Unavailable")
                        KeyValueRow(label: "Guidance", value: model.voicePreflight.guidance)
                    }
                }

                GroupBox("Wake") {
                    VStack(alignment: .leading, spacing: 8) {
                        KeyValueRow(label: "Guidance", value: model.wakeGuidance)
                        KeyValueRow(label: "Last Decision", value: model.wakeAttempt.decision)
                        KeyValueRow(label: "Transcript", value: model.wakeAttempt.transcript.isEmpty ? "None" : model.wakeAttempt.transcript)
                        KeyValueRow(label: "Reason", value: model.wakeAttempt.reason.isEmpty ? "None" : model.wakeAttempt.reason)
                        KeyValueRow(label: "Score", value: String(format: "%.2f", model.wakeAttempt.score))
                        KeyValueRow(label: "Accepted/Rejected", value: "\(model.wakeAttempt.acceptedCount)/\(model.wakeAttempt.rejectedCount)")
                        KeyValueRow(label: "Confidence", value: formatted(model.wakeSignal.confidence))
                        KeyValueRow(label: "Threshold", value: formatted(model.wakeSignal.threshold))
                        KeyValueRow(label: "Acoustic", value: formatted(model.wakeSignal.acousticScore))
                        KeyValueRow(label: "DTW", value: formatted(model.wakeSignal.dtwScore))
                        KeyValueRow(label: "SNR dB", value: formatted(model.wakeSignal.snrDB))
                        KeyValueRow(label: "Feature Frames", value: "\(model.wakeSignal.featureFrames)")
                    }
                }

                GroupBox("Dependencies") {
                    VStack(alignment: .leading, spacing: 8) {
                        KeyValueRow(label: "Ollama", value: model.dependencyHealth?.ollamaReachable == true ? "Reachable" : "Unavailable")
                        KeyValueRow(label: "Chat Model", value: model.dependencyHealth?.chatModelAvailable == true ? "Present" : "Missing")
                        KeyValueRow(label: "Embedding Model", value: model.dependencyHealth?.embeddingModelAvailable == true ? "Present" : "Missing")
                        KeyValueRow(label: "Audio Input", value: model.dependencyHealth?.audioInputAvailable == true ? "Available" : "Unavailable")
                        KeyValueRow(label: "TTS", value: model.dependencyHealth?.ttsAvailable == true ? "Available" : "Unavailable")
                        KeyValueRow(label: "Memory Path", value: model.dependencyHealth?.memoryPathAvailable == true ? "Ready" : "Unavailable")
                        if let issues = model.dependencyHealth?.issues, !issues.isEmpty {
                            Divider()
                            ForEach(issues, id: \.self) { issue in
                                Label(issue, systemImage: "exclamationmark.triangle")
                                    .foregroundStyle(.orange)
                            }
                        }
                    }
                }

                GroupBox("Latency") {
                    VStack(alignment: .leading, spacing: 8) {
                        if model.latencies.isEmpty {
                            Text("No latency samples yet.")
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(model.latencies.keys.sorted(), id: \.self) { key in
                                KeyValueRow(label: key, value: String(format: "%.1f ms", model.latencies[key] ?? 0))
                            }
                        }
                    }
                }

                GroupBox("TTS") {
                    VStack(alignment: .leading, spacing: 8) {
                        KeyValueRow(label: "Emitted Chunks", value: "\(model.ttsProgress.emittedChunkCount)")
                        KeyValueRow(label: "Spoken Chunks", value: "\(model.ttsProgress.spokenChunkCount)")
                        KeyValueRow(label: "Emitted Chars", value: "\(model.ttsProgress.emittedCharCount)")
                        KeyValueRow(label: "Spoken Chars", value: "\(model.ttsProgress.spokenCharCount)")
                        KeyValueRow(label: "Last Emitted", value: model.ttsProgress.lastEmittedChunk.isEmpty ? "None" : model.ttsProgress.lastEmittedChunk)
                        KeyValueRow(label: "Last Spoken", value: model.ttsProgress.lastSpokenChunk.isEmpty ? "None" : model.ttsProgress.lastSpokenChunk)
                        KeyValueRow(label: "Action Summary", value: model.actionSummary)
                        KeyValueRow(label: "Tool Status", value: model.currentToolStatus)
                    }
                }

                GroupBox("Memory And Routing") {
                    VStack(alignment: .leading, spacing: 8) {
                        KeyValueRow(label: "Invocation", value: model.invocationSummary)
                        KeyValueRow(label: "Memory Hits", value: "\(model.memoryHitCount)")
                        if model.savedMemories.isEmpty {
                            Text("No recent saved memories.")
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(model.savedMemories, id: \.self) { item in
                                Text(item)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    }
                }

                GroupBox("Recent Wake Attempts") {
                    VStack(alignment: .leading, spacing: 8) {
                        if model.recentWakeAttempts.isEmpty {
                            Text("No recent wake attempts.")
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(model.recentWakeAttempts, id: \.self) { attempt in
                                Text(attempt)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .font(.system(.body, design: .monospaced))
                            }
                        }
                    }
                }

                GroupBox("Recent Runtime Snapshot Events") {
                    VStack(alignment: .leading, spacing: 8) {
                        if model.recentRuntimeEvents.isEmpty {
                            Text("No recent runtime snapshot events.")
                                .foregroundStyle(.secondary)
                        } else {
                            ForEach(model.recentRuntimeEvents, id: \.self) { event in
                                Text(event)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    }
                }

                GroupBox("Recent Events") {
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 6) {
                            ForEach(Array(model.eventLog.enumerated()), id: \.offset) { _, line in
                                Text(line)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .font(.system(.body, design: .monospaced))
                            }
                        }
                    }
                    .frame(minHeight: 180)
                }

                GroupBox("Backend Notes") {
                    Text(model.diagnosticsNote.isEmpty ? "No backend notes yet." : model.diagnosticsNote)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
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
            return "n/a"
        }
        return String(format: "%.2f", value)
    }
}

private struct KeyValueRow: View {
    let label: String
    let value: String

    var body: some View {
        HStack {
            Text(label)
                .foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .fontWeight(.medium)
        }
    }
}
