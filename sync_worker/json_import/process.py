from __future__ import annotations

import asyncio
import json
import os

from aiogram.types import FSInputFile
from pyrogram.enums import ParseMode

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
from ..core import (
    UploadProgressTracker as SharedUploadProgressTracker,
    build_json_text,
    build_pyro_progress_callback,
    get_msg_meta,
    get_reply_source_msg_id,
    resolve_json_media,
)
from ..media import prepare_json_media_for_send
from ..runtime import TEMP_DIR, record_success, sync_state, update_state_and_check_skip
from ..senders import build_bot_media_group, build_user_media_group
from .grouping import _json_group_family, group_json_messages
from .helpers import (
    JSON_MEDIA_GROUP_WINDOW_SECONDS,
    JsonSyncFatalError,
    ProgressFSInputFile,
    UploadProgressTracker,
    _format_media_label,
    _is_request_entity_too_large,
    _json_should_fallback_to_user,
    _parse_retry_after_seconds,
    _select_json_upload_target,
    _execute_with_retry,
)


def _pyro_file_ref(media_path: str) -> str:
    return media_path


async def _prepare_json_media_path(media_path: str, media_type: str, msg_id: int, hash_perturb: bool) -> tuple[str, bool]:
    return await prepare_json_media_for_send(media_path, media_type, msg_id, hash_perturb, temp_dir=TEMP_DIR)


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
    group_family = _json_group_family(group[0])
    normalized_captions = []
    group_items = []
    for index, ((item, media_path, media_type), caption_html) in enumerate(zip(file_entries, rewritten_captions), start=1):
        caption = caption_html if caption_html else None
        if group_family == "visual" and index > 1:
            caption = None
        normalized_captions.append(caption)
        group_items.append((item, media_path, "video" if media_type == "animation" else media_type))
    media = build_user_media_group(group_items, normalized_captions, {})
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

async def send_json_media_group(
    group,
    target_id,
    json_dir,
    source_scope_id,
    force_send,
    link_context,
    sender: str,
    clone_fallback_to_user: bool,
    hash_perturb: bool = False,
    include_external_source_header: bool = False,
):
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
    prepared_temp_paths = []
    for index, item in enumerate(group):
        item_id = int(item.get("id") or 0)
        media_path, media_type, _ = resolve_json_media(item, json_dir)
        if not media_path or not os.path.exists(media_path):
            await db.add_msg_log("JSON_MEDIA_MISSING", f"消息ID:{item_id} | 媒体组文件不存在，已回退为逐条发送")
            return None
        media_path, created_temp = await _prepare_json_media_path(media_path, media_type, item_id, hash_perturb)
        if created_temp:
            prepared_temp_paths.append(media_path)
        total_bytes += os.path.getsize(media_path)
        file_entries.append((item, media_path, media_type))

        caption_html = build_json_text(item, include_external_source_header=include_external_source_header)
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
            normalized_captions = []
            group_items = []
            for index, (item, media_path, media_type) in enumerate(file_entries):
                caption_html = rewritten_captions[index]
                caption = caption_html if caption_html else None
                if group_family == "visual" and index > 0:
                    caption = None
                normalized_captions.append(caption)
                group_items.append((item, media_path, "video" if media_type == "animation" else media_type))
            tracker, media_list = build_bot_media_group(group_items, normalized_captions, {}, total_bytes, upload_target["label"])
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

    try:
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
    finally:
        for temp_path in prepared_temp_paths:
            try:
                os.remove(temp_path)
            except Exception:
                pass


async def process_json_sync(
    sender,
    target_id_raw,
    json_path,
    safe_delay,
    force_send,
    json_source_username="",
    media_group_window_seconds: int = JSON_MEDIA_GROUP_WINDOW_SECONDS,
    clone_fallback_to_user: bool = True,
    hash_perturb: bool = False,
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
    include_external_source_header = bool(getattr(settings, "get", lambda *_: False)("json_add_external_source_header", False))

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
                hash_perturb,
                include_external_source_header,
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

            text = build_json_text(msg, include_external_source_header=include_external_source_header)
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
                    media_path, created_temp = await _prepare_json_media_path(media_path, media_type, msg_id, hash_perturb)
                    file_size = os.path.getsize(media_path)
                    caption = text if text else None
                    file_label = _format_media_label(media_type, media_path)
                    try:
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
                    finally:
                        if created_temp:
                            try:
                                os.remove(media_path)
                            except Exception:
                                pass
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
