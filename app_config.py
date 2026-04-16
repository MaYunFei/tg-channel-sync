from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app_paths import config_file, ensure_runtime_dirs


DEFAULT_CONFIG: dict[str, Any] = {
    "telegram": {
        "bot_token": "",
        "extra_bot_tokens": [],
        "api_id": 0,
        "api_hash": "",
        "bot_api_base_url": "",
    },
    "proxy": {
        "enabled": False,
        "host": "127.0.0.1",
        "port": 7897,
        "username": "",
        "password": "",
    },
    "server": {
        "host": "127.0.0.1",
        "port": 8011,
        "auto_open_browser": False,
    },
    "sync": {
        "default_delay": 5,
        "force_send": False,
        "prefer_local_bot_api": True,
        "bot_upload_max_mb": 50,
        "bot_rate_limit_enabled": False,
        "bot_rate_limit_gb": 10,
        "bot_rate_limit_window_hours": 24,
        "bot_rate_limit_cooldown_minutes": 300,
    },
    "app": {
        "portable_mode": True,
        "log_level": "INFO",
    },
}


def _merge_dict(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    merged = _merge_dict(DEFAULT_CONFIG, config)
    telegram = merged["telegram"]
    proxy = merged["proxy"]
    server = merged["server"]
    sync = merged["sync"]

    telegram["api_id"] = int(str(telegram.get("api_id", 0) or 0).strip() or 0)
    telegram["api_hash"] = str(telegram.get("api_hash", "") or "").strip()
    telegram["bot_token"] = str(telegram.get("bot_token", "") or "").strip()
    telegram["bot_api_base_url"] = str(telegram.get("bot_api_base_url", "") or "").strip()
    extra_bot_tokens = telegram.get("extra_bot_tokens", [])
    if isinstance(extra_bot_tokens, str):
        extra_bot_tokens = [token.strip() for token in extra_bot_tokens.replace(",", "\n").splitlines() if token.strip()]
    elif isinstance(extra_bot_tokens, list):
        extra_bot_tokens = [str(token or "").strip() for token in extra_bot_tokens if str(token or "").strip()]
    else:
        extra_bot_tokens = []
    telegram["extra_bot_tokens"] = extra_bot_tokens

    proxy["enabled"] = bool(proxy.get("enabled", False))
    proxy["host"] = str(proxy.get("host", "") or "").strip()
    proxy["port"] = int(str(proxy.get("port", 7897) or 7897).strip() or 7897)
    proxy["username"] = str(proxy.get("username", "") or "").strip()
    proxy["password"] = str(proxy.get("password", "") or "").strip()

    server["host"] = str(server.get("host", "127.0.0.1") or "127.0.0.1").strip()
    server["port"] = int(str(server.get("port", 8011) or 8011).strip() or 8011)
    server["auto_open_browser"] = bool(server.get("auto_open_browser", False))

    sync["default_delay"] = max(0.5, float(sync.get("default_delay", 5) or 5))
    sync["force_send"] = bool(sync.get("force_send", False))
    sync["prefer_local_bot_api"] = bool(sync.get("prefer_local_bot_api", True))
    sync["bot_upload_max_mb"] = max(1.0, float(sync.get("bot_upload_max_mb", 50) or 50))
    sync["bot_rate_limit_enabled"] = bool(sync.get("bot_rate_limit_enabled", False))
    sync["bot_rate_limit_gb"] = max(0.1, float(sync.get("bot_rate_limit_gb", 10) or 10))
    sync["bot_rate_limit_window_hours"] = max(1.0, float(sync.get("bot_rate_limit_window_hours", 24) or 24))
    sync["bot_rate_limit_cooldown_minutes"] = max(1.0, float(sync.get("bot_rate_limit_cooldown_minutes", 300) or 300))
    return merged


def load_config() -> dict[str, Any]:
    ensure_runtime_dirs()
    cfg_path = config_file()
    if not cfg_path.exists():
        config = deepcopy(DEFAULT_CONFIG)
        save_config(config)
        return config

    try:
        config = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        config = deepcopy(DEFAULT_CONFIG)
    config = _normalize_config(config)
    save_config(config)
    return config


def save_config(config: dict[str, Any]) -> dict[str, Any]:
    ensure_runtime_dirs()
    normalized = _normalize_config(config)
    config_file().write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return normalized


def get_config() -> dict[str, Any]:
    return load_config()


def get_setup_status(config: dict[str, Any] | None = None) -> dict[str, Any]:
    config = config or load_config()
    telegram = config["telegram"]
    missing_fields: list[str] = []

    if not telegram["bot_token"]:
        missing_fields.append("telegram.bot_token")

    has_api_credentials = bool(telegram["api_id"] and telegram["api_hash"])
    first_run = (
        not telegram["bot_token"]
        and not has_api_credentials
        and not config["telegram"].get("bot_api_base_url")
    )
    return {
        "first_run": first_run,
        "needs_setup": len(missing_fields) > 0,
        "missing_fields": missing_fields,
        "has_bot_token": bool(telegram["bot_token"]),
        "has_api_credentials": has_api_credentials,
    }
