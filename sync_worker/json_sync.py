from __future__ import annotations

import asyncio
import json
import os
import re
import time

from aiogram.types import FSInputFile
from aiogram.types import InputMediaAudio as AioMediaAudio
from aiogram.types import InputMediaDocument as AioMediaDocument
from aiogram.types import InputMediaPhoto as AioMediaPhoto
from aiogram.types import InputMediaVideo as AioMediaVideo
from pyrogram.enums import ParseMode
from pyrogram.types import InputMediaAudio
from pyrogram.types import InputMediaDocument
from pyrogram.types import InputMediaPhoto
from pyrogram.types import InputMediaVideo

import bot_engine
import database as db
from services.sync_services import (
    MESSAGE_LINK_RE,
    build_link_rewrite_context,
    build_json_source_scope_id,
    log_sync_error,
    normalize_channel_username,
    resolve_chat_id,
    resolve_reply_target,
    rewrite_message_links,
    safe_execute,
)
from .common import (
    UploadProgressTracker as SharedUploadProgressTracker,
    build_json_text,
    build_pyro_progress_callback,
    get_msg_meta,
    get_reply_source_msg_id,
    resolve_clone_upload_target,
    resolve_json_media,
)
from .state import record_success, sync_state, update_state_and_check_skip


JSON_GROUP_MEDIA_TYPES = {"photo", "video", "animation", "audio", "document"}
JSON_MEDIA_GROUP_WINDOW_SECONDS = 3
RETRY_AFTER_RE = re.compile(r"retry after\s+(?P<seconds>\d+)", re.IGNORECASE)
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


PYRO_JSON_MEDIA_CLS = {
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
    "animation": InputMediaVideo,
    "audio": InputMediaAudio,
    "document": InputMediaDocument,
}


def _json_message_timestamp(msg: dict) -> int:
    try:
        return int(msg.get("date_unixtime") or 0)
    except (TypeError, ValueError):
        return 0


def _json_group_family(msg: dict) -> str | None:
    msg_type, _ = get_msg_meta(msg, "json")
    if msg_type in {"photo", "video", "animation"}:
        return "visual"
    if msg_type == "audio":
        return "audio"
    if msg_type == "document":
        return "document"
    return None


def _format_media_label(media_type: str | None, media_path: str, *, index: int | None = None, total: int | None = None) -> str:
    action = JSON_UPLOAD_LABELS.get(str(media_type or ""), "上传媒体")
    name = os.path.basename(media_path)
    if index is not None and total is not None:
        return f"{action} {index}/{total}: {name}"
    return f"{action}: {name}"


def _parse_retry_after_seconds(exc: Exception) -> int | None:
    match = RETRY_AFTER_RE.search(str(exc))
    if not match:
        return None
    return max(1, int(match.group("seconds")))


def _is_request_entity_too_large(exc: Exception) -> bool:
    return "request entity too large" in str(exc).lower()


def _json_should_fallback_to_user(sender: str, clone_fallback_to_user: bool) -> bool:
    return sender == "bot" and bool(clone_fallback_to_user)


async def _execute_with_retry(coro_factory, *, action_label: str, max_attempts: int = 3):
    attempt = 0
    while True:
        attempt += 1
        try:
            return await safe_execute(coro_factory(), sync_state)
        except Exception as exc:
            if sync_state["stop_requested"]:
                raise
            retry_after = _parse_retry_after_seconds(exc)
            if retry_after is not None:
                await db.add_msg_log("JSON_RETRY", f"{action_label} | 遇到频控，等待 {retry_after + 1} 秒后重试")
                await asyncio.sleep(retry_after + 1)
                continue
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
        resolve_clone_upload_target(
            sender,
            bot_engine.pyro_user_app,
            file_sizes,
            allow_user_fallback=_json_should_fallback_to_user(sender, clone_fallback_to_user),
            wait_for_available_bot=wait_for_available_bot,
        ),
        sync_state,
    )


def _pyro_file_ref(media_path: str) -> str:
    return media_path


