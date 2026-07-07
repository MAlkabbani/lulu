import SwiftUI

struct SettingsView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        Form {
            Section("Models") {
                LabeledTextFieldRow(
                    label: "Chat Model",
                    prompt: "llama3.2:3b",
                    text: $model.settingsDraft.chatModel
                )
                LabeledTextFieldRow(
                    label: "Embedding Model",
                    prompt: "nomic-embed-text",
                    text: $model.settingsDraft.embeddingModel
                )
                LabeledTextFieldRow(
                    label: "Whisper Model",
                    prompt: "mlx-community/whisper-tiny",
                    text: $model.settingsDraft.whisperModel
                )
                LabeledTextFieldRow(
                    label: "Whisper Language",
                    prompt: "en",
                    text: $model.settingsDraft.whisperLanguage
                )
            }

            Section("Voice") {
                LabeledTextFieldRow(
                    label: "Wake Phrase",
                    prompt: "hey lulu",
                    text: $model.settingsDraft.wakePhrase
                )
                Toggle("Practical Voice Mode", isOn: $model.settingsDraft.practicalVoiceMode)
            }

            Section("Storage") {
                LabeledValueRow(label: "Config Path", value: model.settings?.configPath ?? UserFacingText.notLoadedYet)
                LabeledValueRow(label: "Path Mode", value: model.settings?.pathMode ?? UserFacingText.notLoadedYet)
                LabeledValueRow(label: "Chroma", value: model.settings?.chromaPath ?? UserFacingText.notLoadedYet)
                LabeledValueRow(label: "Logs", value: model.settings?.logsPath ?? UserFacingText.notLoadedYet)
                LabeledValueRow(label: "Exports", value: model.settings?.exportsPath ?? UserFacingText.notLoadedYet)
            }

            Section {
                Button("Save Settings") {
                    Task {
                        await model.saveSettings()
                    }
                }
                .help("Save settings to Lulu's local configuration file.")
                if !model.settingsSaveMessage.isEmpty {
                    InlineNotice(model.settingsSaveMessage, tone: .info)
                }
            }
        }
        .padding(20)
        .formStyle(.grouped)
    }
}
