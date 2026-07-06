import Foundation
import SwiftUI

struct AssistantView: View {
    @ObservedObject var model: AppModel
    @FocusState private var composerFocused: Bool

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

                GroupBox("Send A Text Turn") {
                    VStack(alignment: .leading, spacing: 12) {
                        TextEditor(text: $model.composeText)
                            .focused($composerFocused)
                            .font(.body)
                            .scrollContentBackground(.hidden)
                            .padding(8)
                            .frame(minHeight: 120, alignment: .topLeading)
                            .background(Color(nsColor: .textBackgroundColor))
                            .overlay(
                                RoundedRectangle(cornerRadius: 8)
                                    .stroke(Color.secondary.opacity(0.25), lineWidth: 1)
                            )
                            .overlay(alignment: .topLeading) {
                                if model.composeText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                    Text("Type a message to Lulu...")
                                        .foregroundStyle(.secondary)
                                        .padding(.horizontal, 14)
                                        .padding(.vertical, 16)
                                        .allowsHitTesting(false)
                                }
                            }
                            .onTapGesture {
                                debugReport(
                                    hypothesisId: "A",
                                    location: "AssistantView.swift:126",
                                    message: "composer tapped",
                                    data: [
                                        "textLength": model.composeText.count,
                                        "focused": composerFocused,
                                    ]
                                )
                            }
                        HStack {
                            Spacer()
                            Button(model.isSubmitting ? "Submitting..." : "Send To Lulu") {
                                debugReport(
                                    hypothesisId: "B",
                                    location: "AssistantView.swift:138",
                                    message: "send button tapped",
                                    data: [
                                        "textLength": model.composeText.count,
                                        "isSubmitting": model.isSubmitting,
                                        "focused": composerFocused,
                                    ]
                                )
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
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(20)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .onAppear {
            composerFocused = true
            debugReport(
                hypothesisId: "A",
                location: "AssistantView.swift:157",
                message: "assistant view appeared",
                data: [
                    "textLength": model.composeText.count,
                    "focused": composerFocused,
                ]
            )
        }
        .onChange(of: composerFocused) { _, focused in
            debugReport(
                hypothesisId: "A",
                location: "AssistantView.swift:167",
                message: "composer focus changed",
                data: [
                    "focused": focused,
                    "textLength": model.composeText.count,
                ]
            )
        }
        .onChange(of: model.composeText) { _, text in
            debugReport(
                hypothesisId: "B",
                location: "AssistantView.swift:177",
                message: "composer text changed",
                data: [
                    "textLength": text.count,
                    "preview": String(text.prefix(40)),
                    "focused": composerFocused,
                ]
            )
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

    // #region debug-point A:desktop-input-wake
    private func debugReport(
        hypothesisId: String,
        location: String,
        message: String,
        data: [String: Any]
    ) {
        guard let url = URL(string: "http://127.0.0.1:7777/event") else {
            return
        }
        let payload: [String: Any] = [
            "sessionId": "desktop-input-wake",
            "runId": "pre-fix",
            "hypothesisId": hypothesisId,
            "location": location,
            "msg": "[DEBUG] \(message)",
            "data": data,
        ]
        guard let body = try? JSONSerialization.data(withJSONObject: payload) else {
            return
        }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        URLSession.shared.dataTask(with: request).resume()
    }
    // #endregion
}