async def _send_json_single_via_user(target_id, media_type, media_path, caption, reply_to_id, tracker, file_label):
    app = bot_engine.pyro_user_app
    if not getattr(app, "is_initialized", False):
        raise JsonSyncFatalError("Bot 上传体积超限，且辅助账号未登录，无法回退重传")
    return await _execute_with_retry(
        lambda: getattr(app, f"send_{media_type}", app.send_document)(
            chat_id=target_id,
            **({"sticker": _pyro_file_ref(media_path)} if media_type == "sticker" else {media_type if hasattr(app, f"send_{media_type}") else "document": _pyro_file_ref(media_path)}),
            **({} if media_type == "sticker" else {"caption": caption, "parse_mode": ParseMode.HTML}),
            **({"reply_to_message_id": reply_to_id} if reply_to_id else {}),
            progress=build_pyro_progress_callback(tracker, file_label, total_bytes=os.path.getsize(media_path)),
        ),
        action_label=f"消息 -> 辅助账号重传 [{os.path.basename(media_path)}]",
    )   


async def _send_json_text_via_user(target_id, text, reply_to_id):
    app = bot_engine.pyro_user_app
    if not getattr(app, "is_initialized", False):
        raise JsonSyncFatalError("Bot 发送失败，且辅助账号未登录，无法回退发送文本消息")
    return await _execute_with_retry(
        lambda: app.send_message(
            chat_id=target_id,
            text=text,
            parse_mode=ParseMode.HTML,
            **({"reply_to_message_id": reply_to_id} if reply_to_id else {}),
        ),
        action_label="文本消息 -> 辅助账号发送",
    )


