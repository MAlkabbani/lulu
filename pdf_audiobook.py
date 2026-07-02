from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from pypdf import PdfReader

OCR_STATUS_DEFERRED = "deferred"
DEFAULT_OUTPUT_ROOT = Path("outputs") / "audiobooks"
DEFAULT_PREVIEW_CHARS = 600
MAX_REPEAT_CANDIDATE_CHARS = 80
PORTABLE_AUDIO_FORMATS = ("none", "wav", "m4a", "mp3")
PAGE_NUMBER_PATTERN = re.compile(
    r"^(?:page\s+)?(?:[-–—]?\s*)?(?:\d+|[ivxlcdm]+)(?:\s*[-–—])?$",
    re.IGNORECASE,
)
CHAPTER_HEADING_PATTERN = re.compile(
    r"^(chapter|part)\s+([0-9]+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\b"
    r"(?:\s*[:.\-]\s*.+)?$",
    re.IGNORECASE,
)
SUBTITLE_HEADING_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9 ,.'&:-]{2,79}$")
WHITESPACE_PATTERN = re.compile(r"\s+")
TITLE_CONNECTOR_WORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "by",
    "for",
    "from",
    "in",
    "into",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}


class PDFToAudiobookError(RuntimeError):
    """Base class for operator-facing audiobook workflow errors."""


class InputValidationError(PDFToAudiobookError):
    """Raised when CLI input cannot be accepted safely."""


class PDFProcessingError(PDFToAudiobookError):
    """Raised when the source PDF cannot be processed into audiobook text."""


class AudiobookRenderError(PDFToAudiobookError):
    """Raised when local TTS export fails."""


@dataclass(frozen=True)
class BookMetadata:
    title: str
    author: str
    genre: str | None


@dataclass(frozen=True)
class PreparedSection:
    index: int
    title: str
    text: str


@dataclass(frozen=True)
class ExtractionSummary:
    page_count: int
    extracted_pages: int
    empty_pages: int
    extracted_characters: int
    removed_repeated_lines: list[str]
    ocr_status: str


