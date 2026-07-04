import SwiftUI

struct AssistantView: View {
    @ObservedObject var model: AppModel
    @FocusState private var composerFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label(model.backendHealthy ? "Backend Ready" : "Backend Unavailable", systemImage: model.backendHealthy ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(model.backendHealthy ? .green : .orange)
                Spacer()
                badge(text: model.runtimeState?.runtimeMode.capitalized ?? "Booting", color: .blue)
                badge(text: (model.runtimeState?.mode ?? "starting").replacingOccurrences(of: "_", with: " ").capitalized, color: phaseColor)
            }

            Text(model.connectionStatus)
                .font(.subheadline)
                .foregroundStyle(.secondary)

            HStack(spacing: 10) {
                if let remaining = model.conversationWindowRemaining {
                    badge(text: String(format: "Window %.1fs", remaining), color: .cyan)
                }
                if let remaining = model.cooldownRemaining {
                    badge(text: String(format: "Cooldown %.1fs", remaining), color: .orange)
                }
                badge(text: "Wake \(String(format: "%.2f", model.wakeAttempt.score))", color: model.wakeAttempt.accepted ? .green : .purple)
            }

            GroupBox("Voice Controls") {
                VStack(alignment: .leading, spacing: 12) {
                    HStack {
                        Button("Text Mode") {
                            Task { await model.startTextMode() }
                        }
                        Button("Continuous Voice") {
                            Task { await model.startContinuousVoiceMode() }
                        }
                        Button("Turn-Based Voice") {
                            Task { await model.startTurnBasedVoiceMode() }
                        }
                        Button("Stop Runtime") {
                            Task { await model.stopRuntime() }
                        }
                    }
                    .buttonStyle(.borderedProminent)

                    HStack {
                        badge(
                            text: "Mic \(model.voicePreflight.microphoneStatus.replacingOccurrences(of: "_", with: " ").capitalized)",
                            color: microphoneBadgeColor
                        )
                        badge(
                            text: model.voicePreflight.backendAudioInputAvailable ? "Backend Audio Ready" : "Backend Audio Missing",
                            color: model.voicePreflight.backendAudioInputAvailable ? .green : .red
                        )
                        badge(
                            text: model.voicePreflight.ttsAvailable ? "TTS Ready" : "TTS Missing",
                            color: model.voicePreflight.ttsAvailable ? .green : .red
                        )
                    }

                    HStack {
                        Button("Request Microphone Access") {
                            Task { await model.requestMicrophoneAccess() }
                        }
                        Button("Open Privacy Settings") {
                            model.openPrivacySettings()
                        }
                        .buttonStyle(.bordered)
                    }

                    Text(model.voicePreflight.guidance)
                        .font(.callout)
                        .foregroundStyle(.secondary)

                    Text(model.wakeGuidance)
                        .font(.callout)
                        .foregroundStyle(.secondary)

                    HStack {
                        Text("Wake decision: \(model.wakeAttempt.decision)")
                        Spacer()
                        Text("Accepted \(model.wakeAttempt.acceptedCount) / Rejected \(model.wakeAttempt.rejectedCount)")
                            .foregroundStyle(.secondary)
                    }
                    .font(.caption)
                }
            }

            GroupBox("Transcript") {
                ScrollView {
                    Text(model.transcript.isEmpty ? "Transcript events will appear here." : model.transcript)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .frame(minHeight: 120)
            }

            GroupBox("Assistant Response") {
                ScrollView {
                    Text(model.response.isEmpty ? "Streamed backend responses will appear here." : model.response)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .textSelection(.enabled)
                }
                .frame(minHeight: 180)
            }

            GroupBox("Send A Text Turn") {
                VStack(alignment: .leading, spacing: 12) {
                    TextField(
                        "Type a message to Lulu...",
                        text: $model.composeText,
                        axis: .vertical
                    )
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(4...8)
                    .font(.body)
                    .focused($composerFocused)
                    HStack {
                        Spacer()
                        Button(model.isSubmitting ? "Submitting..." : "Send To Lulu") {
                            Task {
                                await model.submitTextTurn()
                                composerFocused = true
                            }
                        }
                        .keyboardShortcut(.return, modifiers: [.command])
                        .disabled(model.composeText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.isSubmitting)
                    }
                }
            }
        }
        .padding(20)
        .onAppear {
            composerFocused = true
        }
    }

    private var phaseColor: Color {
        switch model.runtimeState?.mode {
        case "ready", "conversation_window":
            return .green
        case "cooldown":
            return .orange
        case "startup_error", "capture_error", "stt_error", "tts_error", "stream_error", "runtime_error":
            return .red
        default:
            return .secondary
        }
    }

    private func badge(text: String, color: Color) -> some View {
        Text(text)
            .font(.caption)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
    }

    private var microphoneBadgeColor: Color {
        switch model.voicePreflight.microphoneStatus {
        case "authorized":
            return .green
        case "denied", "restricted":
            return .red
        case "not_determined":
            return .orange
        default:
            return .secondary
        }
    }
}
