import Foundation
import SwiftUI

struct AssistantView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                ViewThatFits(in: .horizontal) {
                    HStack(alignment: .center) {
                        backendStatus
                        Spacer(minLength: 12)
                        statusBadges
                    }
                    VStack(alignment: .leading, spacing: 10) {
                        backendStatus
                        statusBadges
                    }
                }

                InlineNotice(
                    model.connectionStatus,
                    tone: model.backendHealthy ? .info : .warning
                )

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
                        if let voiceStartBlockedReason = model.voiceStartBlockedReason {
                            InlineNotice(voiceStartBlockedReason, tone: .warning)
                        }

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

                        InlineNotice(model.voicePreflight.guidance, tone: .info, systemImage: "mic.fill")
                        InlineNotice(model.wakeGuidance, tone: .neutral, systemImage: "waveform")

                        GroupBox("Wake Readiness") {
                            VStack(alignment: .leading, spacing: 8) {
                                LabeledValueRow(label: "Latest Decision", value: model.wakeAttempt.decision)
                                LabeledValueRow(
                                    label: "Accepted / Rejected",
                                    value: "\(model.wakeAttempt.acceptedCount) / \(model.wakeAttempt.rejectedCount)"
                                )
                            }
                        }
                    }
                }

                GroupBox("Setup Checklist") {
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Use this checklist to verify the app is ready before you start a voice session or PDF export.")
                            .foregroundStyle(.secondary)
                        ForEach(Array(model.setupChecklistItems.enumerated()), id: \.offset) { _, item in
                            ChecklistRow(item: item)
                        }
                    }
                }

                GroupBox("Transcript") {
                    ScrollView {
                        if model.transcript.isEmpty {
                            EmptyStateView(text: "Transcript activity appears here once Lulu hears speech.")
                        } else {
                            Text(model.transcript)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .textSelection(.enabled)
                        }
                    }
                    .frame(minHeight: 120)
                }

                GroupBox("Assistant Reply") {
                    ScrollView {
                        if model.response.isEmpty {
                            EmptyStateView(text: "Lulu's latest spoken reply appears here.")
                        } else {
                            Text(model.response)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .textSelection(.enabled)
                        }
                    }
                    .frame(minHeight: 180)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(20)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    private var phaseTone: StatusTone {
        switch model.runtimeState?.mode {
        case "ready", "conversation_window":
            return .success
        case "passive_listening", "listening", "thinking", "transcribing", "streaming", "speaking":
            return .info
        case "cooldown":
            return .warning
        case "startup_error", "capture_error", "stt_error", "tts_error", "stream_error", "runtime_error":
            return .danger
        default:
            return .neutral
        }
    }

    private var backendStatus: some View {
        Label(
            model.backendHealthy ? UserFacingText.backendReady : UserFacingText.backendUnavailable,
            systemImage: model.backendHealthy ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
        )
        .foregroundStyle(model.backendHealthy ? Color.green : Color.orange)
        .accessibilityElement(children: .combine)
    }

    private var statusBadges: some View {
        HStack(spacing: 8) {
            StatusBadge(
                text: UserFacingText.runtimeModeLabel(model.runtimeState?.runtimeMode),
                tone: .info
            )
            StatusBadge(
                text: UserFacingText.runtimePhaseLabel(model.runtimeState?.mode),
                tone: phaseTone
            )
        }
    }

    private var conversationBadges: some View {
        HStack(spacing: 8) {
            if let remaining = model.conversationWindowRemaining {
                StatusBadge(text: String(format: "Window %.1fs", remaining), tone: .info)
            }
            if let remaining = model.cooldownRemaining {
                StatusBadge(text: String(format: "Cooldown %.1fs", remaining), tone: .warning)
            }
            StatusBadge(
                text: "Wake Score \(String(format: "%.2f", model.wakeAttempt.score))",
                tone: model.wakeAttempt.accepted ? .success : .neutral
            )
        }
    }

    private var runtimeButtons: some View {
        Group {
            Button("Continuous Voice") {
                Task { await model.startContinuousVoiceMode() }
            }
            .disabled(model.voiceStartBlockedReason != nil)
            .help(model.voiceStartBlockedReason ?? "Start continuous listening.")
            .accessibilityLabel("Start Continuous Voice")
            .accessibilityHint(model.voiceStartBlockedReason ?? "Start continuous voice listening.")

            Button("Turn-Based Voice") {
                Task { await model.startTurnBasedVoiceMode() }
            }
            .disabled(model.voiceStartBlockedReason != nil)
            .help(model.voiceStartBlockedReason ?? "Start one-turn voice capture.")
            .accessibilityLabel("Start Turn-Based Voice")
            .accessibilityHint(model.voiceStartBlockedReason ?? "Start one turn of voice capture.")

            Button("Stop Runtime") {
                Task { await model.stopRuntime() }
            }
            .disabled(model.stopRuntimeBlockedReason != nil)
            .help(model.stopRuntimeBlockedReason ?? "Stop the current voice runtime.")
            .accessibilityLabel("Stop Voice Runtime")
            .accessibilityHint(model.stopRuntimeBlockedReason ?? "Stop the active voice runtime.")
        }
    }

    private var voiceReadinessBadges: some View {
        HStack(spacing: 8) {
            StatusBadge(
                text: "Microphone \(UserFacingText.microphoneStatusLabel(model.voicePreflight.microphoneStatus))",
                tone: microphoneTone
            )
            StatusBadge(
                text: "Audio Input \(UserFacingText.availabilityLabel(model.voicePreflight.backendAudioInputAvailable))",
                tone: model.voicePreflight.backendAudioInputAvailable ? .success : .danger
            )
            StatusBadge(
                text: "Text-to-Speech \(UserFacingText.availabilityLabel(model.voicePreflight.ttsAvailable))",
                tone: model.voicePreflight.ttsAvailable ? .success : .danger
            )
        }
    }

    private var microphoneButtons: some View {
        Group {
            Button("Request Microphone Access") {
                Task { await model.requestMicrophoneAccess() }
            }
            .help("Prompt for microphone permission if macOS has not asked yet.")
            .accessibilityLabel("Request Microphone Access")
            .accessibilityHint("Ask macOS for microphone permission.")

            Button("Open Privacy Settings") {
                model.openPrivacySettings()
            }
            .help("Open the macOS Microphone privacy settings.")
            .buttonStyle(.bordered)
            .accessibilityLabel("Open Privacy Settings")
            .accessibilityHint("Open macOS Privacy settings for microphone access.")
        }
    }

    private var microphoneTone: StatusTone {
        switch model.voicePreflight.microphoneStatus {
        case "authorized":
            return .success
        case "denied", "restricted":
            return .danger
        case "not_determined":
            return .warning
        default:
            return .neutral
        }
    }
}
