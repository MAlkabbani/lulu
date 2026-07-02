# PDF To Audiobooks

Lulu now includes a repo-local PDF-to-audiobook workflow for text-based PDFs.

## Why It Lives Here

- it stays local-first and uses only local file access plus macOS `say`
- it does not modify the interactive assistant runtime in `main.py`
- it follows the existing repo pattern of small utility scripts under `scripts/`

## Entry Point

Run the workflow from the repo root:

```bash
python3 scripts/pdf_to_audiobook.py /path/to/book.pdf --dry-run
```

The supported repo-aligned interface is the script wrapper:

```bash
python3 scripts/pdf_to_audiobook.py INPUT.pdf [options]
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

## Examples

Preview the cleaned text without generating audio:

```bash
python3 scripts/pdf_to_audiobook.py ./samples/book.pdf \
  --title "My Local Book" \
  --author "Example Author" \
  --dry-run
```

Generate local AIFF chapter files:

```bash
python3 scripts/pdf_to_audiobook.py ./samples/book.pdf \
  --title "My Local Book" \
  --author "Example Author" \
  --genre nonfiction \
  --output-dir ./outputs/audiobooks
```

Generate AIFF files plus portable M4A copies:

```bash
python3 scripts/pdf_to_audiobook.py ./samples/book.pdf \
  --title "My Local Book" \
  --author "Example Author" \
  --output-dir ./outputs/audiobooks \
  --portable-format m4a
```

Disable chapter detection and export a single section:

```bash
python3 scripts/pdf_to_audiobook.py ./samples/book.pdf \
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
python3 scripts/pdf_to_audiobook.py ./samples/book.pdf \
  --pronunciation-file ./pronunciations.json
```

## What It Produces

Each run writes a dedicated book directory under the selected output root:

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
- section list, text files, and word counts
- explicit limitations of the current workflow

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

## Troubleshooting

### "Encrypted PDFs are not supported"

Provide a decrypted local copy before running the workflow.

### "No extractable text was found"

The PDF is likely scanned or image-only. OCR is not included yet in the repo-default path.

### "Output directory already exists"

Choose a different `--output-dir` or override the title so Lulu writes to a new book directory.

### "`say` could not run"

This workflow depends on the native macOS `say` command and is only supported on macOS.

### "Portable audio conversion requires ffmpeg"

Install `ffmpeg` and rerun the workflow with `--portable-format`. The default AIFF export path does not require the extra conversion step.
