from __future__ import annotations

import logging

from app_config import get_config


LIBRARY_LOGGERS = (
    "aiogram",
    "pyrogram",
    "aiohttp",
    "asyncio",
    "httpx",
    "telegram",
    "urllib3",
    "tg-channel-sync",
)

NOISY_DEBUG_LOGGERS = (
    "aiosqlite",
    "pyrogram.session.session",
)


def configure_terminal_logging() -> None:
    app_cfg = get_config().get("app", {})
    debug_enabled = bool(app_cfg.get("debug_terminal_logs", False))
    configured_level = str(app_cfg.get("log_level", "INFO") or "INFO").upper()
    level = logging.DEBUG if debug_enabled else getattr(logging, configured_level, logging.INFO)

    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
    root_logger.setLevel(level)
    for logger_name in LIBRARY_LOGGERS:
        logging.getLogger(logger_name).setLevel(level)
    for logger_name in NOISY_DEBUG_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)
