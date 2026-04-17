from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Awaitable, Callable

import database as db


MESSAGE_LINK_RE = re.compile(r"https?://t\.me/(?:c/)?[^/\s]+/\d+")
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{2,}$")


def normalize_channel_username(channel_ref: str) -> str:
    ref = str(channel_ref or "").strip()
    if not ref:
        return ""

    if ref.startswith("@"):
        candidate = ref[1:]
    elif "t.me/" in ref.lower():
        normalized = ref.replace("\\", "/")
        candidate = normalized.split("t.me/", 1)[-1].split("?", 1)[0].split("#", 1)[0].split("/", 1)[0]
    else:
        candidate = ref

    candidate = candidate.strip().lstrip("@")
    if USERNAME_RE.fullmatch(candidate):
        return candidate
    return ""


def build_json_source_scope_id(source_username: str) -> int:
    normalized = normalize_channel_username(source_username)
    if not normalized:
        return 0
    digest = hashlib.sha1(normalized.lower().encode("utf-8")).hexdigest()[:15]
    return -int(digest, 16)


async def resolve_chat_id(bot, chat_ref: str) -> int:
    if bot is None:
        raise ValueError("BOT 未配置或未连接，无法解析频道")

    chat_ref = str(chat_ref or "").strip()
    if not chat_ref:
        raise ValueError("频道引用为空")
    if chat_ref.lstrip("-").isdigit():
        return int(chat_ref)
    if "t.me/" in chat_ref:
        username = chat_ref.split("t.me/")[-1].split("/")[0].split("?")[0]
        chat_ref = f"@{username.lstrip('@')}"
    if not chat_ref.startswith("@"):
        chat_ref = f"@{chat_ref}"
    try:
        return int((await bot.get_chat(chat_ref)).id)
    except Exception as exc:
        raise ValueError(f"无法解析频道 {chat_ref}: {exc}") from exc


def get_quote_payload(message):
    quote = getattr(message, "quote", None)
    if not quote or not getattr(quote, "text", None):
        return None
    return {
        "text": quote.text,
        "position": getattr(quote, "position", None),
        "entities": getattr(quote, "entities", None),
    }


async def resolve_reply_target(
    source_id: int,
    target_id: int,
    reply_source_msg_id: int | None,
    mode_label: str,
    current_msg_id: int,
):
    if not reply_source_msg_id:
        return None
    target_reply_id = await db.get_target_msg_id(source_id, reply_source_msg_id, target_id)
    if target_reply_id:
        await db.add_msg_log(
            f"{mode_label}_REPLY_MAP",
            f"消息ID:{current_msg_id} | 回复源消息:{reply_source_msg_id} -> 目标消息:{target_reply_id}",
        )
    else:
        await db.add_msg_log(
            f"{mode_label}_REPLY_FALLBACK",
            f"消息ID:{current_msg_id} | 被回复消息:{reply_source_msg_id} 未同步，按普通消息发送",
        )
    return target_reply_id


async def resolve_reply_for_forward(
    source_id: int,
    target_id: int,
    current_msg_id: int,
    reply_source_msg_id: int | None,
):
    if not reply_source_msg_id:
        return None
    target_reply_id = await db.get_target_msg_id(source_id, reply_source_msg_id, target_id)
    if target_reply_id:
        await db.add_msg_log(
            "REPLY_MAP",
            f"源频道:{source_id} | 目标频道:{target_id} | 消息ID:{current_msg_id} | 已映射回复目标:{target_reply_id}",
        )
    else:
        await db.add_msg_log(
            "REPLY_FALLBACK",
            f"源频道:{source_id} | 目标频道:{target_id} | 消息ID:{current_msg_id} | 被回复消息ID:{reply_source_msg_id} 尚未同步，按普通消息发送",
        )
    return target_reply_id


