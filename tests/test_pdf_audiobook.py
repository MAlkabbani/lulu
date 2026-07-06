from __future__ import annotations

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from pdf_audiobook import (
    InputValidationError,
    PDFProcessingError,
    PlaybackError,
    _command_timeout_seconds,
    _resolve_relative_paths,
    apply_pronunciation_overrides,
    build_audiobook_from_args,
    build_ffmpeg_command,
    clean_pdf_text,
    convert_audio_outputs,
    main,
    play_export_directory,
    render_section_audio,
    split_into_sections,
    update_manifest_audio_outputs,
    validate_input_pdf,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PDF = PROJECT_ROOT / "tests" / "fixtures" / "sample_book.pdf"


def write_text_pdf(path: Path, pages: list[str], *, metadata: dict[str, str] | None = None) -> None:
    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)

    for page_text in pages:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {
                NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref}),
            }
        )
        stream = DecodedStreamObject()
        stream.set_data(build_content_stream(page_text).encode("utf-8"))
        page[NameObject("/Contents")] = writer._add_object(stream)

    if metadata:
        writer.add_metadata({f"/{key}": value for key, value in metadata.items()})

    with path.open("wb") as handle:
        writer.write(handle)


def build_content_stream(text: str) -> str:
    lines = text.split("\n")
    commands = ["BT", "/F1 12 Tf", "14 TL", "72 720 Td"]
    for index, line in enumerate(lines):
        if index > 0:
            commands.append("T*")
        commands.append(f"({escape_pdf_text(line)}) Tj")
    commands.append("ET")
    return "\n".join(commands)


def escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def test_validate_input_pdf_rejects_non_pdf_extension(tmp_path: Path) -> None:
    wrong_file = tmp_path / "notes.txt"
    wrong_file.write_text("not a pdf", encoding="utf-8")

    with pytest.raises(Exception, match=r"\.pdf"):
        validate_input_pdf(wrong_file)


@pytest.mark.parametrize("raw_value", ["0", "-3"])
def test_command_timeout_seconds_requires_positive_integer(
    monkeypatch,
    raw_value: str,
) -> None:
    monkeypatch.setenv("LULU_TEST_TIMEOUT", raw_value)

    with pytest.raises(
        InputValidationError,
        match="must be a positive integer number of seconds",
    ):
        _command_timeout_seconds("LULU_TEST_TIMEOUT", 5)


def test_extract_workflow_rejects_encrypted_pdf(tmp_path: Path) -> None:
    pdf_path = tmp_path / "locked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt("secret")
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    args = Namespace(
        input_pdf=str(pdf_path),
        title=None,
        author=None,
        genre=None,
        output_dir=str(tmp_path / "out"),
        chapter_splitting="auto",
        dry_run=True,
        portable_format="none",
        preview_chars=200,
        pronunciation_file=None,
    )

    with pytest.raises(PDFProcessingError, match="Encrypted PDFs are not supported"):
        build_audiobook_from_args(args, progress=lambda _: None)


