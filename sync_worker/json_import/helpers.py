from __future__ import annotations

import asyncio
import os
import re
import time

from aiogram.types import FSInputFile

import bot_engine
import database as db
from services.sync_services import execute_with_network_retry, safe_execute

from ..runtime import sync_state
from ..senders import resolve_upload_target, should_fallback_to_user


JSON_MEDIA_GROUP_WINDOW_SECONDS = 3
RETRY_AFTER_PATTERNS = (
    re.compile(r"retry after\s+(?P<seconds>\d+)", re.IGNORECASE),
    re.compile(r"a wait of\s+(?P<seconds>\d+)\s+seconds?\s+is required", re.IGNORECASE),
    re.compile(r"flood_wait(?:_x)?[^\d]*(?P<seconds>\d+)", re.IGNORECASE),
)
JSON_UPLOAD_LABELS = {
    "photo": "上传图片",
    "video": "上传视频",
    "animation": "上传动图",
    "audio": "上传音频",
    "voice": "上传语音",
    "sticker": "上传贴纸",
    "document": "上传文件",
}


class JsonSyncFatalError(RuntimeError):
    pass


class UploadProgressTracker:
    def __init__(self, label: str, total_bytes: int):
        self.label = label
        self.total_bytes = max(1, int(total_bytes or 0))
        self.sent_bytes = 0
        self.started_at = time.time()
        self.last_update_at = 0.0

    def advance(self, chunk_size: int, file_label: str) -> None:
        self.sent_bytes += max(0, int(chunk_size or 0))
        now = time.time()
        if self.sent_bytes < self.total_bytes and now - self.last_update_at < 0.2:
            return
        elapsed = max(0.001, now - self.started_at)
        percent = min(100.0, self.sent_bytes / self.total_bytes * 100)
        sent_mb = self.sent_bytes / (1024 * 1024)
        total_mb = self.total_bytes / (1024 * 1024)
        speed_mb = sent_mb / elapsed
        sync_state["current_text"] = (
            f"{file_label}\n{self.label} {percent:.1f}% ({sent_mb:.1f}/{total_mb:.1f} MB, {speed_mb:.1f} MB/s)"
        )
        self.last_update_at = now


class ProgressFSInputFile(FSInputFile):
    def __init__(self, path: str, tracker: UploadProgressTracker, file_label: str, filename: str | None = None):
        super().__init__(path, filename=filename)
        self.tracker = tracker
        self.file_label = file_label

    async def read(self, bot):
        async for chunk in super().read(bot):
            self.tracker.advance(len(chunk), self.file_label)
            yield chunk


def _format_media_label(media_type: str | None, media_path: str, *, index: int | None = None, total: int | None = None) -> str:
    action = JSON_UPLOAD_LABELS.get(str(media_type or ""), "上传媒体")
    name = os.path.basename(media_path)
    if index is not None and total is not None:
        return f"{action} {index}/{total}: {name}"
    return f"{action}: {name}"


def _parse_retry_after_seconds(exc: Exception) -> int | None:
    text = str(exc)
    for pattern in RETRY_AFTER_PATTERNS:
        match = pattern.search(text)
        if match:
            return max(1, int(match.group("seconds")))
    return None


def _is_request_entity_too_large(exc: Exception) -> bool:
    return "request entity too large" in str(exc).lower()


def _is_topics_parse_error(exc: Exception) -> bool:
    return "topics" in str(exc).lower() and "messages.__init__" in str(exc).lower()


def _json_should_fallback_to_user(sender: str, clone_fallback_to_user: bool) -> bool:
    return sender == "bot" and bool(clone_fallback_to_user)


async def _sleep_retry_delay(seconds: int) -> None:
    await asyncio.sleep(seconds)


async def _execute_with_retry(
    coro_factory,
    *,
    action_label: str,
    max_attempts: int = 3,
    retry_unknown_errors: bool = True,
    stop_client=None,
):
    attempt = 0
    while True:
        attempt += 1
        try:
            task = asyncio.create_task(
                execute_with_network_retry(
                    coro_factory,
                    action_label=action_label,
                    sync_state=sync_state,
                    log_tag="JSON_NETWORK_RETRY",
                )
            )
            while not task.done():
                if sync_state["stop_requested"]:
                    if stop_client is not None and hasattr(stop_client, "stop_transmission"):
                        try:
                            stop_client.stop_transmission()
                        except Exception:
                            pass
                    task.cancel()
                    raise Exception("STOP_REQUESTED")
                await asyncio.sleep(0.2)
            return await task
        except Exception as exc:
            if "task" in locals() and not task.done():
                await asyncio.gather(task, return_exceptions=True)
            if sync_state["stop_requested"]:
                raise
            retry_after = _parse_retry_after_seconds(exc)
            if retry_after is not None:
                await db.add_msg_log("JSON_RETRY", f"{action_label} | 遇到频控，等待 {retry_after + 1} 秒后重试")
                await _sleep_retry_delay(retry_after + 1)
                continue
            if not retry_unknown_errors:
                raise
            if attempt >= max_attempts:
                raise
            await asyncio.sleep(2)


async def _select_json_upload_target(
    sender: str,
    file_sizes,
    *,
    clone_fallback_to_user: bool,
    wait_for_available_bot: bool = True,
):
    return await safe_execute(
        resolve_upload_target(
            sender,
            bot_engine.pyro_user_app,
            file_sizes,
            allow_user_fallback=should_fallback_to_user(sender, clone_fallback_to_user),
            wait_for_available_bot=wait_for_available_bot,
        ),
        sync_state,
    )