async def build_link_rewrite_context(bot, source_id, target_id, source_username_override=None):
    if bot is None:
        return None

    context = {"source_id": source_id, "target_id": target_id}
    try:
        source_chat = await bot.get_chat(source_id) if source_id != 0 else None
        target_chat = await bot.get_chat(target_id)
    except Exception:
        source_chat = None
        target_chat = None

    source_username = source_username_override or (getattr(source_chat, "username", None) if source_chat else None)
    target_username = getattr(target_chat, "username", None) if target_chat else None
    context["source_username"] = str(source_username).lstrip("@") if source_username else None
    context["target_username"] = str(target_username).lstrip("@") if target_username else None
    return context


def _replace_msg_link(match, target_channel_ref: str, mapped_msg_id: int) -> str:
    suffix = match.group("suffix") or ""
    return f"{target_channel_ref}/{mapped_msg_id}{suffix}"


async def rewrite_message_links(text_html, source_id, link_context):
    if not text_html or not link_context:
        return text_html, 0

    updated_html = text_html
    rewrite_count = 0
    target_internal_id = str(abs(link_context["target_id"])).removeprefix("100")

    def has_internal_channel_id(chat_id: int) -> bool:
        return str(abs(int(chat_id or 0))).startswith("100")

    async def replace_pattern(pattern, target_channel_ref):
        nonlocal updated_html, rewrite_count
        for match in list(pattern.finditer(updated_html)):
            source_msg_id = int(match.group("msg_id"))
            target_msg_id = await db.get_target_msg_id(source_id, source_msg_id, link_context["target_id"])
            if not target_msg_id:
                continue
            original = match.group(0)
            replaced = _replace_msg_link(match, target_channel_ref, target_msg_id)
            if original == replaced:
                continue
            updated_html = updated_html.replace(original, replaced)
            rewrite_count += 1

    if source_id != 0 and has_internal_channel_id(source_id):
        source_internal_id = str(abs(source_id)).removeprefix("100")
        await replace_pattern(
            re.compile(rf"(?P<prefix>https?://t\.me/c/{re.escape(source_internal_id)}/)(?P<msg_id>\d+)(?P<suffix>\b)"),
            f"https://t.me/c/{target_internal_id}",
        )

    if link_context.get("source_username"):
        target_channel_ref = (
            f"https://t.me/{link_context['target_username']}"
            if link_context.get("target_username")
            else f"https://t.me/c/{target_internal_id}"
        )
        await replace_pattern(
            re.compile(rf"(?P<prefix>https?://t\.me/{re.escape(link_context['source_username'])}/)(?P<msg_id>\d+)(?P<suffix>\b)"),
            target_channel_ref,
        )

    return updated_html, rewrite_count


def compute_progress_speed(downloaded: int, previous_downloaded: int, elapsed_seconds: float) -> float:
    if elapsed_seconds <= 0:
        return 0.0
    delta = max(0, downloaded - previous_downloaded)
    return delta / elapsed_seconds / (1024 * 1024)


def create_progress_callback(action_name: str, sync_state: dict):
    last_state = {"time": 0.0, "downloaded": 0}

    def progress(downloaded, total_bytes):
        if sync_state.get("stop_requested"):
            raise Exception("STOP_REQUESTED")
        now = time.time()
        elapsed = now - last_state["time"]
        if elapsed > 0.5 or downloaded == total_bytes:
            speed_mb = compute_progress_speed(downloaded, last_state["downloaded"], elapsed if last_state["time"] else 0)
            if total_bytes > 0:
                percent = downloaded / total_bytes * 100
                sync_state["current_text"] = f"{action_name} {percent:.1f}% ({speed_mb:.1f} MB/s)"
            else:
                sync_state["current_text"] = action_name
            last_state["time"] = now
            last_state["downloaded"] = downloaded

    return progress


async def safe_execute(coro, sync_state: dict):
    task = asyncio.create_task(coro)
    while not task.done():
        if sync_state.get("stop_requested"):
            task.cancel()
            raise Exception("STOP_REQUESTED")
        await asyncio.sleep(0.2)
    try:
        return await task
    except asyncio.CancelledError as exc:
        raise Exception("STOP_REQUESTED") from exc


async def log_sync_error(prefix: str, exc: Exception):
    await db.add_log("ERROR", f"{prefix}: {exc}")