def test_extract_workflow_reports_image_only_pdf_without_ocr(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scan.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with pdf_path.open("wb") as handle:
        writer.write(handle)

    args = Namespace(
        input_pdf=str(pdf_path),
        title=None,
        author=None,
        genre=None,
        output_dir=str(tmp_path / "out"),
        chapter_splitting="auto",
        dry_run=True,
        portable_format="none",
        preview_chars=200,
        pronunciation_file=None,
    )

    with pytest.raises(PDFProcessingError, match="OCR support is deferred"):
        build_audiobook_from_args(args, progress=lambda _: None)


def test_clean_pdf_text_removes_repeated_headers_footers_and_page_numbers() -> None:
    cleaned = clean_pdf_text(
        [
            "Example Book\n1\nChapter 1\nThis is the first line\nof a wrapped paragraph.\n1",
            "Example Book\n2\nThis is the second page\nwith more text.\n2",
        ]
    )

    assert "Example Book" not in cleaned
    assert "\n1\n" not in f"\n{cleaned}\n"
    assert "This is the first line of a wrapped paragraph." in cleaned
    assert "This is the second page with more text." in cleaned


def test_split_into_sections_detects_chapters_and_subtitles() -> None:
    text = (
        "Preface paragraph.\n\n"
        "Chapter 1\n\n"
        "THE ARRIVAL\n\n"
        "The first chapter begins here.\n\n"
        "Chapter 2\n\n"
        "The second chapter begins here."
    )

    sections = split_into_sections(text, chapter_splitting="auto")

    assert [section.title for section in sections] == [
        "Opening",
        "Chapter 1: THE ARRIVAL",
        "Chapter 2",
    ]
    assert sections[1].text == "The first chapter begins here."


def test_split_into_sections_detects_title_case_subtitles() -> None:
    text = (
        "Chapter 3\n\n"
        "A Study in Tea\n\n"
        "The chapter starts here.\n\n"
        "Chapter 4\n\n"
        "Another body paragraph."
    )

    sections = split_into_sections(text, chapter_splitting="auto")

    assert [section.title for section in sections] == [
        "Chapter 3: A Study in Tea",
        "Chapter 4",
    ]
    assert sections[0].text == "The chapter starts here."


def test_split_into_sections_does_not_promote_sentence_like_text_to_subtitle() -> None:
    text = "Chapter 5\n\nThe first chapter begins here\n\nMore body text follows."

    sections = split_into_sections(text, chapter_splitting="auto")

    assert [section.title for section in sections] == ["Chapter 5"]
    assert sections[0].text == "The first chapter begins here\n\nMore body text follows."


def test_apply_pronunciation_overrides_replaces_case_insensitive_terms() -> None:
    updated = apply_pronunciation_overrides(
        "Lulu reads MLX and ChromaDB locally.",
        [("mlx", "M L X"), ("ChromaDB", "Chroma D B")],
    )

    assert updated == "Lulu reads M L X and Chroma D B locally."


def test_build_audiobook_from_args_dry_run_writes_manifest_and_text(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    write_text_pdf(
        pdf_path,
        [
            "Book Header\n1\nChapter 1\nHello world from Lulu.\n1",
            "Book Header\n2\nChapter 2\nSecond section for local audio.\n2",
        ],
        metadata={"Title": "Local PDF Book", "Author": "Lulu Tester"},
    )

    args = Namespace(
        input_pdf=str(pdf_path),
        title=None,
        author=None,
        genre="guide",
        output_dir=str(tmp_path / "exports"),
        chapter_splitting="auto",
        dry_run=True,
        portable_format="none",
        preview_chars=120,
        pronunciation_file=None,
    )

    artifacts = build_audiobook_from_args(args, progress=lambda _: None)

    assert artifacts.output_dir.name == "local-pdf-book"
    assert artifacts.manifest_path.exists()
    assert artifacts.full_text_path.exists()
    assert len(artifacts.section_text_paths) == 2
    manifest = artifacts.manifest_path.read_text(encoding="utf-8")
    assert '"ocr_status": "deferred"' in manifest
    assert '"genre": "guide"' in manifest
    assert '"audio_render": "not_requested"' in manifest
    assert artifacts.audio_paths == []


def test_render_section_audio_invokes_local_say(monkeypatch, tmp_path: Path) -> None:
    output_dir = tmp_path / "book"
    text_dir = output_dir / "text"
    audio_dir = output_dir / "audio"
    text_dir.mkdir(parents=True)
    audio_dir.mkdir()
    text_path = text_dir / "01-section.txt"
    text_path.write_text("Hello local audio.", encoding="utf-8")
    recorded_commands: list[list[str]] = []

    def fake_run(
        command: list[str],
        **_: object,
    ) -> SimpleNamespace:
        recorded_commands.append(command)
        Path(command[-1]).write_text("fake aiff", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("pdf_audiobook.subprocess.run", fake_run)

    audio_paths = render_section_audio([text_path], progress=lambda _: None)

    assert recorded_commands == [
        ["say", "-f", str(text_path), "-o", str(audio_dir / "01-section.aiff")]
    ]
    assert audio_paths == [audio_dir / "01-section.aiff"]


def test_render_section_audio_requires_created_file(monkeypatch, tmp_path: Path) -> None:
    output_dir = tmp_path / "book"
    text_dir = output_dir / "text"
    audio_dir = output_dir / "audio"
    text_dir.mkdir(parents=True)
    audio_dir.mkdir()
    text_path = text_dir / "01-section.txt"
    text_path.write_text("Hello local audio.", encoding="utf-8")

    def fake_run(
        command: list[str],
        **_: object,
    ) -> SimpleNamespace:
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("pdf_audiobook.subprocess.run", fake_run)

    with pytest.raises(Exception, match="no audio file was created"):
        render_section_audio([text_path], progress=lambda _: None)


def test_build_ffmpeg_command_uses_expected_codec_for_portable_format(tmp_path: Path) -> None:
    input_path = tmp_path / "chapter.aiff"
    output_path = tmp_path / "chapter.m4a"

    command = build_ffmpeg_command(
        ffmpeg_binary="/opt/homebrew/bin/ffmpeg",
        input_path=input_path,
        output_path=output_path,
        portable_format="m4a",
    )

    assert command == [
        "/opt/homebrew/bin/ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        str(output_path),
    ]


def test_convert_audio_outputs_requires_ffmpeg(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "chapter.aiff"
    audio_path.write_text("fake audio", encoding="utf-8")
    monkeypatch.setattr("pdf_audiobook.shutil.which", lambda _: None)

    with pytest.raises(Exception, match="requires ffmpeg"):
        convert_audio_outputs([audio_path], portable_format="wav", progress=lambda _: None)


def test_convert_audio_outputs_invokes_ffmpeg(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "chapter.aiff"
    audio_path.write_text("fake audio", encoding="utf-8")
    recorded_commands: list[list[str]] = []

    monkeypatch.setattr("pdf_audiobook.shutil.which", lambda _: "/usr/local/bin/ffmpeg")

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        recorded_commands.append(command)
        Path(command[-1]).write_text("fake wav", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("pdf_audiobook.subprocess.run", fake_run)

    portable_paths = convert_audio_outputs(
        [audio_path],
        portable_format="wav",
        progress=lambda _: None,
    )

    assert recorded_commands == [
        [
            "/usr/local/bin/ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-c:a",
            "pcm_s16le",
            str(tmp_path / "chapter.wav"),
        ]
    ]
    assert portable_paths == [tmp_path / "chapter.wav"]


def test_render_section_audio_reports_timeout(monkeypatch, tmp_path: Path) -> None:
    output_dir = tmp_path / "book"
    text_dir = output_dir / "text"
    audio_dir = output_dir / "audio"
    text_dir.mkdir(parents=True)
    audio_dir.mkdir()
    text_path = text_dir / "01-section.txt"
    text_path.write_text("Hello local audio.", encoding="utf-8")

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(command, timeout=5)

    monkeypatch.setattr("pdf_audiobook.subprocess.run", fake_run)

    with pytest.raises(Exception, match="timed out"):
        render_section_audio([text_path], progress=lambda _: None)


def test_convert_audio_outputs_reports_timeout(monkeypatch, tmp_path: Path) -> None:
    audio_path = tmp_path / "chapter.aiff"
    audio_path.write_text("fake audio", encoding="utf-8")
    monkeypatch.setattr("pdf_audiobook.shutil.which", lambda _: "/usr/local/bin/ffmpeg")

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(command, timeout=7)

    monkeypatch.setattr("pdf_audiobook.subprocess.run", fake_run)

    with pytest.raises(Exception, match="timed out"):
        convert_audio_outputs([audio_path], portable_format="wav", progress=lambda _: None)


def test_update_manifest_audio_outputs_records_portable_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "book"
    output_dir.mkdir()
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text('{"metadata": {"title": "Fixture"}}', encoding="utf-8")
    audio_path = output_dir / "audio" / "01-chapter.aiff"
    portable_path = output_dir / "audio" / "01-chapter.m4a"
    audio_path.parent.mkdir()
    audio_path.write_text("aiff", encoding="utf-8")
    portable_path.write_text("m4a", encoding="utf-8")

    update_manifest_audio_outputs(
        manifest_path=manifest_path,
        output_dir=output_dir,
        audio_paths=[audio_path],
        portable_audio_paths=[portable_path],
        portable_format="m4a",
        audio_render_status="succeeded",
        audio_render_error=None,
        portable_conversion_status="succeeded",
        portable_conversion_error=None,
    )

    manifest = manifest_path.read_text(encoding="utf-8")
    assert '"audio_render": "succeeded"' in manifest
    assert '"format": "m4a"' in manifest
    assert '"audio/01-chapter.aiff"' in manifest
    assert '"audio/01-chapter.m4a"' in manifest


def test_build_audiobook_from_args_reuses_existing_title_with_unique_directory(
    monkeypatch, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "sample.pdf"
    write_text_pdf(
        pdf_path,
        ["Chapter 1\nHello world from Lulu."],
        metadata={"Title": "Local PDF Book"},
    )

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        Path(command[-1]).write_text("fake aiff", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("pdf_audiobook.subprocess.run", fake_run)

    args = Namespace(
        input_pdf=str(pdf_path),
        title=None,
        author=None,
        genre=None,
        output_dir=str(tmp_path / "exports"),
        chapter_splitting="auto",
        dry_run=False,
        portable_format="none",
        preview_chars=120,
        pronunciation_file=None,
    )

    first = build_audiobook_from_args(args, progress=lambda _: None)
    second = build_audiobook_from_args(args, progress=lambda _: None)

    assert first.output_dir.name == "local-pdf-book"
    assert second.output_dir.name == "local-pdf-book-2"


def test_build_audiobook_from_args_non_dry_run_updates_manifest_audio_outputs(
    monkeypatch, tmp_path: Path
) -> None:
    pdf_path = tmp_path / "sample.pdf"
    write_text_pdf(
        pdf_path,
        ["Chapter 1\nHello world from Lulu."],
        metadata={"Title": "Playable Book"},
    )

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        Path(command[-1]).write_text("fake aiff", encoding="utf-8")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("pdf_audiobook.subprocess.run", fake_run)

    args = Namespace(
        input_pdf=str(pdf_path),
        title=None,
        author=None,
        genre=None,
        output_dir=str(tmp_path / "exports"),
        chapter_splitting="auto",
        dry_run=False,
        portable_format="none",
        preview_chars=120,
        pronunciation_file=None,
    )

    artifacts = build_audiobook_from_args(args, progress=lambda _: None)

    manifest = artifacts.manifest_path.read_text(encoding="utf-8")
    assert artifacts.audio_paths
    assert '"audio_render": "succeeded"' in manifest
    assert '"render_status": "succeeded"' in manifest
    assert '"audio/' in manifest


def test_play_export_directory_reads_text_when_audio_missing(monkeypatch, tmp_path: Path) -> None:
    export_dir = tmp_path / "book"
    text_dir = export_dir / "text"
    text_dir.mkdir(parents=True)
    text_path = text_dir / "01-section.txt"
    text_path.write_text("Hello from text export.", encoding="utf-8")
    (export_dir / "manifest.json").write_text("{}", encoding="utf-8")
    recorded_commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        recorded_commands.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("pdf_audiobook.subprocess.run", fake_run)

    played_paths, playback_mode = play_export_directory(
        export_dir,
        play_mode="auto",
        progress=lambda _: None,
    )

    assert playback_mode == "text"
    assert played_paths == [text_path]
    assert recorded_commands == [["say", "-f", str(text_path)]]


def test_play_export_directory_rejects_manifest_path_traversal(monkeypatch, tmp_path: Path) -> None:
    export_dir = tmp_path / "book"
    text_dir = export_dir / "text"
    text_dir.mkdir(parents=True)
    (text_dir / "01-section.txt").write_text("Hello from text export.", encoding="utf-8")
    outside_path = tmp_path / "outside.txt"
    outside_path.write_text("secret", encoding="utf-8")
    (export_dir / "manifest.json").write_text(
        json.dumps(
            {
                "audio_outputs": {
                    "portable_conversion": {
                        "files": ["../outside.txt"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    recorded_commands: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        recorded_commands.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("pdf_audiobook.subprocess.run", fake_run)

    played_paths, playback_mode = play_export_directory(
        export_dir,
        play_mode="auto",
        progress=lambda _: None,
    )

    assert playback_mode == "text"
    assert played_paths == [text_dir / "01-section.txt"]
    assert all(str(outside_path) not in command for command in recorded_commands)


def test_resolve_relative_paths_ignores_directory_entries(tmp_path: Path) -> None:
    export_dir = tmp_path / "book"
    text_dir = export_dir / "text"
    text_dir.mkdir(parents=True)
    valid_file = text_dir / "01-section.txt"
    valid_file.write_text("Hello from text export.", encoding="utf-8")

    resolved = _resolve_relative_paths(export_dir, [".", "", "text/01-section.txt"])

    assert resolved == [valid_file]


def test_play_export_directory_reports_malformed_manifest(tmp_path: Path) -> None:
    export_dir = tmp_path / "book"
    text_dir = export_dir / "text"
    text_dir.mkdir(parents=True)
    (text_dir / "01-section.txt").write_text("Hello from text export.", encoding="utf-8")
    (export_dir / "manifest.json").write_text("{bad json", encoding="utf-8")

    with pytest.raises(PlaybackError, match="manifest is malformed"):
        play_export_directory(
            export_dir,
            play_mode="auto",
            progress=lambda _: None,
        )


def test_play_export_directory_reports_timeout(monkeypatch, tmp_path: Path) -> None:
    export_dir = tmp_path / "book"
    text_dir = export_dir / "text"
    text_dir.mkdir(parents=True)
    text_path = text_dir / "01-section.txt"
    text_path.write_text("Hello from text export.", encoding="utf-8")
    (export_dir / "manifest.json").write_text("{}", encoding="utf-8")

    def fake_run(command: list[str], **_: object) -> SimpleNamespace:
        raise subprocess.TimeoutExpired(command, timeout=9)

    monkeypatch.setattr("pdf_audiobook.subprocess.run", fake_run)

    with pytest.raises(PlaybackError, match="timed out"):
        play_export_directory(
            export_dir,
            play_mode="auto",
            progress=lambda _: None,
        )


def test_play_export_directory_requires_audio_when_audio_mode_requested(tmp_path: Path) -> None:
    export_dir = tmp_path / "book"
    text_dir = export_dir / "text"
    text_dir.mkdir(parents=True)
    (text_dir / "01-section.txt").write_text("Hello from text export.", encoding="utf-8")
    (export_dir / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(PlaybackError, match="No generated audio files were found"):
        play_export_directory(
            export_dir,
            play_mode="audio",
            progress=lambda _: None,
        )


def test_main_dry_run_prints_preview(capsys, tmp_path: Path) -> None:
    pdf_path = tmp_path / "preview.pdf"
    write_text_pdf(
        pdf_path,
        ["Chapter 1\nPreview text for Lulu audiobook generation."],
    )

    exit_code = main(
        [
            str(pdf_path),
            "--title",
            "Preview Book",
            "--output-dir",
            str(tmp_path / "exports"),
            "--dry-run",
            "--preview-chars",
            "40",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "[lulu-pdf] Success:" in output
    assert "[lulu-pdf] Media files: 0" in output
    assert "[lulu-pdf] Preview:" in output
    assert "Preview text for Lulu audiobook generati" in output


def test_main_returns_nonzero_for_missing_file(capsys, tmp_path: Path) -> None:
    exit_code = main([str(tmp_path / "missing.pdf"), "--dry-run"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "[lulu-pdf] ERROR:" in output


def test_script_wrapper_smoke_test_with_real_fixture_pdf(tmp_path: Path) -> None:
    output_root = tmp_path / "exports"

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "pdf_to_audiobook.py"),
            str(FIXTURE_PDF),
            "--output-dir",
            str(output_root),
            "--dry-run",
            "--preview-chars",
            "80",
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "[lulu-pdf] Success:" in result.stdout
    assert "[lulu-pdf] Preview:" in result.stdout
    assert "Local audiobook smoke test page one." in result.stdout
    assert (output_root / "fixture-sample-book" / "manifest.json").exists()