@dataclass(frozen=True)
class WorkflowArtifacts:
    output_dir: Path
    manifest_path: Path
    full_text_path: Path
    section_text_paths: list[Path]
    audio_paths: list[Path]
    portable_audio_paths: list[Path]
    sections: list[PreparedSection]
    extraction_summary: ExtractionSummary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a local text-based PDF into cleaned text and local macOS audiobook files."
        )
    )
    parser.add_argument("input_pdf", help="Path to a local PDF file.")
    parser.add_argument(
        "--title",
        help="Book title override. Defaults to PDF metadata title or the file stem.",
    )
    parser.add_argument(
        "--author",
        help="Author override. Defaults to PDF metadata author or 'Unknown author'.",
    )
    parser.add_argument(
        "--genre",
        help="Optional genre or category label stored in the manifest.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Directory where Lulu should write text, manifest, and audio artifacts.",
    )
    parser.add_argument(
        "--chapter-splitting",
        choices=("auto", "none"),
        default="auto",
        help="Use detected chapter boundaries when available, or keep the book as one section.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and clean the PDF without rendering audio files.",
    )
    parser.add_argument(
        "--portable-format",
        choices=PORTABLE_AUDIO_FORMATS,
        default="none",
        help=(
            "Optional local post-processing format for AIFF outputs. "
            "Default: none."
        ),
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=DEFAULT_PREVIEW_CHARS,
        help="How many cleaned characters to print in dry-run mode.",
    )
    parser.add_argument(
        "--pronunciation-file",
        help="Optional JSON file containing simple pronunciation override replacements.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        artifacts = build_audiobook_from_args(args, progress=_print_progress)
    except PDFToAudiobookError as exc:
        print(f"[lulu-pdf] ERROR: {exc}")
        return 1

    print(
        "[lulu-pdf] Success:"
        f" prepared {len(artifacts.sections)} section(s) in {artifacts.output_dir}"
    )
    print(f"[lulu-pdf] Manifest: {artifacts.manifest_path}")
    if args.dry_run:
        preview = artifacts.full_text_path.read_text(encoding="utf-8")[: max(0, args.preview_chars)]
        if preview:
            print("[lulu-pdf] Preview:")
            print(preview)
    else:
        print(f"[lulu-pdf] Audio files: {len(artifacts.audio_paths)}")
        if artifacts.portable_audio_paths:
            print(
                "[lulu-pdf] Portable files:"
                f" {len(artifacts.portable_audio_paths)} ({args.portable_format})"
            )
    return 0


def build_audiobook_from_args(
    args: argparse.Namespace,
    *,
    progress: Callable[[str], None],
) -> WorkflowArtifacts:
    input_pdf = validate_input_pdf(Path(args.input_pdf))
    output_root = Path(args.output_dir).expanduser()
    pronunciation_file = (
        Path(args.pronunciation_file).expanduser() if args.pronunciation_file else None
    )

    progress(f"Validating {input_pdf}")
    metadata_from_pdf, extraction = extract_pdf_text(input_pdf)
    metadata = resolve_book_metadata(
        source_pdf=input_pdf,
        metadata_from_pdf=metadata_from_pdf,
        title_override=args.title,
        author_override=args.author,
        genre=args.genre,
    )
    progress(
        "Extracted "
        f"{extraction.extracted_characters} characters from {extraction.page_count} page(s)"
    )

    progress("Cleaning PDF layout artifacts")
    cleaned_text = clean_pdf_text(extraction.page_texts)
    if not cleaned_text.strip():
        raise PDFProcessingError(
            "The PDF did not produce usable text after cleanup. "
            "This usually means the file is empty, heavily layout-driven, or image-only. "
            f"OCR support is {OCR_STATUS_DEFERRED} in this workflow."
        )

    overrides = load_pronunciation_overrides(pronunciation_file)
    if overrides:
        progress(f"Applying {len(overrides)} pronunciation override(s)")
        cleaned_text = apply_pronunciation_overrides(cleaned_text, overrides)

    progress(f"Preparing sections using chapter splitting: {args.chapter_splitting}")
    sections = split_into_sections(cleaned_text, chapter_splitting=args.chapter_splitting)

    output_dir = prepare_output_directory(output_root, metadata.title)
    progress(f"Writing text artifacts to {output_dir}")
    artifacts = write_workflow_artifacts(
        output_dir=output_dir,
        source_pdf=input_pdf,
        metadata=metadata,
        extraction=extraction,
        sections=sections,
        chapter_splitting=args.chapter_splitting,
    )

    if args.dry_run:
        progress("Dry-run selected; skipping audio rendering")
        return artifacts

    progress("Rendering section audio locally with macOS say")
    audio_paths = render_section_audio(artifacts.section_text_paths, progress=progress)
    portable_audio_paths: list[Path] = []
    if args.portable_format != "none":
        progress(f"Converting AIFF outputs to {args.portable_format}")
        portable_audio_paths = convert_audio_outputs(
            audio_paths,
            portable_format=args.portable_format,
            progress=progress,
        )
    update_manifest_audio_outputs(
        manifest_path=artifacts.manifest_path,
        output_dir=artifacts.output_dir,
        audio_paths=audio_paths,
        portable_audio_paths=portable_audio_paths,
        portable_format=args.portable_format,
    )
    return WorkflowArtifacts(
        output_dir=artifacts.output_dir,
        manifest_path=artifacts.manifest_path,
        full_text_path=artifacts.full_text_path,
        section_text_paths=artifacts.section_text_paths,
        audio_paths=audio_paths,
        portable_audio_paths=portable_audio_paths,
        sections=artifacts.sections,
        extraction_summary=artifacts.extraction_summary,
    )


@dataclass(frozen=True)
class ExtractedPDF:
    metadata: dict[str, str]
    page_texts: list[str]
    page_count: int
    extracted_pages: int
    empty_pages: int
    extracted_characters: int


def validate_input_pdf(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise InputValidationError(f"Input PDF does not exist: {resolved}")
    if not resolved.is_file():
        raise InputValidationError(f"Input PDF is not a file: {resolved}")
    if resolved.suffix.lower() != ".pdf":
        raise InputValidationError("Input file must use the .pdf extension.")
    try:
        header = resolved.read_bytes()[:5]
    except OSError as exc:
        raise InputValidationError(f"Unable to read input PDF: {resolved}") from exc
    if header != b"%PDF-":
        raise InputValidationError("Input file is not recognized as a PDF document.")
    return resolved


def extract_pdf_text(path: Path) -> tuple[dict[str, str], ExtractedPDF]:
    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # pragma: no cover - exception type depends on pypdf version
        raise PDFProcessingError(
            "Failed to read the PDF. The file may be corrupted or use an unsupported structure."
        ) from exc

    if reader.is_encrypted:
        raise PDFProcessingError(
            "Encrypted PDFs are not supported by this local workflow. "
            "Please provide a decrypted copy first."
        )

    page_texts: list[str] = []
    extracted_pages = 0
    empty_pages = 0
    extracted_characters = 0

    for page_number, page in enumerate(reader.pages, start=1):
        try:
            raw_text = page.extract_text() or ""
        except Exception as exc:  # pragma: no cover - depends on malformed page contents
            raise PDFProcessingError(
                f"Failed to extract text from page {page_number}. "
                "The PDF may contain unsupported or corrupted page content."
            ) from exc
        normalized_text = normalize_extracted_text(raw_text)
        page_texts.append(normalized_text)
        if normalized_text.strip():
            extracted_pages += 1
            extracted_characters += len(normalized_text.strip())
        else:
            empty_pages += 1

    if not page_texts:
        raise PDFProcessingError("The PDF contains no pages.")
    if extracted_characters == 0:
        raise PDFProcessingError(
            "No extractable text was found. "
            "This PDF appears to be scanned, image-only, or otherwise not text-based. "
            f"OCR support is {OCR_STATUS_DEFERRED} in this workflow."
        )

    metadata = {}
    for key, value in (reader.metadata or {}).items():
        clean_key = str(key).lstrip("/")
        clean_value = str(value).strip()
        if clean_value:
            metadata[clean_key] = clean_value

    return metadata, ExtractedPDF(
        metadata=metadata,
        page_texts=page_texts,
        page_count=len(page_texts),
        extracted_pages=extracted_pages,
        empty_pages=empty_pages,
        extracted_characters=extracted_characters,
    )


def resolve_book_metadata(
    *,
    source_pdf: Path,
    metadata_from_pdf: dict[str, str],
    title_override: str | None,
    author_override: str | None,
    genre: str | None,
) -> BookMetadata:
    title = (title_override or metadata_from_pdf.get("Title") or source_pdf.stem).strip()
    author = (author_override or metadata_from_pdf.get("Author") or "Unknown author").strip()
    clean_title = normalize_inline_text(title) or source_pdf.stem
    clean_author = normalize_inline_text(author) or "Unknown author"
    clean_genre = normalize_inline_text(genre) if genre else None
    return BookMetadata(title=clean_title, author=clean_author, genre=clean_genre)


def normalize_extracted_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ")
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl")
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = text.replace("\u2026", "...")
    return text


def clean_pdf_text(page_texts: list[str]) -> str:
    pages = [split_page_lines(text) for text in page_texts]
    repeated_lines = detect_repeated_margin_lines(pages)
    cleaned_pages = [
        rebuild_page_text(lines, repeated_lines=repeated_lines)
        for lines in pages
    ]
    paragraphs = [page for page in cleaned_pages if page]
    joined = "\n\n".join(paragraphs)
    joined = re.sub(r"[ \t]+\n", "\n", joined)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    joined = re.sub(r"[ \t]{2,}", " ", joined)
    return joined.strip()


def split_page_lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n")]


def detect_repeated_margin_lines(pages: list[list[str]]) -> set[str]:
    counts: dict[str, int] = {}
    for lines in pages:
        meaningful: list[str] = []
        for line in lines:
            normalized = normalize_margin_candidate(line)
            if normalized:
                meaningful.append(normalized)
        if not meaningful:
            continue
        candidates = meaningful[:2] + meaningful[-2:]
        for candidate in set(candidates):
            counts[candidate] = counts.get(candidate, 0) + 1
    minimum_repeats = 2 if len(pages) < 4 else 3
    return {
        candidate
        for candidate, count in counts.items()
        if count >= minimum_repeats
    }


def normalize_margin_candidate(line: str) -> str:
    clean_line = normalize_inline_text(line)
    if not clean_line:
        return ""
    if len(clean_line) > MAX_REPEAT_CANDIDATE_CHARS:
        return ""
    if PAGE_NUMBER_PATTERN.fullmatch(clean_line):
        return ""
    return clean_line.casefold()


def rebuild_page_text(lines: list[str], *, repeated_lines: set[str]) -> str:
    cleaned_lines: list[str] = []
    for line in lines:
        clean_line = normalize_inline_text(line)
        if not clean_line:
            cleaned_lines.append("")
            continue
        if PAGE_NUMBER_PATTERN.fullmatch(clean_line):
            continue
        if normalize_margin_candidate(clean_line) in repeated_lines:
            continue
        cleaned_lines.append(clean_line)

    paragraphs: list[str] = []
    paragraph_lines: list[str] = []

    for line in cleaned_lines:
        if not line:
            flush_paragraph(paragraph_lines, paragraphs)
            continue
        if is_standalone_heading(line):
            flush_paragraph(paragraph_lines, paragraphs)
            paragraphs.append(line)
            continue
        paragraph_lines.append(line)

    flush_paragraph(paragraph_lines, paragraphs)
    return "\n\n".join(paragraphs).strip()


def flush_paragraph(paragraph_lines: list[str], paragraphs: list[str]) -> None:
    if not paragraph_lines:
        return
    merged = join_wrapped_lines(paragraph_lines)
    if merged:
        paragraphs.append(merged)
    paragraph_lines.clear()


def join_wrapped_lines(lines: list[str]) -> str:
    merged = ""
    for line in lines:
        if not merged:
            merged = line
            continue
        if merged.endswith("-") and line and line[0].islower():
            merged = merged[:-1] + line
            continue
        merged = f"{merged} {line}"
    return normalize_inline_text(merged)


def normalize_inline_text(text: str | None) -> str:
    if not text:
        return ""
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def is_standalone_heading(line: str) -> bool:
    if CHAPTER_HEADING_PATTERN.fullmatch(line):
        return True
    if SUBTITLE_HEADING_PATTERN.fullmatch(line) and len(line.split()) <= 10:
        return True
    return False


def split_into_sections(text: str, *, chapter_splitting: str) -> list[PreparedSection]:
    if chapter_splitting == "none":
        return [PreparedSection(index=1, title="Full Book", text=text.strip())]

    paragraphs = [paragraph.strip() for paragraph in text.split("\n\n") if paragraph.strip()]
    sections: list[PreparedSection] = []
    current_title = "Opening"
    current_paragraphs: list[str] = []
    detected_heading = False
    index = 0

    while index < len(paragraphs):
        paragraph = paragraphs[index]
        if CHAPTER_HEADING_PATTERN.fullmatch(paragraph):
            detected_heading = True
            if current_paragraphs:
                sections.append(
                    PreparedSection(
                        index=len(sections) + 1,
                        title=current_title,
                        text="\n\n".join(current_paragraphs).strip(),
                    )
                )
                current_paragraphs = []
            current_title = paragraph
            if (
                index + 1 < len(paragraphs)
                and is_probable_subtitle(paragraphs[index + 1])
            ):
                current_title = f"{paragraph}: {paragraphs[index + 1]}"
                index += 1
        else:
            current_paragraphs.append(paragraph)
        index += 1

    if current_paragraphs:
        sections.append(
            PreparedSection(
                index=len(sections) + 1,
                title=current_title,
                text="\n\n".join(current_paragraphs).strip(),
            )
        )

    if not detected_heading:
        return [PreparedSection(index=1, title="Full Book", text=text.strip())]

    normalized_sections = [
        PreparedSection(
            index=position,
            title=normalize_section_title(section.title, fallback_index=position),
            text=section.text,
        )
        for position, section in enumerate(sections, start=1)
        if section.text.strip()
    ]
    return normalized_sections or [PreparedSection(index=1, title="Full Book", text=text.strip())]


def is_probable_subtitle(paragraph: str) -> bool:
    if CHAPTER_HEADING_PATTERN.fullmatch(paragraph):
        return False
    if paragraph.endswith((".", "!", "?")):
        return False
    if len(paragraph) > 80:
        return False
    if SUBTITLE_HEADING_PATTERN.fullmatch(paragraph) is not None:
        return True

    words = paragraph.split()
    if not words or len(words) > 10:
        return False

    alphabetic_words = [word for word in words if any(char.isalpha() for char in word)]
    if not alphabetic_words:
        return False

    normalized_words = [
        re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", word)
        for word in words
    ]
    significant_words = [
        word
        for word in normalized_words
        if word and word.casefold() not in TITLE_CONNECTOR_WORDS
    ]
    if not significant_words:
        return False

    return all(_is_title_like_subtitle_word(word) for word in significant_words)


def _is_title_like_subtitle_word(word: str) -> bool:
    if not word:
        return False
    if word.isupper():
        return True
    if word.isdigit():
        return True
    if re.fullmatch(r"[IVXLCDM]+", word):
        return True
    if len(word) == 1:
        return word.isupper()
    return word[0].isupper() and word[1:] == word[1:].lower()


def normalize_section_title(title: str, *, fallback_index: int) -> str:
    clean_title = normalize_inline_text(title)
    return clean_title or f"Section {fallback_index:02d}"


def load_pronunciation_overrides(path: Path | None) -> list[tuple[str, str]]:
    if path is None:
        return []
    if not path.exists():
        raise InputValidationError(f"Pronunciation file does not exist: {path}")
    if not path.is_file():
        raise InputValidationError(f"Pronunciation file is not a file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputValidationError(
            "Pronunciation file must contain valid JSON object mappings."
        ) from exc
    if not isinstance(payload, dict):
        raise InputValidationError("Pronunciation file must be a JSON object of replacements.")

    overrides: list[tuple[str, str]] = []
    for source, replacement in payload.items():
        if not isinstance(source, str) or not isinstance(replacement, str):
            raise InputValidationError(
                "Pronunciation overrides must map string keys to string replacement values."
            )
        clean_source = source.strip()
        clean_replacement = replacement.strip()
        if clean_source and clean_replacement:
            overrides.append((clean_source, clean_replacement))
    return overrides


def apply_pronunciation_overrides(text: str, overrides: list[tuple[str, str]]) -> str:
    updated = text
    for source, replacement in overrides:
        if re.fullmatch(r"[\w\s'-]+", source):
            pattern = re.compile(rf"\b{re.escape(source)}\b", re.IGNORECASE)
            updated = pattern.sub(replacement, updated)
        else:
            updated = updated.replace(source, replacement)
    return updated


def prepare_output_directory(output_root: Path, title: str) -> Path:
    root = output_root.expanduser().resolve()
    target = root / slugify(title)
    if target.exists():
        raise InputValidationError(
            f"Output directory already exists: {target}. "
            "Choose a different title or output directory."
        )
    target.mkdir(parents=True, exist_ok=False)
    return target


def slugify(value: str) -> str:
    lowered = value.casefold()
    cleaned = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return cleaned or "lulu-audiobook"


def write_workflow_artifacts(
    *,
    output_dir: Path,
    source_pdf: Path,
    metadata: BookMetadata,
    extraction: ExtractedPDF,
    sections: list[PreparedSection],
    chapter_splitting: str,
) -> WorkflowArtifacts:
    text_dir = output_dir / "text"
    audio_dir = output_dir / "audio"
    text_dir.mkdir()
    audio_dir.mkdir()

    full_text_path = text_dir / "full_text.txt"
    full_text = "\n\n".join(section.text for section in sections).strip()
    full_text_path.write_text(full_text, encoding="utf-8")

    section_text_paths: list[Path] = []
    section_payloads: list[dict[str, object]] = []
    for section in sections:
        file_name = f"{section.index:02d}-{slugify(section.title)}.txt"
        text_path = text_dir / file_name
        text_path.write_text(section.text, encoding="utf-8")
        section_text_paths.append(text_path)
        section_payloads.append(
            {
                "index": section.index,
                "title": section.title,
                "text_file": str(text_path.relative_to(output_dir)),
                "characters": len(section.text),
                "word_count": len(section.text.split()),
            }
        )

    manifest_path = output_dir / "manifest.json"
    repeated_lines = sorted(
        detect_repeated_margin_lines([split_page_lines(text) for text in extraction.page_texts])
    )
    extraction_summary = ExtractionSummary(
        page_count=extraction.page_count,
        extracted_pages=extraction.extracted_pages,
        empty_pages=extraction.empty_pages,
        extracted_characters=extraction.extracted_characters,
        removed_repeated_lines=repeated_lines,
        ocr_status=OCR_STATUS_DEFERRED,
    )
    manifest = {
        "source_pdf": str(source_pdf),
        "metadata": asdict(metadata),
        "chapter_splitting": chapter_splitting,
        "ocr_status": OCR_STATUS_DEFERRED,
        "sections": section_payloads,
        "extraction": asdict(extraction_summary),
        "limitations": [
            "This workflow supports text-based PDFs first.",
            "Scanned or image-only PDFs require OCR, which is deferred in this repository.",
            (
                "Audio export writes AIFF section files by default and can optionally "
                "create portable copies through a local ffmpeg conversion pass."
            ),
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return WorkflowArtifacts(
        output_dir=output_dir,
        manifest_path=manifest_path,
        full_text_path=full_text_path,
        section_text_paths=section_text_paths,
        audio_paths=[],
        portable_audio_paths=[],
        sections=sections,
        extraction_summary=extraction_summary,
    )


def render_section_audio(
    section_text_paths: list[Path],
    *,
    progress: Callable[[str], None],
) -> list[Path]:
    audio_paths: list[Path] = []
    for text_path in section_text_paths:
        audio_path = text_path.parents[1] / "audio" / f"{text_path.stem}.aiff"
        progress(f"Rendering {text_path.name} -> {audio_path.name}")
        try:
            result = subprocess.run(
                ["say", "-f", str(text_path), "-o", str(audio_path)],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise AudiobookRenderError(
                "Failed to invoke macOS say. "
                "This feature requires the native say command on macOS."
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or (
                f"macOS say exited with status {result.returncode}."
            )
            raise AudiobookRenderError(f"Audio rendering failed for {text_path.name}: {detail}")
        audio_paths.append(audio_path)
    return audio_paths


def convert_audio_outputs(
    audio_paths: list[Path],
    *,
    portable_format: str,
    progress: Callable[[str], None],
) -> list[Path]:
    if portable_format == "none":
        return []
    ffmpeg_binary = shutil.which("ffmpeg")
    if ffmpeg_binary is None:
        raise AudiobookRenderError(
            "Portable audio conversion requires ffmpeg, but it was not found in PATH."
        )

    portable_paths: list[Path] = []
    for audio_path in audio_paths:
        target_path = audio_path.with_suffix(f".{portable_format}")
        progress(f"Converting {audio_path.name} -> {target_path.name}")
        command = build_ffmpeg_command(
            ffmpeg_binary=ffmpeg_binary,
            input_path=audio_path,
            output_path=target_path,
            portable_format=portable_format,
        )
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError as exc:
            raise AudiobookRenderError(
                "Failed to invoke ffmpeg for portable audio conversion."
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or (
                f"ffmpeg exited with status {result.returncode}."
            )
            raise AudiobookRenderError(
                f"Portable audio conversion failed for {audio_path.name}: {detail}"
            )
        portable_paths.append(target_path)
    return portable_paths


def build_ffmpeg_command(
    *,
    ffmpeg_binary: str,
    input_path: Path,
    output_path: Path,
    portable_format: str,
) -> list[str]:
    command = [ffmpeg_binary, "-y", "-i", str(input_path)]
    if portable_format == "wav":
        command.extend(["-c:a", "pcm_s16le"])
    elif portable_format == "m4a":
        command.extend(["-c:a", "aac", "-b:a", "96k"])
    elif portable_format == "mp3":
        command.extend(["-codec:a", "libmp3lame", "-q:a", "4"])
    else:  # pragma: no cover - argparse choices guard this
        raise ValueError(f"Unsupported portable format: {portable_format}")
    command.append(str(output_path))
    return command


def update_manifest_audio_outputs(
    *,
    manifest_path: Path,
    output_dir: Path,
    audio_paths: list[Path],
    portable_audio_paths: list[Path],
    portable_format: str,
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["audio_outputs"] = {
        "aiff_files": [str(path.relative_to(output_dir)) for path in audio_paths],
        "portable_format": portable_format,
        "portable_files": [str(path.relative_to(output_dir)) for path in portable_audio_paths],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _print_progress(message: str) -> None:
    print(f"[lulu-pdf] {message}")


if __name__ == "__main__":
    raise SystemExit(main())
