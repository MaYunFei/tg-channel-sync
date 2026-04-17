from __future__ import annotations

import asyncio
import json
import os

from aiogram.types import FSInputFile

import bot_engine
import database as db
from services.sync_services import (
    MESSAGE_LINK_RE,
    build_link_rewrite_context,
    log_sync_error,
    resolve_chat_id,
    resolve_reply_target,
    rewrite_message_links,
)
from .common import build_json_text, get_msg_meta, get_reply_source_msg_id, resolve_json_media
from .state import record_success, sync_state, update_state_and_check_skip


async def process_json_sync(target_id_raw, json_path, safe_delay, force_send, json_source_username=""):
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
    link_context = await build_link_rewrite_context(
        bot_engine.aiogram_bot,
        0,
        target_id,
        source_username_override=json_source_username,
    )
    sync_state["total"] = len(messages)
    warned_media_groups = False
    warned_link_rewrite = False
    settings = await db.get_all_settings()

    for msg in messages:
        if sync_state["stop_requested"]:
            break
        if msg.get("type") != "message":
            continue

        msg_id = msg.get("id", 0)
        msg_type, sync_key = get_msg_meta(msg, "json")
        if settings.get(sync_key, "1") == "0":
            await db.add_msg_log("JSON_DROP_TYPE", f"消息ID:{msg_id} | type={msg_type}")
            continue

        text = build_json_text(msg)
        if text and not warned_link_rewrite and MESSAGE_LINK_RE.search(text):
            warned_link_rewrite = True
            if json_source_username:
                await db.add_msg_log("JSON_INFO", f"JSON 导入已启用链接改写，源频道用户名: @{str(json_source_username).lstrip('@')}")
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
            await db.add_msg_log("JSON_DROP_REGEX", f"消息ID:{msg_id}")
            continue

        if await update_state_and_check_skip(0, target_id, msg_id, text[:50] or "[媒体]", force_send=force_send):
            continue
        text, rewrite_count = await rewrite_message_links(text, 0, link_context)
        if rewrite_count:
            await db.add_msg_log("JSON_LINK_REWRITE", f"消息ID:{msg_id} | 命中 {rewrite_count} 个链接改写")

        media_group_hint = msg.get("media_group_id") or msg.get("grouped_id") or msg.get("media_group")
        if media_group_hint and not warned_media_groups:
            warned_media_groups = True
            await db.add_msg_log("JSON_WARN", "JSON 导入检测到媒体组标记，当前只能按单条消息发送，无法原样还原媒体组")

        media_path, media_type, media_note = resolve_json_media(msg, json_dir)
        reply_to_id = await resolve_reply_target(0, target_id, get_reply_source_msg_id(msg, "json"), "JSON", msg_id)

        try:
            if media_path and os.path.exists(media_path):
                sync_state["current_text"] = f"上传: {os.path.basename(media_path)}"
                file = FSInputFile(media_path)
                caption = text if text else None

                if media_note == "sticker_thumbnail":
                    await db.add_msg_log("JSON_STICKER_AS_IMAGE", f"消息ID:{msg_id} | 贴纸缺少可转发链接，已改为缩略图图片发送")
                elif media_note == "sticker_file":
                    await db.add_msg_log("JSON_STICKER_AS_IMAGE", f"消息ID:{msg_id} | 贴纸缺少可转发链接，已按图片方式尝试发送")

                if media_type == "photo":
                    sent = await bot_engine.aiogram_bot.send_photo(target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id)
                elif media_type == "video":
                    sent = await bot_engine.aiogram_bot.send_video(target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id)
                elif media_type == "audio":
                    sent = await bot_engine.aiogram_bot.send_audio(target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id)
                elif media_type == "voice":
                    sent = await bot_engine.aiogram_bot.send_voice(target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id)
                else:
                    sent = await bot_engine.aiogram_bot.send_document(target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id)
                sent_id = sent.message_id
            elif text:
                sent = await bot_engine.aiogram_bot.send_message(target_id, text, parse_mode="HTML", reply_to_message_id=reply_to_id)
                sent_id = sent.message_id
            else:
                continue

            await record_success(0, target_id, msg_id, sent_id, force_send=force_send)
            await db.add_msg_log("JSON_SEND", f"消息ID:{msg_id} | 目标:[{target_id}] 新ID:{sent_id} | 上传成功")
        except Exception as exc:
            if sync_state["stop_requested"]:
                break
            await log_sync_error(f"JSON 消息上传失败 ID {msg_id}", exc)

        await asyncio.sleep(safe_delay)
