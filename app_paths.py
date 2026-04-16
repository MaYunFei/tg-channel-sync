from __future__ import annotations

import sys
from pathlib import Path


def app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def static_dir() -> Path:
    return app_root() / "static"


def config_file() -> Path:
    return app_root() / "config.json"


def version_file() -> Path:
    return app_root() / "VERSION"


def data_dir() -> Path:
    return app_root() / "data"


def temp_dir() -> Path:
    return app_root() / "temp"


def logs_dir() -> Path:
    return data_dir() / "logs"


def sessions_dir() -> Path:
    return data_dir() / "sessions"


def database_file() -> Path:
    return data_dir() / "data.db"


def pyrogram_user_session_base() -> Path:
    return sessions_dir() / "sync_user_session"


def ensure_runtime_dirs() -> None:
    for path in [data_dir(), temp_dir(), logs_dir(), sessions_dir()]:
        path.mkdir(parents=True, exist_ok=True)
