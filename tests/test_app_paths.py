from __future__ import annotations

from pathlib import Path

from app_core import app_paths


def test_repo_mode_paths_are_repo_local(monkeypatch) -> None:
    monkeypatch.delenv(app_paths.PATH_MODE_ENV, raising=False)

    assert app_paths.detect_path_mode() == "repo"
    assert app_paths.default_chroma_path() == app_paths.repo_root() / "vault_db"
    assert app_paths.default_logs_path() == app_paths.repo_root() / "logs"
    assert app_paths.default_exports_path() == app_paths.repo_root() / "exports"


def test_unknown_path_mode_falls_back_to_repo(monkeypatch) -> None:
    monkeypatch.setenv(app_paths.PATH_MODE_ENV, "bundle-ish")

    assert app_paths.detect_path_mode() == "repo"


def test_app_support_mode_paths_use_library_defaults(monkeypatch, tmp_path: Path) -> None:
    app_support_root = tmp_path / "Application Support" / "Lulu"
    cache_root = tmp_path / "Caches" / "Lulu"

    monkeypatch.setenv(app_paths.PATH_MODE_ENV, "app_support")
    monkeypatch.setenv(app_paths.APP_SUPPORT_DIR_ENV, str(app_support_root))
    monkeypatch.setenv(app_paths.CACHE_DIR_ENV, str(cache_root))

    assert app_paths.detect_path_mode() == "app_support"
    assert app_paths.default_config_path() == app_support_root / "config.json"
    assert app_paths.default_chroma_path() == app_support_root / "chroma"
    assert app_paths.default_logs_path() == app_support_root / "logs"
    assert app_paths.default_exports_path() == app_support_root / "exports"
    assert app_paths.cache_dir() == cache_root
    assert app_paths.packaged_writable_roots() == (app_support_root, cache_root)


def test_explicit_config_path_override_beats_path_mode(monkeypatch, tmp_path: Path) -> None:
    custom_config_path = tmp_path / "custom" / "lulu.json"

    monkeypatch.setenv(app_paths.PATH_MODE_ENV, "app_support")
    monkeypatch.setenv(app_paths.CONFIG_PATH_ENV, str(custom_config_path))

    assert app_paths.default_config_path() == custom_config_path


def test_packaged_writable_roots_expand_user_overrides(monkeypatch) -> None:
    monkeypatch.setenv(app_paths.APP_SUPPORT_DIR_ENV, "~/Library/Application Support/CustomLulu")
    monkeypatch.setenv(app_paths.CACHE_DIR_ENV, "~/Library/Caches/CustomLulu")

    app_support_root, cache_root = app_paths.packaged_writable_roots()

    assert app_support_root == Path.home() / "Library" / "Application Support" / "CustomLulu"
    assert cache_root == Path.home() / "Library" / "Caches" / "CustomLulu"