async def _send_json_group_via_user(group, target_id, rewritten_captions, file_entries, reply_to_id):
    app = bot_engine.pyro_user_app
    if not getattr(app, "is_initialized", False):
        raise JsonSyncFatalError("Bot 上传体积超限，且辅助账号未登录，无法回退发送媒体组")
    total_bytes = sum(os.path.getsize(path) for _, path, _ in file_entries)
    tracker = SharedUploadProgressTracker("上传媒体组 [辅助账号回退]", total_bytes)
    media = []
    group_family = _json_group_family(group[0])
    for index, ((item, media_path, media_type), caption_html) in enumerate(zip(file_entries, rewritten_captions), start=1):
        media_cls = PYRO_JSON_MEDIA_CLS.get(media_type, InputMediaDocument)
        caption = caption_html if caption_html else None
        if group_family == "visual" and index > 1:
            caption = None
        media.append(
            media_cls(
                media=media_path,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        )
    kwargs = {"chat_id": target_id, "media": media}
    if reply_to_id:
        kwargs["reply_to_message_id"] = reply_to_id
    kwargs["progress"] = build_pyro_progress_callback(
        tracker,
        f"上传媒体组: {len(file_entries)} 项",
        total_bytes=total_bytes,
    )
    return await _execute_with_retry(
        lambda: app.send_media_group(**kwargs),
        action_label=f"媒体组 -> 辅助账号重传 [{len(file_entries)} 项]",
    )


def _json_can_group_media(msg: dict) -> bool:
    if msg.get("type") != "message":
        return False
    return (_json_group_family(msg) or "") in {"visual", "audio", "document"}


def _json_has_caption(msg: dict) -> bool:
    return bool(str(build_json_text(msg) or "").strip())


def _json_should_append_to_heuristic_group(group: list[dict], msg: dict, window_seconds: int) -> bool:
    if not group or not _json_can_group_media(msg):
        return False
    if _json_group_family(group[0]) != _json_group_family(msg):
        return False
    prev = group[-1]
    prev_id = int(prev.get("id") or 0)
    curr_id = int(msg.get("id") or 0)
    if curr_id != prev_id + 1:
        return False
    if msg.get("reply_to_message_id"):
        return False
    prev_ts = _json_message_timestamp(prev)
    curr_ts = _json_message_timestamp(msg)
    if prev_ts and curr_ts and curr_ts - prev_ts > max(1, int(window_seconds or JSON_MEDIA_GROUP_WINDOW_SECONDS)):
        return False
    if _json_group_family(msg) != "visual":
        return True
    caption_count = sum(1 for item in group if _json_has_caption(item))
    if _json_has_caption(msg) and caption_count >= 1:
        return False
    return True


def group_json_messages(messages: list[dict], window_seconds: int) -> list[list[dict]]:
    grouped = []
    current_heuristic_group: list[dict] = []

    def flush_heuristic_group():
        nonlocal current_heuristic_group
        if not current_heuristic_group:
            return
        if len(current_heuristic_group) == 1:
            grouped.append([current_heuristic_group[0]])
        else:
            grouped.append(current_heuristic_group)
        current_heuristic_group = []

    for msg in messages:
        explicit_group_id = msg.get("media_group_id") or msg.get("grouped_id") or msg.get("media_group")
        if explicit_group_id:
            flush_heuristic_group()
            if grouped and len(grouped[-1]) > 0:
                prev_explicit = grouped[-1][0].get("media_group_id") or grouped[-1][0].get("grouped_id") or grouped[-1][0].get("media_group")
                if prev_explicit == explicit_group_id:
                    grouped[-1].append(msg)
                    continue
            grouped.append([msg])
            continue

        if _json_should_append_to_heuristic_group(current_heuristic_group, msg, window_seconds):
            current_heuristic_group.append(msg)
            continue

        flush_heuristic_group()
        if _json_can_group_media(msg):
            current_heuristic_group = [msg]
        else:
            grouped.append([msg])

    flush_heuristic_group()
    return grouped


async def send_json_media_group(group, target_id, json_dir, source_scope_id, force_send, link_context, sender: str, clone_fallback_to_user: bool):
    group_ids = [int(item.get("id") or 0) for item in group]
    first_msg = group[0]
    first_id = group_ids[0] if group_ids else 0
    if await update_state_and_check_skip(source_scope_id, target_id, first_id, "[JSON媒体组]", force_send=force_send):
        return

    media_list = []
    sent_ids = []
    rewritten_captions = []
    total_bytes = 0
    group_family = _json_group_family(first_msg)

    file_entries = []
    for index, item in enumerate(group):
        item_id = int(item.get("id") or 0)
        media_path, media_type, _ = resolve_json_media(item, json_dir)
        if not media_path or not os.path.exists(media_path):
            await db.add_msg_log("JSON_MEDIA_MISSING", f"消息ID:{item_id} | 媒体组文件不存在，已回退为逐条发送")
            return None
        total_bytes += os.path.getsize(media_path)
        file_entries.append((item, media_path, media_type))

        caption_html = build_json_text(item)
        caption_html, rewrite_count = await rewrite_message_links(caption_html, source_scope_id, link_context)
        if rewrite_count:
            await db.add_msg_log("JSON_LINK_REWRITE", f"消息ID:{item_id} | 命中 {rewrite_count} 个链接改写")
        rewritten_captions.append(caption_html)

    reply_to_id = await resolve_reply_target(
        source_scope_id,
        target_id,
        get_reply_source_msg_id(first_msg, "json"),
        "JSON",
        first_id,
    )
    file_sizes = [os.path.getsize(path) for _, path, _ in file_entries]
    upload_target = await _select_json_upload_target(
        sender,
        file_sizes,
        clone_fallback_to_user=clone_fallback_to_user,
        wait_for_available_bot=not _json_should_fallback_to_user(sender, clone_fallback_to_user),
    )
    sent_group = None

    if upload_target["sender"] == "user":
        sent_group = await _send_json_group_via_user(group, target_id, rewritten_captions, file_entries, reply_to_id)
    else:
        for _ in range(3):
            if sync_state["stop_requested"]:
                break
            tracker = UploadProgressTracker(f"上传媒体组 [{upload_target['label']}]", total_bytes)
            media_list = []
            for index, (item, media_path, media_type) in enumerate(file_entries):
                caption_html = rewritten_captions[index]
                caption = caption_html if caption_html else None
                if group_family == "visual" and index > 0:
                    caption = None
                file = ProgressFSInputFile(
                    media_path,
                    tracker,
                    _format_media_label(media_type, media_path, index=index + 1, total=len(group)),
                )
                if media_type == "photo":
                    media_list.append(AioMediaPhoto(media=file, caption=caption, parse_mode="HTML"))
                elif media_type in {"video", "animation"}:
                    media_list.append(AioMediaVideo(media=file, caption=caption, parse_mode="HTML"))
                elif media_type == "audio":
                    media_list.append(AioMediaAudio(media=file, caption=caption, parse_mode="HTML"))
                else:
                    media_list.append(AioMediaDocument(media=file, caption=caption, parse_mode="HTML"))
            try:
                sent_group = await safe_execute(
                    upload_target["client"].send_media_group(
                        target_id,
                        media_list,
                        reply_to_message_id=reply_to_id,
                    ),
                    sync_state,
                )
                await bot_engine.note_upload_success(upload_target["client"], sum(file_sizes))
                break
            except Exception as exc:
                if sync_state["stop_requested"]:
                    raise
                if _is_request_entity_too_large(exc):
                    if _json_should_fallback_to_user(sender, clone_fallback_to_user):
                        await db.add_msg_log("JSON_FALLBACK", f"组首消息ID:{first_id} | Bot 上传体积超限，已回退辅助账号发送媒体组")
                        sent_group = await _send_json_group_via_user(group, target_id, rewritten_captions, file_entries, reply_to_id)
                        break
                    raise
                retry_after = _parse_retry_after_seconds(exc)
                if retry_after is not None:
                    await bot_engine.mark_upload_bot_cooldown(upload_target["client"], retry_after + 1, f"JSON 媒体组首ID:{first_id}")
                    upload_target = await _select_json_upload_target(
                        sender,
                        file_sizes,
                        clone_fallback_to_user=clone_fallback_to_user,
                        wait_for_available_bot=not _json_should_fallback_to_user(sender, clone_fallback_to_user),
                    )
                    if upload_target["sender"] == "user":
                        await db.add_msg_log("JSON_FALLBACK", f"组首消息ID:{first_id} | Bot 频控，已切换辅助账号发送媒体组")
                        sent_group = await _send_json_group_via_user(group, target_id, rewritten_captions, file_entries, reply_to_id)
                        break
                    continue
                raise
        if sent_group is None and not sync_state["stop_requested"] and _json_should_fallback_to_user(sender, clone_fallback_to_user):
            await db.add_msg_log("JSON_FALLBACK", f"组首消息ID:{first_id} | Bot 发送失败，已回退辅助账号发送媒体组")
            sent_group = await _send_json_group_via_user(group, target_id, rewritten_captions, file_entries, reply_to_id)

    if sent_group is None:
        return None
    for original_msg, sent_msg in zip(group, sent_group):
        new_id = sent_msg.message_id if hasattr(sent_msg, "message_id") else sent_msg.id
        sent_ids.append(new_id)
        await record_success(source_scope_id, target_id, int(original_msg.get("id") or 0), new_id, force_send=force_send)

    await db.add_msg_log(
        "JSON_GROUP_SEND",
        f"组首消息ID:{first_id} | 共 {len(group)} 条 | 目标:[{target_id}] | 已按媒体组发送",
    )
    return sent_ids


async def process_json_sync(
    sender,
    target_id_raw,
    json_path,
    safe_delay,
    force_send,
    json_source_username="",
    media_group_window_seconds: int = JSON_MEDIA_GROUP_WINDOW_SECONDS,
    clone_fallback_to_user: bool = True,
):
    if not json_path or not os.path.exists(json_path):
        await log_sync_error("JSON 文件不存在或路径无效", ValueError(json_path or ""))
        return

    try:
        with open(json_path, "r", encoding="utf-8") as file_obj:
            data = json.load(file_obj)
    except Exception as exc:
        await log_sync_error("JSON 解析失败", exc)
        return

    messages = data.get("messages", [])
    json_dir = os.path.dirname(os.path.abspath(json_path))
    target_id = await resolve_chat_id(bot_engine.aiogram_bot, target_id_raw)
    source_username = normalize_channel_username(json_source_username)
    source_scope_id = 0
    if source_username:
        source_scope_id = build_json_source_scope_id(source_username)
        try:
            source_scope_id = await resolve_chat_id(bot_engine.aiogram_bot, f"@{source_username}")
        except Exception:
            await db.add_msg_log(
                "JSON_WARN",
                f"JSON 源频道 @{source_username} 无法解析真实 ID，已使用稳定用户名作用域导入（链接改写仅支持 t.me/@用户名）",
            )
    elif json_source_username:
        await db.add_msg_log("JSON_WARN", "源频道用户名格式无效，请填写 @username 或 https://t.me/username")

    link_context = await build_link_rewrite_context(
        bot_engine.aiogram_bot,
        source_scope_id,
        target_id,
        source_username_override=source_username,
    )
    sync_state["total"] = len(messages)
    warned_media_groups = False
    warned_link_rewrite = False
    settings = await db.get_all_settings()

    media_group_window_seconds = max(1, int(media_group_window_seconds or JSON_MEDIA_GROUP_WINDOW_SECONDS))

    for group in group_json_messages(messages, media_group_window_seconds):
        if sync_state["stop_requested"]:
            break
        if len(group) > 1:
            if any(item.get("media_group_id") or item.get("grouped_id") or item.get("media_group") for item in group):
                if not warned_media_groups:
                    warned_media_groups = True
                    await db.add_msg_log("JSON_INFO", "JSON 导入检测到显式媒体组标记，已按媒体组发送")
            result = await send_json_media_group(
                group,
                target_id,
                json_dir,
                source_scope_id,
                force_send,
                link_context,
                sender,
                clone_fallback_to_user,
            )
            if result is not None:
                await asyncio.sleep(safe_delay)
                continue
        for msg in group:
            if sync_state["stop_requested"]:
                break
            if msg.get("type") != "message":
                continue

            msg_id = msg.get("id", 0)
            msg_type, sync_key = get_msg_meta(msg, "json")
            if settings.get(sync_key, "1") == "0":
                await db.add_msg_log("JSON_DROP_TYPE", f"消息ID:{msg_id} | 类型:{msg_type} | 已被类型过滤拦截")
                continue

            text = build_json_text(msg)
            if text and not warned_link_rewrite and MESSAGE_LINK_RE.search(text):
                warned_link_rewrite = True
                if source_username:
                    await db.add_msg_log("JSON_INFO", f"JSON 导入已启用链接改写，源频道用户名: @{source_username}")
                else:
                    await db.add_msg_log("JSON_WARN", "JSON 导入检测到消息链接引用；未填写源频道用户名，无法安全改写源频道链接")

            file_name = ""
            if msg.get("file"):
                file_name = os.path.basename(str(msg.get("file") or ""))
            elif msg.get("photo"):
                file_name = os.path.basename(str(msg.get("photo") or ""))
            elif msg.get("video"):
                file_name = os.path.basename(str(msg.get("video") or ""))
            elif msg.get("audio"):
                file_name = os.path.basename(str(msg.get("audio") or ""))
            elif msg.get("voice"):
                file_name = os.path.basename(str(msg.get("voice") or ""))

            should_skip, text = await db.apply_message_filters(text, msg_type != "text", file_name)
            if should_skip or (msg_type == "text" and not text.strip()):
                await db.add_msg_log("JSON_DROP_REGEX", f"消息ID:{msg_id} | 已被正则过滤拦截")
                continue

            if await update_state_and_check_skip(source_scope_id, target_id, msg_id, text[:50] or "[媒体]", force_send=force_send):
                continue
            text, rewrite_count = await rewrite_message_links(text, source_scope_id, link_context)
            if rewrite_count:
                await db.add_msg_log("JSON_LINK_REWRITE", f"消息ID:{msg_id} | 命中 {rewrite_count} 个链接改写")

            media_path, media_type, _ = resolve_json_media(msg, json_dir)
            reply_to_id = await resolve_reply_target(
                source_scope_id,
                target_id,
                get_reply_source_msg_id(msg, "json"),
                "JSON",
                msg_id,
            )

            try:
                if media_path and os.path.exists(media_path):
                    file_size = os.path.getsize(media_path)
                    caption = text if text else None
                    file_label = _format_media_label(media_type, media_path)
                    upload_target = await _select_json_upload_target(
                        sender,
                        [file_size],
                        clone_fallback_to_user=clone_fallback_to_user,
                        wait_for_available_bot=not _json_should_fallback_to_user(sender, clone_fallback_to_user),
                    )

                    if upload_target["sender"] == "user":
                        sent = await _send_json_single_via_user(
                            target_id,
                            media_type,
                            media_path,
                            caption,
                            reply_to_id,
                            SharedUploadProgressTracker("上传中 [辅助账号回退]", file_size),
                            file_label,
                        )
                    else:
                        sent = None
                        for _ in range(3):
                            if sync_state["stop_requested"]:
                                break
                            tracker = UploadProgressTracker(f"上传中 [{upload_target['label']}]", file_size)
                            file = ProgressFSInputFile(media_path, tracker, file_label)
                            try:
                                if media_type == "photo":
                                    send_coro = lambda: upload_target["client"].send_photo(
                                        target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id
                                    )
                                elif media_type == "video":
                                    send_coro = lambda: upload_target["client"].send_video(
                                        target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id
                                    )
                                elif media_type == "animation":
                                    send_coro = lambda: upload_target["client"].send_animation(
                                        target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id
                                    )
                                elif media_type == "audio":
                                    send_coro = lambda: upload_target["client"].send_audio(
                                        target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id
                                    )
                                elif media_type == "voice":
                                    send_coro = lambda: upload_target["client"].send_voice(
                                        target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id
                                    )
                                elif media_type == "sticker":
                                    sent = await safe_execute(
                                        upload_target["client"].send_sticker(target_id, file, reply_to_message_id=reply_to_id),
                                        sync_state,
                                    )
                                    await bot_engine.note_upload_success(upload_target["client"], file_size)
                                    break
                                else:
                                    send_coro = lambda: upload_target["client"].send_document(
                                        target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id
                                    )
                                if media_type != "sticker":
                                    sent = await safe_execute(send_coro(), sync_state)
                                    await bot_engine.note_upload_success(upload_target["client"], file_size)
                                    break
                            except Exception as exc:
                                if sync_state["stop_requested"]:
                                    raise
                                if media_type == "sticker":
                                    thumb = str(msg.get("thumbnail") or "")
                                    thumb_path = os.path.join(json_dir, thumb) if thumb else ""
                                    if _is_request_entity_too_large(exc):
                                        if _json_should_fallback_to_user(sender, clone_fallback_to_user):
                                            await db.add_msg_log("JSON_FALLBACK", f"消息ID:{msg_id} | Bot 上传体积超限，已回退辅助账号发送")
                                            sent = await _send_json_single_via_user(
                                                target_id,
                                                media_type,
                                                media_path,
                                                caption,
                                                reply_to_id,
                                                SharedUploadProgressTracker("上传中 [辅助账号回退]", file_size),
                                                file_label,
                                            )
                                            break
                                        raise
                                    retry_after = _parse_retry_after_seconds(exc)
                                    if retry_after is not None:
                                        await bot_engine.mark_upload_bot_cooldown(upload_target["client"], retry_after + 1, f"JSON 消息ID:{msg_id}")
                                        upload_target = await _select_json_upload_target(
                                            sender,
                                            [file_size],
                                            clone_fallback_to_user=clone_fallback_to_user,
                                            wait_for_available_bot=not _json_should_fallback_to_user(sender, clone_fallback_to_user),
                                        )
                                        if upload_target["sender"] == "user":
                                            await db.add_msg_log("JSON_FALLBACK", f"消息ID:{msg_id} | Bot 频控，已切换辅助账号发送")
                                            sent = await _send_json_single_via_user(
                                                target_id,
                                                media_type,
                                                media_path,
                                                caption,
                                                reply_to_id,
                                                SharedUploadProgressTracker("上传中 [辅助账号回退]", file_size),
                                                file_label,
                                            )
                                            break
                                        continue
                                    if not thumb_path or not os.path.exists(thumb_path):
                                        raise
                                    await db.add_msg_log("JSON_STICKER_AS_IMAGE", f"消息ID:{msg_id} | 贴纸发送失败，已回退为缩略图图片发送: {exc}")
                                    sent = await _execute_with_retry(
                                        lambda: bot_engine.aiogram_bot.send_photo(
                                            target_id,
                                            FSInputFile(thumb_path),
                                            caption=caption,
                                            parse_mode="HTML",
                                            reply_to_message_id=reply_to_id,
                                        ),
                                        action_label=f"贴纸缩略图回退 [{msg_id}]",
                                    )
                                    break
                                if _is_request_entity_too_large(exc):
                                    if _json_should_fallback_to_user(sender, clone_fallback_to_user):
                                        await db.add_msg_log("JSON_FALLBACK", f"消息ID:{msg_id} | Bot 上传体积超限，已回退辅助账号发送")
                                        sent = await _send_json_single_via_user(
                                            target_id,
                                            media_type,
                                            media_path,
                                            caption,
                                            reply_to_id,
                                            SharedUploadProgressTracker("上传中 [辅助账号回退]", file_size),
                                            file_label,
                                        )
                                        break
                                    raise
                                retry_after = _parse_retry_after_seconds(exc)
                                if retry_after is not None:
                                    await bot_engine.mark_upload_bot_cooldown(upload_target["client"], retry_after + 1, f"JSON 消息ID:{msg_id}")
                                    upload_target = await _select_json_upload_target(
                                        sender,
                                        [file_size],
                                        clone_fallback_to_user=clone_fallback_to_user,
                                        wait_for_available_bot=not _json_should_fallback_to_user(sender, clone_fallback_to_user),
                                    )
                                    if upload_target["sender"] == "user":
                                        await db.add_msg_log("JSON_FALLBACK", f"消息ID:{msg_id} | Bot 频控，已切换辅助账号发送")
                                        sent = await _send_json_single_via_user(
                                            target_id,
                                            media_type,
                                            media_path,
                                            caption,
                                            reply_to_id,
                                            SharedUploadProgressTracker("上传中 [辅助账号回退]", file_size),
                                            file_label,
                                        )
                                        break
                                    continue
                                raise
                        if sent is None and not sync_state["stop_requested"] and _json_should_fallback_to_user(sender, clone_fallback_to_user):
                            await db.add_msg_log("JSON_FALLBACK", f"消息ID:{msg_id} | Bot 发送失败，已回退辅助账号发送")
                            sent = await _send_json_single_via_user(
                                target_id,
                                media_type,
                                media_path,
                                caption,
                                reply_to_id,
                                SharedUploadProgressTracker("上传中 [辅助账号回退]", file_size),
                                file_label,
                            )
                        if sent is None:
                            return
                    sent_id = sent.message_id if hasattr(sent, "message_id") else sent.id
                elif text:
                    upload_target = await _select_json_upload_target(
                        sender,
                        [],
                        clone_fallback_to_user=clone_fallback_to_user,
                        wait_for_available_bot=not _json_should_fallback_to_user(sender, clone_fallback_to_user),
                    )
                    if upload_target["sender"] == "user":
                        sent = await _send_json_text_via_user(target_id, text, reply_to_id)
                    else:
                        sent = None
                        for _ in range(3):
                            if sync_state["stop_requested"]:
                                break
                            try:
                                sent = await safe_execute(
                                    upload_target["client"].send_message(
                                        target_id,
                                        text,
                                        parse_mode="HTML",
                                        reply_to_message_id=reply_to_id,
                                    ),
                                    sync_state,
                                )
                                break
                            except Exception as exc:
                                if sync_state["stop_requested"]:
                                    raise
                                retry_after = _parse_retry_after_seconds(exc)
                                if retry_after is not None:
                                    await bot_engine.mark_upload_bot_cooldown(upload_target["client"], retry_after + 1, f"JSON 消息ID:{msg_id}")
                                    upload_target = await _select_json_upload_target(
                                        sender,
                                        [],
                                        clone_fallback_to_user=clone_fallback_to_user,
                                        wait_for_available_bot=not _json_should_fallback_to_user(sender, clone_fallback_to_user),
                                    )
                                    if upload_target["sender"] == "user":
                                        await db.add_msg_log("JSON_FALLBACK", f"消息ID:{msg_id} | Bot 频控，已切换辅助账号发送文本")
                                        sent = await _send_json_text_via_user(target_id, text, reply_to_id)
                                        break
                                    continue
                                raise
                        if sent is None and not sync_state["stop_requested"] and _json_should_fallback_to_user(sender, clone_fallback_to_user):
                            await db.add_msg_log("JSON_FALLBACK", f"消息ID:{msg_id} | Bot 发送失败，已回退辅助账号发送文本")
                            sent = await _send_json_text_via_user(target_id, text, reply_to_id)
                        if sent is None:
                            return
                    sent_id = sent.message_id
                else:
                    if media_path and not os.path.exists(media_path):
                        await db.add_msg_log("JSON_MEDIA_MISSING", f"消息ID:{msg_id} | 媒体文件不存在，已跳过: {media_path}")
                    continue

                await record_success(source_scope_id, target_id, msg_id, sent_id, force_send=force_send)
                await db.add_msg_log("JSON_SEND", f"消息ID:{msg_id} | 目标:[{target_id}] 新ID:{sent_id} | 上传成功")
            except JsonSyncFatalError as exc:
                sync_state["stop_requested"] = True
                await log_sync_error(f"JSON 致命错误 ID {msg_id}", exc)
                raise
            except Exception as exc:
                if sync_state["stop_requested"]:
                    break
                await log_sync_error(f"JSON 消息上传失败 ID {msg_id}", exc)

            await asyncio.sleep(safe_delay)
