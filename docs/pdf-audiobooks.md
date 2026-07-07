# PDF Audiobooks

Lulu now includes a repo-local PDF-to-audiobook workflow for text-based PDFs.

The backend service also exposes PDF job endpoints, and the native macOS shell now includes an initial `PDF Audiobooks` surface that drives those same backend jobs as a separate desktop utility.

## Why It Lives Here

- it stays local-first and uses only local file access plus macOS `say`
- it does not modify the interactive assistant runtime in `main.py`
- it follows the existing repo pattern of small utility scripts under `scripts/`

## Entry Point

Run the workflow from the repo root:

```bash
source .venv/bin/activate
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf --dry-run
```

The supported repo-aligned interface is the script wrapper:

```bash
python3 scripts/pdf_to_audiobook.py INPUT.pdf [options]
```

Or, to play an existing export:

```bash
python3 scripts/pdf_to_audiobook.py --play-export OUTPUT_DIR [options]
```

## Arguments

- positional `INPUT.pdf`: local PDF file path
- `--title`: optional title override
- `--author`: optional author override
- `--genre`: optional genre/category label for the manifest
- `--output-dir`: root directory for generated artifacts. Default: `outputs/audiobooks`
- `--chapter-splitting auto|none`: detect chapter boundaries when possible, or keep one section
- `--dry-run`: extract and clean text without rendering audio
- `--portable-format none|wav|m4a|mp3`: optional local post-processing step after AIFF generation
- `--preview-chars`: preview length printed in dry-run mode
- `--pronunciation-file`: optional JSON file for simple replacement-based pronunciation overrides
- `--play-export`: play an existing export directory instead of generating a new one
- `--play-after-export`: play the export immediately after generation finishes
- `--play-mode auto|audio|text`: prefer generated audio or exported text during playback

## Examples

Preview the cleaned text without generating audio:

```bash
source .venv/bin/activate
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --dry-run
```

Generate local AIFF section files:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --title "My Local Book" \
  --author "Example Author" \
  --genre nonfiction \
  --output-dir ./outputs/audiobooks
```

Generate audio and start listening immediately:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --title "My Local Book" \
  --author "Example Author" \
  --output-dir ./outputs/audiobooks \
  --play-after-export
```

Generate AIFF files plus portable M4A copies:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --title "My Local Book" \
  --author "Example Author" \
  --output-dir ./outputs/audiobooks \
  --portable-format m4a
```

Disable chapter detection and export a single section:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --chapter-splitting none
```

Use pronunciation replacements:

```json
{
  "MLX": "M L X",
  "ChromaDB": "Chroma D B"
}
```

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf \
  --pronunciation-file ./pronunciations.json
```

Play a previously generated export:

```bash
python3 scripts/pdf_to_audiobook.py \
  --play-export ./outputs/audiobooks/my-local-book
```

Read exported text aloud instead of playing audio files:

```bash
python3 scripts/pdf_to_audiobook.py \
  --play-export ./outputs/audiobooks/my-local-book \
  --play-mode text
```

Before you run these examples:

- replace `/path/to/book.pdf` with a real local PDF path
- run from the repo root if you want `./outputs/audiobooks` to resolve inside this repository
- use an absolute `--output-dir` if you want exports written elsewhere
- replace `./outputs/audiobooks/my-local-book` with the exact folder Lulu prints after generation
- if you rerun the same title, expect a new folder such as `my-local-book-2`

## What It Produces

Each run writes a dedicated book directory under the selected output root. If a folder with the same title already exists, Lulu creates a new unique directory such as `my-local-book-2` instead of failing:

```text
outputs/audiobooks/
  my-local-book/
    manifest.json
    text/
      full_text.txt
      01-chapter-1.txt
      02-chapter-2.txt
    audio/
      01-chapter-1.aiff
      02-chapter-2.aiff
      01-chapter-1.m4a
      02-chapter-2.m4a
