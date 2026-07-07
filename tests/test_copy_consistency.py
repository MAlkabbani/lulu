from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_core_docs_use_current_pdf_audiobooks_name() -> None:
    for relative_path in (
        "README.md",
        "docs/README.md",
        "docs/pdf-audiobooks.md",
        "macos_app/README.md",
    ):
        content = _read(relative_path)
        assert "PDF To Audiobooks" not in content
        assert "PDF Audiobooks" in content


def test_operations_doc_does_not_use_stale_stage1_desktop_migration_copy() -> None:
    content = _read("docs/operations.md")

    assert (
        "Stage 1 of the desktop-app migration adds a local authenticated service boundary"
        not in content
    )
    assert (
        "local authenticated backend service is now the active shared service boundary"
        in content
    )
