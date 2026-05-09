from __future__ import annotations

import asyncio
import os
import re

import database as db
from services.sync_services import execute_with_network_retry, safe_execute

from ..runtime import TEMP_DIR, sync_state


RETRY_AFTER_RE = re.compile(r"retry after\s+(?P<seconds>\d+)", re.IGNORECASE)
WAIT_REQUIRED_RE = re.compile(r"wait of\s+(?P<seconds>\d+)\s+seconds?\s+is required", re.IGNORECASE)


def _parse_retry_after_seconds(exc: Exception) -> int | None:
    text = str(exc)
    match = RETRY_AFTER_RE.search(text) or WAIT_REQUIRED_RE.search(text)
    if not match:
        return None
    return max(1, int(match.group("seconds")))


def _is_request_entity_too_large(exc: Exception) -> bool:
    return "request entity too large" in str(exc).lower()


def _is_topics_parse_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "topics" in text and "messages.__init__" in text


def _is_chat_forwards_restricted(exc: Exception) -> bool:
    text = str(exc).lower()
    return "chat_forwards_restricted" in text or "restricts forwarding content" in text


def _build_temp_download_path(msg, msg_type: str) -> str:
    media_obj = getattr(msg, msg_type, None)
    original_name = str(getattr(media_obj, "file_name", "") or "").strip()
    safe_name = re.sub(r'[<>:"/\\|?*]+', "_", original_name)
    base_name, ext = os.path.splitext(safe_name)
    if not base_name:
        default_ext = {
            "photo": ".jpg",
            "video": ".mp4",
            "animation": ".mp4",
            "audio": ".mp3",
            "voice": ".ogg",
            "sticker": ".webp",
            "document": "",
        }
        ext = ext or default_ext.get(msg_type, "")
        base_name = f"{msg_type}_{getattr(msg, 'id', 'media')}"
    return os.path.join(TEMP_DIR, f"{getattr(msg, 'id', 'media')}_{base_name}{ext}")


def _get_media_thumbs(msg, msg_type: str):
    media_obj = getattr(msg, msg_type, None)
    thumbs = getattr(media_obj, "thumbs", None) if media_obj else None
    return thumbs or []


def _build_temp_thumbnail_path(msg, msg_type: str) -> str:
    return os.path.join(TEMP_DIR, f"{getattr(msg, 'id', 'media')}_{msg_type}_thumb.jpg")


async def _download_media_thumbnail(app, msg, msg_type: str) -> str | None:
    thumbs = _get_media_thumbs(msg, msg_type)
    if not thumbs:
        return None
    thumb = thumbs[-1]
    thumb_ref = getattr(thumb, "file_id", None)
    if not thumb_ref:
        return None
    thumb_path = _build_temp_thumbnail_path(msg, msg_type)
    try:
        downloaded = await safe_execute(app.download_media(thumb_ref, file_name=thumb_path), sync_state)
    except Exception:
        return None
    return downloaded if isinstance(downloaded, str) and os.path.exists(downloaded) else None


async def _execute_with_clone_retry(coro_factory, *, action_label: str):
    return await _execute_with_clone_retry_interruptibly(coro_factory, action_label=action_label, stop_client=None)


async def _execute_with_clone_retry_interruptibly(coro_factory, *, action_label: str, stop_client=None):
    while True:
        task = asyncio.create_task(
            execute_with_network_retry(
                coro_factory,
                action_label=action_label,
                sync_state=sync_state,
                log_tag="CLONE_NETWORK_RETRY",
            )
        )
        try:
            while not task.done():
                if sync_state.get("stop_requested"):
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
            if task.done():
                try:
                    await task
                except Exception as task_exc:
                    exc = task_exc
            else:
                await asyncio.gather(task, return_exceptions=True)
            if sync_state.get("stop_requested"):
                raise
            retry_after = _parse_retry_after_seconds(exc)
            if retry_after is None:
                raise
            wait_seconds = retry_after + 1
            sync_state["current_text"] = f"等待重试\n上传阶段触发频控，需等待 {wait_seconds} 秒"
            await db.add_msg_log(
                "CLONE_RETRY",
                f"{action_label} | 上传阶段遇到频控，等待 {wait_seconds} 秒后重试；若当前走 Bot 发送，可切换为辅助账号继续发送",
            )
            await asyncio.sleep(wait_seconds)


def _clone_should_fallback_to_user(sender: str, clone_fallback_to_user: bool) -> bool:
    return sender == "bot" and bool(clone_fallback_to_user)
