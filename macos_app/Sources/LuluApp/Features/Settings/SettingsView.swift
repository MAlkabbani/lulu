import SwiftUI

struct SettingsView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        Form {
            Section("Models") {
                TextField("Chat Model", text: $model.settingsDraft.chatModel)
                TextField("Embedding Model", text: $model.settingsDraft.embeddingModel)
                TextField("Whisper Model", text: $model.settingsDraft.whisperModel)
                TextField("Whisper Language", text: $model.settingsDraft.whisperLanguage)
            }

            Section("Voice") {
                TextField("Wake Phrase", text: $model.settingsDraft.wakePhrase)
                Toggle("Practical Voice Mode", isOn: $model.settingsDraft.practicalVoiceMode)
            }

            Section("Storage") {
                LabeledContent("Config Path", value: model.settings?.configPath ?? "Not loaded")
                LabeledContent("Path Mode", value: model.settings?.pathMode ?? "Not loaded")
                LabeledContent("Chroma", value: model.settings?.chromaPath ?? "Not loaded")
                LabeledContent("Logs", value: model.settings?.logsPath ?? "Not loaded")
                LabeledContent("Exports", value: model.settings?.exportsPath ?? "Not loaded")
            }

            Section {
                Button("Save Settings") {
                    Task {
                        await model.saveSettings()
                    }
                }
                if !model.settingsSaveMessage.isEmpty {
                    Text(model.settingsSaveMessage)
                        .foregroundStyle(.secondary)
                }
            }
        }
        .padding(20)
        .formStyle(.grouped)
    }
}

