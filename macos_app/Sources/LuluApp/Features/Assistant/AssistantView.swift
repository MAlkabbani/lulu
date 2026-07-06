import Foundation
import SwiftUI

struct AssistantView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .center) {
                        statusLabel
                        Spacer(minLength: 12)
                        statusBadges
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        statusLabel
                        statusBadges
                    }
                }

                Text(model.connectionStatus)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)

                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 10) {
                        conversationBadges
                    }
                    VStack(alignment: .leading, spacing: 8) {
                        conversationBadges
                    }
                }

                GroupBox("Voice Controls") {
                    VStack(alignment: .leading, spacing: 12) {
                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 10) {
                                runtimeButtons
                            }
                            VStack(alignment: .leading, spacing: 10) {
                                runtimeButtons
                            }
                        }
                        .buttonStyle(.borderedProminent)

                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 10) {
                                voiceReadinessBadges
                            }
                            VStack(alignment: .leading, spacing: 8) {
                                voiceReadinessBadges
                            }
                        }

                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 10) {
                                microphoneButtons
                            }
                            VStack(alignment: .leading, spacing: 10) {
                                microphoneButtons
                            }
                        }

                        Text(model.voicePreflight.guidance)
                            .font(.callout)
                            .foregroundStyle(.secondary)

                        Text(model.wakeGuidance)
                            .font(.callout)
                            .foregroundStyle(.secondary)

                        ViewThatFits(in: .horizontal) {
                            HStack {
                                Text("Wake decision: \(model.wakeAttempt.decision)")
                                Spacer(minLength: 12)
                                Text("Accepted \(model.wakeAttempt.acceptedCount) / Rejected \(model.wakeAttempt.rejectedCount)")
                                    .foregroundStyle(.secondary)
                            }
                            VStack(alignment: .leading, spacing: 4) {
                                Text("Wake decision: \(model.wakeAttempt.decision)")
                                Text("Accepted \(model.wakeAttempt.acceptedCount) / Rejected \(model.wakeAttempt.rejectedCount)")
                                    .foregroundStyle(.secondary)
                            }
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
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(20)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
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

    @ViewBuilder
    private var statusLabel: some View {
        Label(model.backendHealthy ? "Backend Ready" : "Backend Unavailable", systemImage: model.backendHealthy ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
            .foregroundStyle(model.backendHealthy ? .green : .orange)
    }

    @ViewBuilder
    private var statusBadges: some View {
        badge(text: model.runtimeState?.runtimeMode.capitalized ?? "Booting", color: .blue)
        badge(text: (model.runtimeState?.mode ?? "starting").replacingOccurrences(of: "_", with: " ").capitalized, color: phaseColor)
    }

    @ViewBuilder
    private var conversationBadges: some View {
        if let remaining = model.conversationWindowRemaining {
            badge(text: String(format: "Window %.1fs", remaining), color: .cyan)
        }
        if let remaining = model.cooldownRemaining {
            badge(text: String(format: "Cooldown %.1fs", remaining), color: .orange)
        }
        badge(text: "Wake \(String(format: "%.2f", model.wakeAttempt.score))", color: model.wakeAttempt.accepted ? .green : .purple)
    }

    @ViewBuilder
    private var runtimeButtons: some View {
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

    @ViewBuilder
    private var voiceReadinessBadges: some View {
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

    @ViewBuilder
    private var microphoneButtons: some View {
        Button("Request Microphone Access") {
            Task { await model.requestMicrophoneAccess() }
        }
        Button("Open Privacy Settings") {
            model.openPrivacySettings()
        }
        .buttonStyle(.bordered)
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
