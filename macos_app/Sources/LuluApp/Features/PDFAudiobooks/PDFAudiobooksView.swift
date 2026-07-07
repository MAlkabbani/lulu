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
                        InlineNotice(
                            "Run the PDF audiobook workflow separately from the live assistant runtime.",
                            tone: .info,
                            systemImage: "book.closed.fill"
                        )
                        Text("Use Dry Run first to validate extraction and PDF support before generating audio.")
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
                        LabeledTextFieldRow(
                            label: "PDF File",
                            prompt: "/path/to/book.pdf",
                            text: $model.pdfDraft.pdfPath,
                            helpText: "Choose a local text-based PDF."
                        )
                        HStack(spacing: 10) {
                            Button("Choose PDF") {
                                if let selection = chooseFile(allowedContentTypes: [.pdf]) {
                                    model.pdfDraft.pdfPath = selection
                                }
                            }
                            .help("Open a PDF file chooser.")
                            if !model.pdfDraft.pdfPath.isEmpty {
                                Button("Clear") {
                                    model.pdfDraft.pdfPath = ""
                                }
                                .buttonStyle(.bordered)
                                .help("Clear the selected PDF path.")
                            }
                        }
                    }
                }

                GroupBox("Metadata") {
                    VStack(alignment: .leading, spacing: 12) {
                        LabeledTextFieldRow(
                            label: "Title Override",
                            prompt: "Leave blank to keep the PDF title",
                            text: $model.pdfDraft.title,
                            helpText: "Optional. Overrides the detected book title."
                        )
                        LabeledTextFieldRow(
                            label: "Author Override",
                            prompt: "Leave blank to keep the PDF author",
                            text: $model.pdfDraft.author,
                            helpText: "Optional. Overrides the detected author."
                        )
                        LabeledTextFieldRow(
                            label: "Genre",
                            prompt: "Optional genre",
                            text: $model.pdfDraft.genre,
                            helpText: "Optional metadata for the output manifest."
                        )
                    }
                }

                GroupBox("Output And Options") {
                    VStack(alignment: .leading, spacing: 12) {
                        LabeledTextFieldRow(
                            label: "Export Folder",
                            prompt: "/path/to/exports",
                            text: $model.pdfDraft.outputDir,
                            helpText: "Choose where Lulu writes the manifest, cleaned text, and audio output."
                        )
                        HStack(spacing: 10) {
                            Button("Choose Export Folder") {
                                if let selection = chooseDirectory() {
                                    model.pdfDraft.outputDir = selection
                                }
                            }
                            .help("Choose the folder where Lulu should write output files.")
                            if !model.pdfDraft.outputDir.isEmpty {
                                Button("Use Settings Default") {
                                    model.pdfDraft.outputDir = model.settings?.exportsPath ?? model.pdfDraft.outputDir
                                }
                                .buttonStyle(.bordered)
                                .help("Use the default export folder from Settings.")
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
                        .help("Choose None to keep AIFF-only output. WAV, M4A, and MP3 require ffmpeg.")
                        if model.dependencyHealth?.ffmpegAvailable == false {
                            InlineNotice(
                                "Portable WAV, M4A, and MP3 export requires ffmpeg in PATH. AIFF export still works.",
                                tone: .warning
                            )
                        }

                        Toggle("Dry Run Only", isOn: $model.pdfDraft.dryRun)
                            .help("Validate extraction and sectioning without rendering audio.")

                        Stepper(value: $model.pdfDraft.previewChars, in: 100...4000, step: 100) {
                            Text("Preview Characters: \(model.pdfDraft.previewChars)")
                        }

                        LabeledTextFieldRow(
                            label: "Pronunciation File",
                            prompt: "/path/to/pronunciations.json",
                            text: $model.pdfDraft.pronunciationFile,
                            helpText: "Optional JSON file for pronunciation overrides."
                        )
                        HStack(spacing: 10) {
                            Button("Choose Pronunciation File") {
                                if let selection = chooseFile(allowedContentTypes: [.json]) {
                                    model.pdfDraft.pronunciationFile = selection
                                }
                            }
                            .help("Choose a pronunciation override file.")
                            if !model.pdfDraft.pronunciationFile.isEmpty {
                                Button("Clear") {
                                    model.pdfDraft.pronunciationFile = ""
                                }
                                .buttonStyle(.bordered)
                                .help("Clear the pronunciation file path.")
                            }
                        }
                    }
                }

                GroupBox("Actions") {
                    VStack(alignment: .leading, spacing: 12) {
                        if let submissionReason = model.pdfSubmissionBlockedReason {
                            InlineNotice(submissionReason, tone: .warning)
                        }

                        ViewThatFits(in: .horizontal) {
                            HStack(spacing: 10) {
                                actionButtons
                            }
                            VStack(alignment: .leading, spacing: 10) {
                                actionButtons
                            }
                        }

                        InlineNotice(
                            model.pdfStatusMessage.isEmpty ? UserFacingText.noActivityYet : model.pdfStatusMessage,
                            tone: .neutral
                        )
                    }
                }

                GroupBox("Current Job") {
                    VStack(alignment: .leading, spacing: 8) {
                        if let job = model.pdfJob {
                            LabeledValueRow(label: "Job ID", value: job.jobID)
                            LabeledValueRow(label: "Status", value: UserFacingText.pdfJobStatusLabel(job.status))
                            LabeledValueRow(label: "Mode", value: UserFacingText.pdfWorkflowModeLabel(dryRun: job.dryRun))
                            LabeledValueRow(label: "Section Count", value: "\(job.sectionCount)")
                            LabeledValueRow(
                                label: "Output Folder",
                                value: UserFacingText.textOrFallback(job.outputDir)
                            )
                            LabeledValueRow(
                                label: "Manifest",
                                value: UserFacingText.textOrFallback(job.manifestPath)
                            )
                            LabeledValueRow(
                                label: "Error",
                                value: UserFacingText.textOrFallback(job.error)
                            )
                            HStack(spacing: 10) {
                                Button("Reveal Output Folder") {
                                    model.revealPDFOutputInFinder()
                                }
                                .buttonStyle(.bordered)
                                .disabled(!model.canRevealPDFOutput)
                                .help(model.canRevealPDFOutput ? "Open the current output folder in Finder." : "No output folder is available yet.")

                                Button("Copy Output Folder Path") {
                                    model.copyPDFOutputFolderPath()
                                }
                                .buttonStyle(.bordered)
                                .disabled(!model.canRevealPDFOutput)
                                .help(model.canRevealPDFOutput ? "Copy the current output folder path." : "No output folder is available yet.")

                                Button("Copy Manifest Path") {
                                    model.copyPDFManifestPath()
                                }
                                .buttonStyle(.bordered)
                                .disabled(!model.canCopyPDFManifestPath)
                                .help(model.canCopyPDFManifestPath ? "Copy the current manifest path." : "No manifest path is available yet.")
                            }
                        } else {
                            EmptyStateView(text: UserFacingText.noActivityYet)
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
                            EmptyStateView(text: "Progress updates appear here once a PDF job starts.")
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
        StatusBadge(
            text: model.backendHealthy ? UserFacingText.backendReady : UserFacingText.backendUnavailable,
            tone: model.backendHealthy ? .success : .warning
        )
        StatusBadge(text: model.pdfWorkflowBusy ? "Running" : "Idle", tone: model.pdfWorkflowBusy ? .info : .neutral)
        StatusBadge(text: UserFacingText.pdfWorkflowModeLabel(dryRun: model.pdfDraft.dryRun), tone: model.pdfDraft.dryRun ? .warning : .info)
        StatusBadge(
            text: model.dependencyHealth?.ffmpegAvailable == false ? "Optional Dependency Unavailable" : "Portable Export Ready",
            tone: model.dependencyHealth?.ffmpegAvailable == false ? .warning : .success
        )
    }

    @ViewBuilder
    private var actionButtons: some View {
        Button(model.pdfDraft.dryRun ? "Run Dry Run" : "Export Audiobook") {
            Task { await model.submitPDFJob() }
        }
        .buttonStyle(.borderedProminent)
        .disabled(model.pdfSubmissionBlockedReason != nil)
        .help(model.pdfSubmissionBlockedReason ?? "Start the current PDF workflow.")

        Button("Refresh Job Status") {
            Task { await model.refreshPDFJobStatus() }
        }
        .buttonStyle(.bordered)
        .disabled(model.pdfStatusRefreshBlockedReason != nil)
        .help(model.pdfStatusRefreshBlockedReason ?? "Refresh the current job status.")

        Button("Clear Job") {
            model.resetPDFWorkflow()
        }
        .buttonStyle(.bordered)
        .disabled(model.pdfJob == nil && model.pdfStatusMessage.isEmpty)
        .help("Clear the current PDF workflow state.")
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
