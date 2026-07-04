from __future__ import annotations

from pathlib import Path

from app_core import app_paths


def test_repo_mode_paths_are_repo_local(monkeypatch) -> None:
    monkeypatch.delenv(app_paths.PATH_MODE_ENV, raising=False)

    assert app_paths.detect_path_mode() == "repo"
    assert app_paths.default_chroma_path() == app_paths.repo_root() / "vault_db"
    assert app_paths.default_logs_path() == app_paths.repo_root() / "logs"
    assert app_paths.default_exports_path() == app_paths.repo_root() / "exports"


def test_app_support_mode_paths_use_library_defaults(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(app_paths.PATH_MODE_ENV, "app_support")
    monkeypatch.setenv(app_paths.APP_SUPPORT_DIR_ENV, str(tmp_path / "Application Support" / "Lulu"))
    monkeypatch.setenv(app_paths.CACHE_DIR_ENV, str(tmp_path / "Caches" / "Lulu"))

    assert app_paths.detect_path_mode() == "app_support"
    assert app_paths.default_config_path() == tmp_path / "Application Support" / "Lulu" / "config.json"
    assert app_paths.default_chroma_path() == tmp_path / "Application Support" / "Lulu" / "chroma"
    assert app_paths.default_logs_path() == tmp_path / "Application Support" / "Lulu" / "logs"
    assert app_paths.default_exports_path() == tmp_path / "Application Support" / "Lulu" / "exports"
    assert app_paths.cache_dir() == tmp_path / "Caches" / "Lulu"

