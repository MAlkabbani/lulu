import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct PDFAudiobooksView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                GroupBox("Workflow") {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Run the offline PDF audiobook utility separately from the live assistant runtime.")
                            .font(.callout)
                        Text("Use dry run to validate extraction and PDF support before generating audio.")
                            .font(.callout)
                            .foregroundStyle(.secondary)

                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 10) {
                                statusBadges
                            }
                            VStack(alignment: .leading, spacing: 8) {
                                statusBadges
                            }
                        }
                    }
                }

                GroupBox("Source PDF") {
                    VStack(alignment: .leading, spacing: 12) {
                        TextField("PDF Path", text: $model.pdfDraft.pdfPath)
                            .textFieldStyle(.roundedBorder)
                        HStack(spacing: 10) {
                            Button("Choose PDF") {
                                if let selection = chooseFile(allowedContentTypes: [.pdf]) {
                                    model.pdfDraft.pdfPath = selection
                                }
                            }
                            if !model.pdfDraft.pdfPath.isEmpty {
                                Button("Clear") {
                                    model.pdfDraft.pdfPath = ""
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                    }
                }

                GroupBox("Metadata") {
                    VStack(alignment: .leading, spacing: 12) {
                        TextField("Title Override", text: $model.pdfDraft.title)
                        TextField("Author Override", text: $model.pdfDraft.author)
                        TextField("Genre", text: $model.pdfDraft.genre)
                    }
                    .textFieldStyle(.roundedBorder)
                }

                GroupBox("Output And Options") {
                    VStack(alignment: .leading, spacing: 12) {
                        TextField("Export Root Directory", text: $model.pdfDraft.outputDir)
                            .textFieldStyle(.roundedBorder)
                        HStack(spacing: 10) {
                            Button("Choose Export Folder") {
                                if let selection = chooseDirectory() {
                                    model.pdfDraft.outputDir = selection
                                }
                            }
                            if !model.pdfDraft.outputDir.isEmpty {
                                Button("Use Settings Default") {
                                    model.pdfDraft.outputDir = model.settings?.exportsPath ?? model.pdfDraft.outputDir
                                }
                                .buttonStyle(.bordered)
                            }
                        }

                        Picker("Chapter Splitting", selection: $model.pdfDraft.chapterSplitting) {
                            Text("Automatic").tag("auto")
                            Text("None").tag("none")
                        }

                        Picker("Portable Format", selection: $model.pdfDraft.portableFormat) {
                            Text("None").tag("none")
                            Text("WAV").tag("wav")
                            Text("M4A").tag("m4a")
                            Text("MP3").tag("mp3")
                        }
                        if model.dependencyHealth?.ffmpegAvailable == false {
                            Label(
                                "Portable WAV, M4A, and MP3 export requires ffmpeg in PATH. AIFF export still works.",
                                systemImage: "exclamationmark.triangle"
                            )
                            .foregroundStyle(.orange)
                        }

                        Toggle("Dry Run Only", isOn: $model.pdfDraft.dryRun)

                        Stepper(value: $model.pdfDraft.previewChars, in: 100...4000, step: 100) {
                            Text("Preview Characters: \(model.pdfDraft.previewChars)")
                        }

                        TextField("Pronunciation File (Optional JSON)", text: $model.pdfDraft.pronunciationFile)
                            .textFieldStyle(.roundedBorder)
                        HStack(spacing: 10) {
                            Button("Choose Pronunciation File") {
                                if let selection = chooseFile(allowedContentTypes: [.json]) {
                                    model.pdfDraft.pronunciationFile = selection
                                }
                            }
                            if !model.pdfDraft.pronunciationFile.isEmpty {
                                Button("Clear") {
                                    model.pdfDraft.pronunciationFile = ""
                                }
                                .buttonStyle(.bordered)
                            }
                        }
                    }
                }

                GroupBox("Actions") {
                    VStack(alignment: .leading, spacing: 12) {
                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 10) {
                                actionButtons
                            }
                            VStack(alignment: .leading, spacing: 10) {
                                actionButtons
                            }
                        }

                        Text(model.pdfStatusMessage.isEmpty ? "No PDF job has been started yet." : model.pdfStatusMessage)
                            .foregroundStyle(.secondary)
                    }
                }

                GroupBox("Current Job") {
                    VStack(alignment: .leading, spacing: 8) {
                        if let job = model.pdfJob {
                            PDFValueRow(label: "Job ID", value: job.jobID)
                            PDFValueRow(label: "Status", value: job.status.capitalized)
                            PDFValueRow(label: "Mode", value: job.dryRun ? "Dry Run" : "Export")
                            PDFValueRow(label: "Section Count", value: "\(job.sectionCount)")
                            PDFValueRow(label: "Output Directory", value: job.outputDir ?? "Not available yet")
                            PDFValueRow(label: "Manifest", value: job.manifestPath ?? "Not available yet")
                            PDFValueRow(label: "Error", value: job.error ?? "None")
                        } else {
                            Text("No active or completed PDF job yet.")
                                .foregroundStyle(.secondary)
                        }
                    }
                }

                GroupBox("Progress") {
                    VStack(alignment: .leading, spacing: 8) {
                        if let progress = model.pdfJob?.progress, !progress.isEmpty {
                            ForEach(Array(progress.enumerated()), id: \.offset) { _, line in
                                Text(line)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .font(.system(.body, design: .monospaced))
                            }
                        } else {
                            Text("Progress updates will appear here once a PDF job starts.")
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(20)
        }
    }

    @ViewBuilder
    private var statusBadges: some View {
        badge(text: model.backendHealthy ? "Backend Ready" : "Backend Unavailable", color: model.backendHealthy ? .green : .orange)
        badge(text: model.pdfWorkflowBusy ? "Job Running" : "Idle", color: model.pdfWorkflowBusy ? .blue : .secondary)
        badge(text: model.pdfDraft.dryRun ? "Dry Run" : "Export", color: model.pdfDraft.dryRun ? .purple : .cyan)
        badge(
            text: model.dependencyHealth?.ffmpegAvailable == false ? "ffmpeg Missing" : "ffmpeg Ready",
            color: model.dependencyHealth?.ffmpegAvailable == false ? .orange : .green
        )
    }

    @ViewBuilder
    private var actionButtons: some View {
        Button(model.pdfDraft.dryRun ? "Run Dry Run" : "Export Audiobook") {
            Task { await model.submitPDFJob() }
        }
        .buttonStyle(.borderedProminent)
        .disabled(model.pdfWorkflowBusy)

        Button("Refresh Job Status") {
            Task { await model.refreshPDFJobStatus() }
        }
        .buttonStyle(.bordered)
        .disabled(model.pdfJob == nil)

        Button("Clear Job") {
            model.resetPDFWorkflow()
        }
        .buttonStyle(.bordered)
        .disabled(model.pdfJob == nil && model.pdfStatusMessage.isEmpty)
    }

    private func badge(text: String, color: Color) -> some View {
        Text(text)
            .font(.caption)
            .padding(.horizontal, 10)
            .padding(.vertical, 4)
            .background(color.opacity(0.15), in: Capsule())
            .foregroundStyle(color)
    }

    private func chooseFile(allowedContentTypes: [UTType]) -> String? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = false
        panel.allowsMultipleSelection = false
        panel.allowedContentTypes = allowedContentTypes
        return panel.runModal() == .OK ? panel.url?.path : nil
    }

    private func chooseDirectory() -> String? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        return panel.runModal() == .OK ? panel.url?.path : nil
    }
}

private struct PDFValueRow: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label)
                .foregroundStyle(.secondary)
            Text(value)
                .frame(maxWidth: .infinity, alignment: .leading)
                .textSelection(.enabled)
        }
    }
}