```

`manifest.json` records:

- source PDF path
- title, author, and optional genre
- extraction counts and empty-page counts
- OCR status
- AIFF outputs and any optional portable-format outputs
- workflow status for text export, audio render, and portable conversion
- render/conversion errors when a run stops after writing text artifacts
- section list, text files, and word counts
- explicit limitations of the current workflow

## Listening To The Result

The PDF workflow can now both generate exports and play them back locally.

- `--play-after-export` starts playback immediately after a successful generation run
- `--play-export OUTPUT_DIR` plays a previously generated export directory
- `--play-mode auto` prefers generated audio files and falls back to exported text when no audio files exist
- `--play-mode audio` requires generated audio files and fails clearly if none are present
- `--play-mode text` reads the exported text files aloud with macOS `say`

What gets played:

- portable files first when they exist (`m4a`, `mp3`, or `wav`)
- otherwise AIFF files under `audio/`
- otherwise exported per-section text files under `text/`

Important:

- `--dry-run` never creates media files
- `--dry-run --play-after-export --play-mode text` is the fastest way to preview the cleaned text as speech
- if you only see `text/` plus `manifest.json`, check `manifest.json` before assuming the export succeeded
- `--play-export` expects a generated export directory, not the original PDF path

## Text Preparation

The workflow intentionally keeps the cleanup pipeline simple and testable:

- validates the local path and PDF signature before parsing
- extracts text with `pypdf`
- removes repeated short headers and footers when they recur across pages
- drops obvious page-number lines
- repairs simple broken line wraps and hyphenated line breaks
- preserves paragraph breaks when the PDF provides meaningful spacing
- normalizes whitespace and common punctuation variants for TTS
- detects chapter headings using conservative `Chapter` and `Part` patterns, plus short title-like subtitles that immediately follow them

It does not claim advanced semantic restructuring beyond those rules.

## OCR Boundary

This workflow supports text-based PDFs first.

If a PDF is scanned, image-only, or yields no extractable text, Lulu stops with a clear error and reports that OCR support is currently `deferred`.

Heavyweight OCR dependencies are intentionally not included in the default repository workflow yet because they would materially change the local setup surface and platform assumptions.

## Audio Boundary

Audio export uses local macOS `say` with `-f` and `-o` to generate AIFF files per section.

If `--portable-format` is set, Lulu then runs a second local conversion pass with `ffmpeg` to create `wav`, `m4a`, or `mp3` copies alongside the AIFF originals.

- this fits the current repo's macOS-first TTS boundary
- this keeps long-form audiobook export separate from the streaming response path in `audio_handler.py`
- this does not currently package chapters into M4B or add embedded audiobook metadata
- `ffmpeg` is required only for the optional portable conversion pass
- portable export requests now fail fast before rendering work starts if `ffmpeg` is unavailable

Install `ffmpeg` on macOS with:

```bash
brew install ffmpeg
```

If you use the repo-local bootstrap path, `./scripts/install_lulu.sh` already installs `ffmpeg` for you and `./scripts/start_lulu.sh --check` now warns early if portable export support is unavailable on the current machine.

## Troubleshooting

### "Encrypted PDFs are not supported"

Provide a decrypted local copy before running the workflow.

### "No extractable text was found"

The PDF is likely scanned or image-only. OCR is not included yet in the repo-default path.

### "I only got text files and no media files"

- if you used `--dry-run`, this is expected because dry-run writes text only
- if you expected audio, inspect `manifest.json` for `workflow_status` and `audio_outputs`
- if you used `--play-export`, make sure you passed the generated export directory and not the source PDF path
- if you just want the cleaned text spoken aloud, run:

```bash
python3 scripts/pdf_to_audiobook.py --play-export /path/to/export --play-mode text
```

### "Output directory already exists"

Lulu now auto-creates a new unique folder for reruns, such as `my-local-book-2`.

### "`say` could not run"

This workflow depends on the native macOS `say` command and is only supported on macOS. The same applies to text playback with `--play-mode text`.

### "No generated audio files were found"

The export has no playable audio files yet. Either:

- rerun the PDF export without `--dry-run`, or
- use `--play-mode text` to have Lulu read the exported text aloud

### "Portable audio conversion requires ffmpeg"

Install `ffmpeg` and rerun the workflow with `--portable-format`:

```bash
brew install ffmpeg
```

The default AIFF export path does not require the extra conversion step. If you installed Lulu with `./scripts/install_lulu.sh`, `ffmpeg` should already be present; rerun `./scripts/start_lulu.sh --check` to confirm the current shell can see it in `PATH`.
