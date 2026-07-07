from __future__ import annotations

import argparse
import json
import os
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


def _command_timeout_seconds(env_name: str, default: int) -> int:
    raw_value = os.getenv(env_name, str(default)).strip()
    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise InputValidationError(
            f"{env_name} must be an integer number of seconds, got {raw_value!r}."
        ) from exc
    if parsed <= 0:
        raise InputValidationError(
            f"{env_name} must be a positive integer number of seconds, got {parsed}."
        )
    return parsed


def _render_timeout_seconds_for_text_path(text_path: Path) -> int:
    base_timeout = _command_timeout_seconds("PDF_AUDIO_RENDER_TIMEOUT_SECONDS", 600)
    try:
        character_count = len(text_path.read_text(encoding="utf-8"))
    except OSError:
        return base_timeout

    # `say` runtime grows roughly with spoken text length, so a fixed 60s timeout
    # is too aggressive for long single-section books such as `full-book.txt`.
    estimated_seconds = (character_count // 18) + 30
    return max(base_timeout, estimated_seconds)


class PDFToAudiobookError(RuntimeError):
    """Base class for operator-facing audiobook workflow errors."""


class InputValidationError(PDFToAudiobookError):
    """Raised when CLI input cannot be accepted safely."""


class PDFProcessingError(PDFToAudiobookError):
    """Raised when the source PDF cannot be processed into audiobook text."""


class AudiobookRenderError(PDFToAudiobookError):
    """Raised when local TTS export fails."""

    def __init__(
        self,
        message: str,
        *,
        audio_paths: list[Path] | None = None,
        portable_audio_paths: list[Path] | None = None,
    ) -> None:
        super().__init__(message)
        self.audio_paths = audio_paths or []
        self.portable_audio_paths = portable_audio_paths or []


class PlaybackError(PDFToAudiobookError):
    """Raised when generated audiobook assets cannot be played locally."""


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


@dataclass(frozen=True)
class ServiceJobResult:
    job_id: str
    status: str
    output_dir: Path | None
    manifest_path: Path | None
    error: str | None
    section_count: int
    dry_run: bool


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Convert a local text-based PDF into cleaned text and local macOS audiobook files."
        )
    )
    parser.add_argument("input_pdf", nargs="?", help="Path to a local PDF file.")
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
        help=("Optional local post-processing format for AIFF outputs. Default: none."),
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
    parser.add_argument(
        "--play-export",
        help=(
            "Play a previously generated export directory. "
            "When set, Lulu plays generated audio if available or exported text "
            "according to --play-mode."
        ),
    )
    parser.add_argument(
        "--play-after-export",
        action="store_true",
        help=(
            "After preparing an export, immediately play generated audio or exported "
            "text according to --play-mode."
        ),
    )
    parser.add_argument(
        "--play-mode",
        choices=("auto", "audio", "text"),
        default="auto",
        help=("Playback preference for --play-export or --play-after-export. Default: auto."),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        validate_cli_args(args)
        if args.play_export:
            export_dir = validate_export_directory(Path(args.play_export))
            played_paths, playback_mode = play_export_directory(
                export_dir,
                play_mode=args.play_mode,
                progress=_print_progress,
            )
            print(
                "[lulu-pdf] Playback complete:"
                f" played {len(played_paths)} {playback_mode} file(s) from {export_dir}"
            )
            return 0
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
        print("[lulu-pdf] Media files: 0 (--dry-run writes text artifacts only)")
        preview = artifacts.full_text_path.read_text(encoding="utf-8")[: max(0, args.preview_chars)]
        if preview:
            print("[lulu-pdf] Preview:")
            print(preview)
        if args.play_after_export:
            played_paths, playback_mode = play_export_directory(
                artifacts.output_dir,
                play_mode=args.play_mode,
                progress=_print_progress,
            )
            print(
                "[lulu-pdf] Playback complete:"
                f" played {len(played_paths)} {playback_mode} file(s) from {artifacts.output_dir}"
            )
        else:
            print(
                "[lulu-pdf] To read the exported text aloud later:"
                f" python3 scripts/pdf_to_audiobook.py --play-export {artifacts.output_dir}"
                " --play-mode text"
            )
    else:
        print(f"[lulu-pdf] Audio files: {len(artifacts.audio_paths)}")
        if artifacts.portable_audio_paths:
            print(
                "[lulu-pdf] Portable files:"
                f" {len(artifacts.portable_audio_paths)} ({args.portable_format})"
            )
        print(
            "[lulu-pdf] To listen now:"
            f" python3 scripts/pdf_to_audiobook.py --play-export {artifacts.output_dir}"
        )
        if args.play_after_export:
            played_paths, playback_mode = play_export_directory(
                artifacts.output_dir,
                play_mode=args.play_mode,
                progress=_print_progress,
            )
            print(
                "[lulu-pdf] Playback complete:"
                f" played {len(played_paths)} {playback_mode} file(s) from {artifacts.output_dir}"
            )
    return 0


def validate_cli_args(args: argparse.Namespace) -> None:
    if args.play_export:
        if args.input_pdf:
            raise InputValidationError("Do not provide INPUT.pdf when using --play-export.")
        if args.play_after_export:
            raise InputValidationError(
                "--play-after-export cannot be used together with --play-export."
            )
        return
    if not args.input_pdf:
        raise InputValidationError(
            "Provide INPUT.pdf to generate an export, or use --play-export "
            "to play an existing export."
        )


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
        portable_format=args.portable_format,
    )

    if args.dry_run:
        progress("Dry-run selected; skipping audio rendering")
        return artifacts

    progress("Rendering section audio locally with macOS say")
    try:
        audio_paths = render_section_audio(artifacts.section_text_paths, progress=progress)
    except AudiobookRenderError as exc:
        update_manifest_audio_outputs(
            manifest_path=artifacts.manifest_path,
            output_dir=artifacts.output_dir,
            audio_paths=exc.audio_paths,
            portable_audio_paths=[],
            portable_format=args.portable_format,
            audio_render_status="failed",
            audio_render_error=str(exc),
            portable_conversion_status=(
                "blocked" if args.portable_format != "none" else "not_requested"
            ),
            portable_conversion_error=(
                "Audio rendering did not finish, so portable conversion did not run."
                if args.portable_format != "none"
                else None
            ),
        )
        raise
    portable_audio_paths: list[Path] = []
    if args.portable_format != "none":
        progress(f"Converting AIFF outputs to {args.portable_format}")
        try:
            portable_audio_paths = convert_audio_outputs(
                audio_paths,
                portable_format=args.portable_format,
                progress=progress,
            )
        except AudiobookRenderError as exc:
            update_manifest_audio_outputs(
                manifest_path=artifacts.manifest_path,
                output_dir=artifacts.output_dir,
                audio_paths=audio_paths,
                portable_audio_paths=exc.portable_audio_paths,
                portable_format=args.portable_format,
                audio_render_status="succeeded",
                audio_render_error=None,
                portable_conversion_status="failed",
                portable_conversion_error=str(exc),
            )
            raise
    update_manifest_audio_outputs(
        manifest_path=artifacts.manifest_path,
        output_dir=artifacts.output_dir,
        audio_paths=audio_paths,
        portable_audio_paths=portable_audio_paths,
        portable_format=args.portable_format,
        audio_render_status="succeeded",
        audio_render_error=None,
        portable_conversion_status=(
            "succeeded" if args.portable_format != "none" else "not_requested"
        ),
        portable_conversion_error=None,
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


def run_service_job(
    *,
    job_id: str,
    input_pdf: Path,
    output_dir: Path,
    title: str | None,
    author: str | None,
    genre: str | None,
    chapter_splitting: str,
    dry_run: bool,
    portable_format: str,
    preview_chars: int,
    pronunciation_file: Path | None,
    progress: Callable[[str], None],
) -> tuple[ServiceJobResult, WorkflowArtifacts | None]:
    args = argparse.Namespace(
        input_pdf=str(input_pdf),
        title=title,
        author=author,
        genre=genre,
        output_dir=str(output_dir),
        chapter_splitting=chapter_splitting,
        dry_run=dry_run,
        portable_format=portable_format,
        preview_chars=preview_chars,
        pronunciation_file=str(pronunciation_file) if pronunciation_file else None,
        play_export=None,
        play_after_export=False,
        play_mode="auto",
    )
    validate_cli_args(args)
    try:
        artifacts = build_audiobook_from_args(args, progress=progress)
    except PDFToAudiobookError as exc:
        return (
            ServiceJobResult(
                job_id=job_id,
                status="failed",
                output_dir=None,
                manifest_path=None,
                error=str(exc),
                section_count=0,
                dry_run=dry_run,
            ),
            None,
        )
    return (
        ServiceJobResult(
            job_id=job_id,
            status="completed",
            output_dir=artifacts.output_dir,
            manifest_path=artifacts.manifest_path,
            error=None,
            section_count=len(artifacts.sections),
            dry_run=dry_run,
        ),
        artifacts,
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
    cleaned_pages = [rebuild_page_text(lines, repeated_lines=repeated_lines) for lines in pages]
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
    return {candidate for candidate, count in counts.items() if count >= minimum_repeats}


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
            if index + 1 < len(paragraphs) and is_probable_subtitle(paragraphs[index + 1]):
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

    normalized_words = [re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", word) for word in words]
    significant_words = [
        word for word in normalized_words if word and word.casefold() not in TITLE_CONNECTOR_WORDS
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
    base_name = slugify(title)
    target = root / base_name
    suffix = 2
    while target.exists():
        target = root / f"{base_name}-{suffix}"
        suffix += 1
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
    portable_format: str,
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
        "workflow_status": {
            "text_export": "succeeded",
            "audio_render": "not_requested",
            "portable_conversion": "not_requested",
        },
        "audio_outputs": {
            "render_status": "not_requested",
            "render_error": None,
            "aiff_files": [],
            "portable_conversion": {
                "status": "not_requested",
                "format": portable_format,
                "error": None,
                "files": [],
            },
        },
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
                timeout=_render_timeout_seconds_for_text_path(text_path),
            )
        except OSError as exc:
            raise AudiobookRenderError(
                "Failed to invoke macOS say. This feature requires the native say command on macOS."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AudiobookRenderError(
                f"Audio rendering timed out for {text_path.name} after {exc.timeout} seconds.",
                audio_paths=audio_paths,
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or (
                f"macOS say exited with status {result.returncode}."
            )
            raise AudiobookRenderError(
                f"Audio rendering failed for {text_path.name}: {detail}",
                audio_paths=audio_paths,
            )
        if not audio_path.exists():
            raise AudiobookRenderError(
                f"Audio rendering reported success for {text_path.name}, "
                f"but no audio file was created at {audio_path}.",
                audio_paths=audio_paths,
            )
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
            "Portable audio conversion requires ffmpeg, but it was not found in PATH.",
            audio_paths=audio_paths,
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
                timeout=_command_timeout_seconds("PDF_AUDIO_CONVERT_TIMEOUT_SECONDS", 120),
            )
        except OSError as exc:
            raise AudiobookRenderError(
                "Failed to invoke ffmpeg for portable audio conversion."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise AudiobookRenderError(
                "Portable audio conversion timed out for "
                f"{audio_path.name} after {exc.timeout} seconds.",
                audio_paths=audio_paths,
                portable_audio_paths=portable_paths,
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or (
                f"ffmpeg exited with status {result.returncode}."
            )
            raise AudiobookRenderError(
                f"Portable audio conversion failed for {audio_path.name}: {detail}",
                audio_paths=audio_paths,
                portable_audio_paths=portable_paths,
            )
        if not target_path.exists():
            raise AudiobookRenderError(
                f"Portable audio conversion reported success for {audio_path.name}, "
                f"but no converted file was created at {target_path}.",
                audio_paths=audio_paths,
                portable_audio_paths=portable_paths,
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
    audio_render_status: str,
    audio_render_error: str | None,
    portable_conversion_status: str,
    portable_conversion_error: str | None,
) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["workflow_status"] = {
        "text_export": "succeeded",
        "audio_render": audio_render_status,
        "portable_conversion": portable_conversion_status,
    }
    manifest["audio_outputs"] = {
        "render_status": audio_render_status,
        "render_error": audio_render_error,
        "aiff_files": [str(path.relative_to(output_dir)) for path in audio_paths],
        "portable_conversion": {
            "status": portable_conversion_status,
            "format": portable_format,
            "error": portable_conversion_error,
            "files": [str(path.relative_to(output_dir)) for path in portable_audio_paths],
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def validate_export_directory(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise InputValidationError(f"Export directory does not exist: {resolved}")
    if not resolved.is_dir():
        raise InputValidationError(f"Export path is not a directory: {resolved}")
    if not (resolved / "text").exists():
        raise InputValidationError(f"Export directory does not contain a text/ folder: {resolved}")
    return resolved


def play_export_directory(
    export_dir: Path,
    *,
    play_mode: str,
    progress: Callable[[str], None],
) -> tuple[list[Path], str]:
    assets = collect_export_assets(export_dir)
    selected_paths, playback_mode = select_playback_paths(assets, play_mode=play_mode)
    command_name = "afplay" if playback_mode == "audio" else "say"
    for path in selected_paths:
        progress(f"Playing {path.name} using {command_name}")
        command = ["afplay", str(path)] if playback_mode == "audio" else ["say", "-f", str(path)]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=_command_timeout_seconds("PDF_AUDIO_PLAYBACK_TIMEOUT_SECONDS", 120),
            )
        except OSError as exc:
            raise PlaybackError(f"Failed to invoke macOS {command_name} for {path.name}.") from exc
        except subprocess.TimeoutExpired as exc:
            raise PlaybackError(
                f"Playback timed out for {path.name} after {exc.timeout} seconds."
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or "").strip() or (
                f"macOS {command_name} exited with status {result.returncode}."
            )
            raise PlaybackError(f"Playback failed for {path.name}: {detail}")
    return selected_paths, playback_mode


def collect_export_assets(export_dir: Path) -> dict[str, list[Path]]:
    manifest_path = export_dir / "manifest.json"
    manifest = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PlaybackError("Export manifest is malformed and could not be read.") from exc
    audio_outputs = manifest.get("audio_outputs", {})
    portable_payload = audio_outputs.get("portable_conversion", {})

    portable_paths = _resolve_relative_paths(
        export_dir,
        portable_payload.get("files", []),
    )
    aiff_paths = _resolve_relative_paths(
        export_dir,
        audio_outputs.get("aiff_files", []),
    )
    if not portable_paths:
        portable_paths = sorted((export_dir / "audio").glob("*.m4a"))
        portable_paths.extend(sorted((export_dir / "audio").glob("*.mp3")))
        portable_paths.extend(sorted((export_dir / "audio").glob("*.wav")))
    if not aiff_paths:
        aiff_paths = sorted((export_dir / "audio").glob("*.aiff"))

    text_dir = export_dir / "text"
    section_text_paths = sorted(
        path for path in text_dir.glob("*.txt") if path.name != "full_text.txt"
    )
    full_text_path = text_dir / "full_text.txt"
    text_paths = section_text_paths
    if not text_paths and full_text_path.exists():
        text_paths = [full_text_path]

    return {
        "portable": portable_paths,
        "audio": aiff_paths,
        "text": text_paths,
    }


def _resolve_relative_paths(output_dir: Path, values: object) -> list[Path]:
    if not isinstance(values, list):
        return []
    output_root = output_dir.resolve()
    resolved_paths: list[Path] = []
    for value in values:
        if not isinstance(value, str):
            continue
        candidate = Path(value)
        if candidate.is_absolute():
            continue
        path = (output_dir / candidate).resolve()
        if not str(path).startswith(str(output_root) + os.sep) and path != output_root:
            continue
        if path.is_file():
            resolved_paths.append(path)
    return resolved_paths


def select_playback_paths(
    assets: dict[str, list[Path]],
    *,
    play_mode: str,
) -> tuple[list[Path], str]:
    portable_paths = assets.get("portable", [])
    audio_paths = assets.get("audio", [])
    text_paths = assets.get("text", [])

    if play_mode == "audio":
        selected = portable_paths or audio_paths
        if not selected:
            raise PlaybackError(
                "No generated audio files were found in this export. "
                "Use --play-mode text to read the exported text aloud instead."
            )
        return selected, "audio"
    if play_mode == "text":
        if not text_paths:
            raise PlaybackError("No exported text files were found in this export.")
        return text_paths, "text"

    if portable_paths or audio_paths:
        return portable_paths or audio_paths, "audio"
    if text_paths:
        return text_paths, "text"
    raise PlaybackError("The export does not contain playable audio files or readable text files.")


def _print_progress(message: str) -> None:
    print(f"[lulu-pdf] {message}")


if __name__ == "__main__":
    raise SystemExit(main())
