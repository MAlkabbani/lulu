from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "Lulu"
PATH_MODE_ENV = "LULU_PATH_MODE"
CONFIG_PATH_ENV = "LULU_CONFIG_PATH"
APP_SUPPORT_DIR_ENV = "LULU_APP_SUPPORT_DIR"
CACHE_DIR_ENV = "LULU_CACHE_DIR"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def detect_path_mode() -> str:
    raw = (os.getenv(PATH_MODE_ENV) or "repo").strip().lower()
    if raw == "app_support":
        return "app_support"
    return "repo"


def app_support_dir() -> Path:
    configured = os.getenv(APP_SUPPORT_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Library" / "Application Support" / APP_NAME


def cache_dir() -> Path:
    configured = os.getenv(CACHE_DIR_ENV)
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Library" / "Caches" / APP_NAME


def packaged_writable_roots() -> tuple[Path, Path]:
    return app_support_dir(), cache_dir()


def default_config_path() -> Path:
    configured = os.getenv(CONFIG_PATH_ENV)
    if configured:
        return Path(configured).expanduser()
    if detect_path_mode() == "app_support":
        return app_support_dir() / "config.json"
    return repo_root() / ".env.json"


def default_chroma_path() -> Path:
    if detect_path_mode() == "app_support":
        return app_support_dir() / "chroma"
    return repo_root() / "vault_db"


def default_logs_path() -> Path:
    if detect_path_mode() == "app_support":
        return app_support_dir() / "logs"
    return repo_root() / "logs"


def default_exports_path() -> Path:
    if detect_path_mode() == "app_support":
        return app_support_dir() / "exports"
    return repo_root() / "exports"
