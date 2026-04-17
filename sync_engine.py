import asyncio
import json
import os
import re
import time

from aiogram.types import FSInputFile
from aiogram.types import InputMediaAudio as AioAudio
from aiogram.types import InputMediaDocument as AioDoc
from aiogram.types import InputMediaPhoto as AioPhoto
from aiogram.types import ReplyParameters
from aiogram.types import InputMediaVideo as AioVideo
from pyrogram.enums import ParseMode
from pyrogram.types import InputMediaAudio
from pyrogram.types import InputMediaDocument
from pyrogram.types import InputMediaPhoto
from pyrogram.types import InputMediaVideo

import bot_engine
import database as db
from app_paths import temp_dir

TYPE_MAP = {
    "photo": "sync_photo",
    "video": "sync_video",
    "animation": "sync_gif",
    "audio": "sync_audio",
    "voice": "sync_voice",
    "document": "sync_document",
    "sticker": "sync_sticker",
}
AIO_MEDIA_CLS = {"photo": AioPhoto, "video": AioVideo, "audio": AioAudio, "document": AioDoc}
PYRO_MEDIA_CLS = {"photo": InputMediaPhoto, "video": InputMediaVideo, "audio": InputMediaAudio, "document": InputMediaDocument}
TEMP_DIR = str(temp_dir())

sync_state = {
    "is_syncing": False,
    "mode": "",
    "total": 0,
    "current": 0,
    "current_text": "",
    "current_link": "",
    "skipped": 0,
    "stop_requested": False,
    "source_id_raw": "",
    "target_id_raw": "",
    "delay": 5,
    "start_id": "",
    "end_id": "",
    "json_path": "",
    "json_source_username": "",
    "force_send": False,
}


def get_msg_meta(msg, mode):
    if mode in ["api", "clone"]:
        for attr, key in TYPE_MAP.items():
            if getattr(msg, attr, None):
                return attr, key
        return "text", "sync_text"

    if msg.get("photo"):
        return "photo", "sync_photo"

    media_type = msg.get("media_type")
    json_map = {
        "video_file": ("video", "sync_video"),
        "animation": ("animation", "sync_gif"),
        "audio_file": ("audio", "sync_audio"),
        "voice_message": ("voice", "sync_voice"),
        "sticker": ("sticker", "sync_sticker"),
    }
    if media_type in json_map:
        return json_map[media_type]
    if "file" in msg:
        return "document", "sync_document"
    return "text", "sync_text"


def get_quote_payload(msg):
    quote = getattr(msg, "quote", None)
    if not quote or not getattr(quote, "text", None):
        return None
    return {
        "text": quote.text,
        "position": getattr(quote, "position", None),
        "entities": getattr(quote, "entities", None),
    }


async def build_link_rewrite_context(source_id, target_id, source_username_override=None):
    if bot_engine.aiogram_bot is None:
        return None

    context = {"source_id": source_id, "target_id": target_id}
    try:
        source_chat = await bot_engine.aiogram_bot.get_chat(source_id) if source_id != 0 else None
        target_chat = await bot_engine.aiogram_bot.get_chat(target_id)
    except Exception:
        source_chat = None
        target_chat = None

    source_username = source_username_override or (getattr(source_chat, "username", None) if source_chat else None)
    target_username = getattr(target_chat, "username", None) if target_chat else None
    context["source_username"] = str(source_username).lstrip("@") if source_username else None
    context["target_username"] = str(target_username).lstrip("@") if target_username else None
    return context


def _replace_msg_link(match, target_channel_ref, mapped_msg_id):
    prefix = match.group("prefix")
    suffix = match.group("suffix") or ""
    return f"{prefix}{target_channel_ref}/{mapped_msg_id}{suffix}"


async def rewrite_message_links(text_html, source_id, link_context):
    if not text_html or not link_context:
        return text_html, 0

    updated_html = text_html
    rewrite_count = 0

    async def replace_pattern(pattern, target_channel_ref):
        nonlocal updated_html, rewrite_count
        for match in list(pattern.finditer(updated_html)):
            source_msg_id = int(match.group("msg_id"))
            target_msg_id = await db.get_target_msg_id(source_id, source_msg_id)
            if not target_msg_id:
                continue
            original = match.group(0)
            replaced = _replace_msg_link(match, target_channel_ref, target_msg_id)
            if original == replaced:
                continue
            updated_html = updated_html.replace(original, replaced)
            rewrite_count += 1

    if source_id != 0:
        source_internal_id = str(abs(source_id)).removeprefix("100")
        target_internal_id = str(abs(link_context["target_id"])).removeprefix("100")
        await replace_pattern(
            re.compile(rf"(?P<prefix>https?://t\.me/c/{re.escape(source_internal_id)}/)(?P<msg_id>\d+)(?P<suffix>\b)"),
            f"https://t.me/c/{target_internal_id}",
        )

    if link_context.get("source_username") and link_context.get("target_username"):
        await replace_pattern(
            re.compile(rf"(?P<prefix>https?://t\.me/{re.escape(link_context['source_username'])}/)(?P<msg_id>\d+)(?P<suffix>\b)"),
            f"https://t.me/{link_context['target_username']}",
        )

    return updated_html, rewrite_count


async def rewrite_media_group_captions(source_id, target_id, group, source_username_override=None):
    link_context = await build_link_rewrite_context(source_id, target_id, source_username_override=source_username_override)
    captions = []
    changed = False
    total_rewrites = 0

    for item in group:
        original_caption = item.caption.html if item.caption else ""
        rewritten_caption, rewrite_count = await rewrite_message_links(original_caption, source_id, link_context)
        if rewritten_caption != original_caption:
            changed = True
        total_rewrites += rewrite_count
        captions.append(rewritten_caption)

    return captions, changed, total_rewrites


def get_media_reference(msg, msg_type):
    media_obj = getattr(msg, msg_type, None)
    return getattr(media_obj, "file_id", None) if media_obj else None


async def dynamic_send(client, msg_type, chat_id, file_ref, caption, parse_mode, reply_to_message_id=None, quote_data=None):
    method_name = "send_message" if msg_type == "text" else f"send_{msg_type}"
    method = getattr(client, method_name, None) or getattr(client, "send_document")
    kwargs = {"chat_id": chat_id}
    if msg_type != "sticker":
        kwargs["parse_mode"] = parse_mode
    if quote_data and reply_to_message_id:
        if bot_engine.is_bot_client(client):
            kwargs["reply_parameters"] = ReplyParameters(
                message_id=reply_to_message_id,
                quote=quote_data["text"],
                quote_position=quote_data.get("position"),
            )
        else:
            kwargs["reply_to_message_id"] = reply_to_message_id
            kwargs["quote_text"] = quote_data["text"]
            if quote_data.get("entities"):
                kwargs["quote_entities"] = quote_data["entities"]
    elif reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
    if msg_type == "sticker":
        kwargs["sticker"] = file_ref
    elif msg_type != "text":
        kwargs["caption"] = caption
        kwargs[msg_type if hasattr(client, method_name) else "document"] = file_ref
    else:
        kwargs["text"] = caption
    return await method(**kwargs)


async def resolve_clone_upload_target(sender, app, file_sizes):
    total_size = sum(file_sizes)
    if sender != "bot":
        return {"sender": "user", "client": app, "parse_mode": ParseMode.HTML, "label": "辅助账号", "bytes": total_size}
    if not bot_engine.should_upload_via_bot(max(file_sizes) if file_sizes else 0):
        return {"sender": "user", "client": app, "parse_mode": ParseMode.HTML, "label": "辅助账号", "bytes": total_size}
    selection = await bot_engine.acquire_upload_bot(total_size)
    return {"sender": "bot", "client": selection["client"], "parse_mode": "HTML", "label": selection["label"], "bytes": total_size}


async def safe_execute(coro):
    task = asyncio.create_task(coro)
    while not task.done():
        if sync_state.get("stop_requested"):
            task.cancel()
            raise Exception("STOP_REQUESTED")
        await asyncio.sleep(0.2)
    try:
        return await task
    except asyncio.CancelledError:
        raise Exception("STOP_REQUESTED")


def create_progress_callback(action_name):
    last_upd = [0]

    def progress(downloaded, total_bytes):
        if sync_state.get("stop_requested"):
            raise Exception("STOP_REQUESTED")
        now = time.time()
        if now - last_upd[0] > 0.5 or downloaded == total_bytes:
            last_upd[0] = now
            speed_mb = (downloaded / (now - last_upd[0] + 0.001)) / (1024 * 1024) if downloaded > 0 else 0
            sync_state["current_text"] = f"{action_name} {downloaded / total_bytes * 100:.1f}% ({speed_mb:.1f} MB/s)" if total_bytes > 0 else action_name

    return progress


def get_reply_source_msg_id(msg, mode):
    if mode == "json":
        return msg.get("reply_to_message_id")
    return getattr(msg, "reply_to_message_id", None)


async def resolve_reply_target(source_id, reply_source_msg_id, mode_label, current_msg_id):
    if not reply_source_msg_id:
        return None
    target_reply_id = await db.get_target_msg_id(source_id, reply_source_msg_id)
    if target_reply_id:
        await db.add_msg_log(f"{mode_label}_REPLY_MAP", f"消息ID:{current_msg_id} | 回复源消息:{reply_source_msg_id} -> 目标消息:{target_reply_id}")
    else:
        await db.add_msg_log(f"{mode_label}_REPLY_FALLBACK", f"消息ID:{current_msg_id} | 被回复消息:{reply_source_msg_id} 未同步，按普通消息发送")
    return target_reply_id


def build_json_text(msg):
    text = msg.get("text", "")
    if not isinstance(text, list):
        return text
    parts = []
    for item in text:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            parts.append(item.get("text", ""))
    return "".join(parts)


def resolve_json_media(msg, json_dir):
    if msg.get("photo"):
        return os.path.join(json_dir, msg["photo"]), "photo", None
    if msg.get("video"):
        return os.path.join(json_dir, msg["video"]), "video", None
    if msg.get("audio"):
        return os.path.join(json_dir, msg["audio"]), "audio", None
    if msg.get("voice"):
        return os.path.join(json_dir, msg["voice"]), "voice", None
    if msg.get("file"):
        if msg.get("media_type") == "sticker":
            thumb = msg.get("thumbnail")
            if thumb:
                thumb_path = os.path.join(json_dir, thumb)
                if os.path.exists(thumb_path):
                    return thumb_path, "photo", "sticker_thumbnail"
            return os.path.join(json_dir, msg["file"]), "photo", "sticker_file"
        return os.path.join(json_dir, msg["file"]), "document", None
    return None, None, None


async def update_state_and_check_skip(source_id, msg_id, text, force_send=False):
    sync_state["current"] += 1
    sync_state["current_link"] = f"t.me/c/{str(source_id).replace('-100', '')}/{msg_id}"
    sync_state["current_text"] = text
    if not force_send and await db.is_message_synced(source_id, msg_id):
        sync_state["skipped"] += 1
        mode_label = sync_state.get("mode", "SYNC") or "SYNC"
        source_label = source_id if source_id else "JSON"
        await db.add_msg_log(f"{mode_label}_SKIP_DUP", f"源:[{source_label}] 消息ID:{msg_id} | 已命中重复检查，跳过发送")
        return True
    return False


async def record_success(source_id, msg_id, target_msg_id, force_send=False):
    await db.save_msg_mapping(source_id, msg_id, target_msg_id, overwrite=force_send)


async def sync_single_message(mode, sender, app, bot, source_id, target_id, msg, safe_delay, force_send):
    msg_type, _ = get_msg_meta(msg, mode)
    has_media = msg_type != "text"
    file_name = getattr(getattr(msg, msg_type, None), "file_name", "") if msg_type in ["document", "video"] else ""
    text_html = msg.text.html if msg.text else (msg.caption.html if msg.caption else "") if hasattr(msg, "text") else ""
    quote_data = get_quote_payload(msg)

    should_skip, new_html = await db.apply_message_filters(text_html, has_media, file_name or "")
    if should_skip or (not has_media and not new_html.strip()):
        return
    if await update_state_and_check_skip(source_id, msg.id, new_html[:50] or "[媒体]", force_send=force_send):
        return

    reply_to_id = await resolve_reply_target(source_id, get_reply_source_msg_id(msg, mode), mode.upper(), msg.id)
    link_context = await build_link_rewrite_context(source_id, target_id)
    new_html, rewrite_count = await rewrite_message_links(new_html, source_id, link_context)
    if rewrite_count:
        await db.add_msg_log(f"{mode.upper()}_LINK_REWRITE", f"原始:[{source_id}] 消息ID:{msg.id} | 命中 {rewrite_count} 个链接改写")

    try:
        if mode == "api":
            if quote_data and reply_to_id:
                if not has_media:
                    kwargs = {"chat_id": target_id, "text": new_html, "parse_mode": ParseMode.HTML, "reply_to_message_id": reply_to_id, "quote_text": quote_data["text"]}
                    if quote_data.get("entities"):
                        kwargs["quote_entities"] = quote_data["entities"]
                    sent_id = (await safe_execute(app.send_message(**kwargs))).id
                else:
                    media_ref = get_media_reference(msg, msg_type)
                    if not media_ref:
                        raise ValueError(f"引用回复媒体缺少可复用 file_id: {msg.id}")
                    sent = await safe_execute(dynamic_send(app, msg_type, target_id, media_ref, new_html, ParseMode.HTML, reply_to_message_id=reply_to_id, quote_data=quote_data))
                    sent_id = sent.id
                await db.add_msg_log("API_QUOTE_SEND", f"原始:[{source_id}] 消息ID:{msg.id} | 已按引用回复发送")
            elif new_html != text_html:
                if not has_media:
                    kwargs = {"chat_id": target_id, "text": new_html, "parse_mode": ParseMode.HTML}
                    if reply_to_id:
                        kwargs["reply_to_message_id"] = reply_to_id
                    sent_id = (await safe_execute(app.send_message(**kwargs))).id
                else:
                    kwargs = {"chat_id": target_id, "from_chat_id": source_id, "message_id": msg.id, "caption": new_html}
                    if reply_to_id:
                        kwargs["reply_to_message_id"] = reply_to_id
                    sent_id = (await safe_execute(app.copy_message(**kwargs))).id
            else:
                kwargs = {"chat_id": target_id, "from_chat_id": source_id, "message_id": msg.id}
                if reply_to_id:
                    kwargs["reply_to_message_id"] = reply_to_id
                sent_id = (await safe_execute(app.copy_message(**kwargs))).id
        else:
            if not has_media:
                sent = await safe_execute(dynamic_send(bot if sender == "bot" else app, "text", target_id, None, new_html, "HTML" if sender == "bot" else ParseMode.HTML, reply_to_message_id=reply_to_id, quote_data=quote_data if reply_to_id else None))
                sent_id = sent.message_id if sender == "bot" else sent.id
            else:
                file_path = None
                for _ in range(3):
                    if sync_state["stop_requested"]:
                        break
                    try:
                        file_path = await safe_execute(app.download_media(msg, file_name=f"{TEMP_DIR}/", progress=create_progress_callback("下载中")))
                        if file_path:
                            break
                    except Exception as e:
                        if "STOP_REQUESTED" in str(e):
                            raise e
                        await asyncio.sleep(2)
                if not file_path or sync_state["stop_requested"]:
                    return

                file_size = os.path.getsize(file_path)
                upload_target = await safe_execute(resolve_clone_upload_target(sender, app, [file_size]))
                actual_sender = upload_target["sender"]
                client = upload_target["client"]
                parse_mode = upload_target["parse_mode"]
                sent_id = None

                for _ in range(3):
                    if sync_state["stop_requested"]:
                        break
                    try:
                        sync_state["current_text"] = f"上传中... [{upload_target['label']}]"
                        media_arg = FSInputFile(file_path) if actual_sender == "bot" else file_path
                        sent = await safe_execute(dynamic_send(client, msg_type, target_id, media_arg, new_html, parse_mode, reply_to_message_id=reply_to_id, quote_data=quote_data if reply_to_id else None))
                        sent_id = sent.message_id if actual_sender == "bot" else sent.id
                        if actual_sender == "bot":
                            await bot_engine.note_upload_success(client, file_size)
                        break
                    except Exception as e:
                        if "STOP_REQUESTED" in str(e):
                            raise e
                        await asyncio.sleep(2)

                if sent_id is None and sender == "bot" and actual_sender == "bot":
                    await db.add_msg_log("BOT_FALLBACK", f"原始:[{source_id}] 消息ID:{msg.id} | {upload_target['label']} 上传失败，回退辅助账号重传")
                    for _ in range(3):
                        if sync_state["stop_requested"]:
                            break
                        try:
                            sync_state["current_text"] = "上传中... [辅助账号回退]"
                            sent = await safe_execute(dynamic_send(app, msg_type, target_id, file_path, new_html, ParseMode.HTML, reply_to_message_id=reply_to_id, quote_data=quote_data if reply_to_id else None))
                            sent_id = sent.id
                            actual_sender = "user"
                            break
                        except Exception as e:
                            if "STOP_REQUESTED" in str(e):
                                raise e
                            await asyncio.sleep(2)

                try:
                    os.remove(file_path)
                except Exception:
                    pass
                if sent_id is None:
                    return

        await record_success(source_id, msg.id, sent_id, force_send=force_send)
        if mode == "clone" and quote_data and reply_to_id:
            await db.add_msg_log("CLONE_QUOTE_SEND", f"原始:[{source_id}] 消息ID:{msg.id} | 已按引用回复发送")
        await db.add_msg_log(f"{mode.upper()}_SEND", f"原始:[{source_id}] 消息ID:{msg.id} | 目标:[{target_id}] 新ID:{sent_id} | 同步成功")
    except Exception as e:
        if sync_state["stop_requested"]:
            return
        await db.add_log("ERROR", f"单条同步异常 ID {msg.id}: {e}")

    await asyncio.sleep(safe_delay)


async def sync_media_group(mode, sender, app, bot, source_id, target_id, group, safe_delay, force_send):
    if await update_state_and_check_skip(source_id, group[0].id, "[媒体组]", force_send=force_send):
        return

    reply_to_id = await resolve_reply_target(source_id, get_reply_source_msg_id(group[0], mode), mode.upper(), group[0].id)
    quote_data = get_quote_payload(group[0])
    rewritten_captions, captions_changed, caption_rewrite_count = await rewrite_media_group_captions(source_id, target_id, group)

    if mode == "api":
        for _ in range(3):
            if sync_state["stop_requested"]:
                break
            try:
                if captions_changed:
                    media_list = []
                    for item, caption_html in zip(group, rewritten_captions):
                        item_type, _ = get_msg_meta(item, mode)
                        media_ref = get_media_reference(item, item_type)
                        if not media_ref:
                            raise ValueError(f"媒体组消息缺少可复用 file_id: {item.id}")
                        media_cls = PYRO_MEDIA_CLS.get(item_type, PYRO_MEDIA_CLS["document"])
                        media_list.append(media_cls(media=media_ref, caption=caption_html, parse_mode=ParseMode.HTML))
                    kwargs = {"chat_id": target_id, "media": media_list}
                    if reply_to_id:
                        kwargs["reply_to_message_id"] = reply_to_id
                    if quote_data and reply_to_id:
                        kwargs["quote_text"] = quote_data["text"]
                        if quote_data.get("entities"):
                            kwargs["quote_entities"] = quote_data["entities"]
                    copied_msgs = await safe_execute(app.send_media_group(**kwargs))
                else:
                    kwargs = {"chat_id": target_id, "from_chat_id": source_id, "message_id": group[0].id}
                    if reply_to_id:
                        kwargs["reply_to_message_id"] = reply_to_id
                    if quote_data and reply_to_id:
                        kwargs["quote_text"] = quote_data["text"]
                        if quote_data.get("entities"):
                            kwargs["quote_entities"] = quote_data["entities"]
                    copied_msgs = await safe_execute(app.copy_media_group(**kwargs))
                for orig_m, new_m in zip(group, copied_msgs):
                    await record_success(source_id, orig_m.id, new_m.id, force_send=force_send)
                if captions_changed:
                    await db.add_msg_log("API_GROUP_CAPTION_REWRITE", f"原始:[{source_id}] 组首ID:{group[0].id} | 命中 {caption_rewrite_count} 个 caption 链接改写")
                if quote_data and reply_to_id:
                    await db.add_msg_log("API_QUOTE_GROUP_SEND", f"原始:[{source_id}] 组首ID:{group[0].id} | 已按引用回复发送媒体组")
                break
            except TypeError as e:
                if "topics" in str(e):
                    for item in group:
                        await record_success(source_id, item.id, 0, force_send=force_send)
                    break
            except Exception as e:
                if "STOP_REQUESTED" in str(e):
                    raise e
    else:
        downloaded_files = []
        dl_success = False
        for _ in range(3):
            if sync_state["stop_requested"]:
                break
            try:
                sem = asyncio.Semaphore(3)

                async def dl_album_item(m_item, idx):
                    async with sem:
                        return await safe_execute(app.download_media(m_item, file_name=f"{TEMP_DIR}/", progress=create_progress_callback(f"并发下载 [{idx}]")))

                results = await asyncio.gather(*[dl_album_item(item, index + 1) for index, item in enumerate(group)], return_exceptions=True)
                if any(isinstance(r, Exception) for r in results):
                    if any("STOP_REQUESTED" in str(r) for r in results):
                        sync_state["stop_requested"] = True
                    await asyncio.sleep(2)
                    continue
                downloaded_files = [(item, path) for item, path in zip(group, results) if isinstance(path, str)]
                dl_success = True
                break
            except Exception:
                pass

        if not dl_success or sync_state["stop_requested"]:
            for _, path in downloaded_files:
                try:
                    os.remove(path)
                except Exception:
                    pass
            return

        file_sizes = [os.path.getsize(path) for _, path in downloaded_files]
        upload_target = await safe_execute(resolve_clone_upload_target(sender, app, file_sizes))
        actual_sender = upload_target["sender"]
        cls_map = AIO_MEDIA_CLS if actual_sender == "bot" else PYRO_MEDIA_CLS
        client = upload_target["client"]
        parse_mode = upload_target["parse_mode"]

        sent_group_success = False
        for _ in range(3):
            if sync_state["stop_requested"]:
                break
            try:
                sync_state["current_text"] = f"上传相册... [{upload_target['label']}]"
                media_list = []
                for (item, path), caption_html in zip(downloaded_files, rewritten_captions):
                    item_type, _ = get_msg_meta(item, mode)
                    media_cls = cls_map.get(item_type, cls_map["document"])
                    media_list.append(media_cls(media=FSInputFile(path) if actual_sender == "bot" else path, caption=caption_html, parse_mode=parse_mode))
                send_kwargs = {"chat_id": target_id, "media": media_list}
                if reply_to_id:
                    send_kwargs["reply_to_message_id"] = reply_to_id
                if quote_data and reply_to_id:
                    if actual_sender == "bot":
                        send_kwargs.pop("reply_to_message_id", None)
                        send_kwargs["reply_parameters"] = ReplyParameters(
                            message_id=reply_to_id,
                            quote=quote_data["text"],
                            quote_position=quote_data.get("position"),
                        )
                    else:
                        send_kwargs["quote_text"] = quote_data["text"]
                        if quote_data.get("entities"):
                            send_kwargs["quote_entities"] = quote_data["entities"]
                sent_msgs = await safe_execute(client.send_media_group(**send_kwargs))
                for orig_m, new_m in zip(group, sent_msgs):
                    await record_success(source_id, orig_m.id, new_m.message_id if actual_sender == "bot" else new_m.id, force_send=force_send)
                if actual_sender == "bot":
                    await bot_engine.note_upload_success(client, sum(file_sizes))
                sent_group_success = True
                if captions_changed:
                    await db.add_msg_log("CLONE_GROUP_CAPTION_REWRITE", f"原始:[{source_id}] 组首ID:{group[0].id} | 命中 {caption_rewrite_count} 个 caption 链接改写")
                if quote_data and reply_to_id:
                    await db.add_msg_log("CLONE_QUOTE_GROUP_SEND", f"原始:[{source_id}] 组首ID:{group[0].id} | 已按引用回复发送媒体组")
                break
            except Exception as e:
                if "STOP_REQUESTED" in str(e):
                    raise e
                await asyncio.sleep(2)

        if not sync_state["stop_requested"] and sender == "bot" and actual_sender == "bot" and not sent_group_success:
            first_id = group[0].id if group else 0
            await db.add_msg_log("BOT_FALLBACK", f"原始:[{source_id}] 组首ID:{first_id} | {upload_target['label']} 上传失败，回退辅助账号重传")
            media_list = []
            for (item, path), caption_html in zip(downloaded_files, rewritten_captions):
                item_type, _ = get_msg_meta(item, mode)
                media_cls = PYRO_MEDIA_CLS.get(item_type, PYRO_MEDIA_CLS["document"])
                media_list.append(media_cls(media=path, caption=caption_html, parse_mode=ParseMode.HTML))
            send_kwargs = {"chat_id": target_id, "media": media_list}
            if reply_to_id:
                send_kwargs["reply_to_message_id"] = reply_to_id
            if quote_data and reply_to_id:
                send_kwargs["quote_text"] = quote_data["text"]
                if quote_data.get("entities"):
                    send_kwargs["quote_entities"] = quote_data["entities"]
            sent_msgs = await safe_execute(app.send_media_group(**send_kwargs))
            for orig_m, new_m in zip(group, sent_msgs):
                await record_success(source_id, orig_m.id, new_m.id, force_send=force_send)

        for _, path in downloaded_files:
            try:
                os.remove(path)
            except Exception:
                pass

    await asyncio.sleep(safe_delay)


async def process_json_sync(target_id_raw, json_path, safe_delay, force_send, json_source_username=""):
    if not json_path or not os.path.exists(json_path):
        await db.add_log("ERROR", "JSON 文件不存在或路径无效")
        return

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        await db.add_log("ERROR", f"JSON 解析失败: {e}")
        return

    messages = data.get("messages", [])
    json_dir = os.path.dirname(os.path.abspath(json_path))
    target_id = await resolve_chat_id(target_id_raw)
    link_context = await build_link_rewrite_context(0, target_id, source_username_override=json_source_username)
    sync_state["total"] = len(messages)
    warned_media_groups = False
    warned_link_rewrite = False

    for msg in messages:
        if sync_state["stop_requested"]:
            break
        if msg.get("type") != "message":
            continue

        msg_id = msg.get("id", 0)
        text = build_json_text(msg)
        if text and not warned_link_rewrite and re.search(r"https?://t\.me/(?:c/)?[^/\s]+/\d+", text):
            warned_link_rewrite = True
            if json_source_username:
                await db.add_msg_log("JSON_INFO", f"JSON 导入已启用链接改写，源频道用户名: @{str(json_source_username).lstrip('@')}")
            else:
                await db.add_msg_log("JSON_WARN", "JSON 导入检测到消息链接引用；未填写源频道用户名，无法安全改写源频道链接")
        if await update_state_and_check_skip(0, msg_id, text[:50] or "[媒体]", force_send=force_send):
            continue
        text, rewrite_count = await rewrite_message_links(text, 0, link_context)
        if rewrite_count:
            await db.add_msg_log("JSON_LINK_REWRITE", f"消息ID:{msg_id} | 命中 {rewrite_count} 个链接改写")

        media_group_hint = msg.get("media_group_id") or msg.get("grouped_id") or msg.get("media_group")
        if media_group_hint and not warned_media_groups:
            warned_media_groups = True
            await db.add_msg_log("JSON_WARN", "JSON 导入检测到媒体组标记，当前只能按单条消息发送，无法原样还原媒体组")

        media_path, media_type, media_note = resolve_json_media(msg, json_dir)
        reply_to_id = await resolve_reply_target(0, get_reply_source_msg_id(msg, "json"), "JSON", msg_id)

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

            await record_success(0, msg_id, sent_id, force_send=force_send)
            await db.add_msg_log("JSON_SEND", f"消息ID:{msg_id} | 目标:[{target_id}] 新ID:{sent_id} | 上传成功")
        except Exception as e:
            if sync_state["stop_requested"]:
                break
            await db.add_log("ERROR", f"JSON 消息上传失败 ID {msg_id}: {e}")

        await asyncio.sleep(safe_delay)


async def process_master_sync(mode: str, sender: str, source_id_raw: str, target_id_raw: str, delay: float, start_id: int, end_id: int, json_path: str, force_send: bool = False, json_source_username: str = ""):
    safe_delay = max(0.5, float(delay))
    if mode == "api":
        sender = "user"
    elif mode == "json":
        sender = "bot"

    sync_state.update({"is_syncing": True, "mode": mode.upper(), "source_id_raw": source_id_raw, "target_id_raw": target_id_raw, "delay": safe_delay, "start_id": start_id, "end_id": end_id, "json_path": json_path, "json_source_username": json_source_username, "current": 0, "skipped": 0, "total": 0, "stop_requested": False, "force_send": force_send})
    settings = await db.get_all_settings()

    try:
        source_id = await resolve_chat_id(source_id_raw)
        target_id = await resolve_chat_id(target_id_raw)
    except Exception as e:
        await db.add_log("ERROR", f"任务中止，频道有误: {e}")
        sync_state["is_syncing"] = False
        return

    if mode == "clone":
        for name in os.listdir(TEMP_DIR):
            try:
                os.remove(os.path.join(TEMP_DIR, name))
            except Exception:
                pass
        await db.add_log("INFO", "已清空 temp，准备下载")

    try:
        if mode in ["api", "clone"]:
            app, bot = bot_engine.pyro_user_app, bot_engine.aiogram_bot
            if not start_id:
                start_id = 1
            if not end_id:
                async for last_msg in app.get_chat_history(source_id, limit=1):
                    end_id = last_msg.id
            if not end_id:
                end_id = 1
            sync_state["total"] = end_id - start_id + 1

            for chunk_start in range(start_id, end_id + 1, 100):
                if sync_state["stop_requested"]:
                    break
                try:
                    msgs = await app.get_messages(source_id, list(range(chunk_start, min(chunk_start + 99, end_id) + 1)))
                except Exception:
                    continue

                filtered_msgs = []
                for msg in msgs:
                    if msg is None or msg.empty:
                        continue
                    msg_type, sync_key = get_msg_meta(msg, mode)
                    if settings.get(sync_key, "1") == "0":
                        continue
                    filtered_msgs.append(msg)

                grouped_msgs = []
                current_group = []
                for msg in filtered_msgs:
                    if msg.media_group_id:
                        if not current_group or current_group[0].media_group_id == msg.media_group_id:
                            current_group.append(msg)
                        else:
                            grouped_msgs.append(current_group)
                            current_group = [msg]
                    else:
                        if current_group:
                            grouped_msgs.append(current_group)
                            current_group = []
                        grouped_msgs.append([msg])
                if current_group:
                    grouped_msgs.append(current_group)

                for group in grouped_msgs:
                    if sync_state["stop_requested"]:
                        break
                    if len(group) == 1:
                        await sync_single_message(mode, sender, app, bot, source_id, target_id, group[0], safe_delay, force_send)
                    else:
                        await sync_media_group(mode, sender, app, bot, source_id, target_id, group, safe_delay, force_send)
        else:
            await process_json_sync(target_id_raw, json_path, safe_delay, force_send, json_source_username=json_source_username)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        await db.add_log("ERROR", f"同步中断: {e}")
    finally:
        sync_state["is_syncing"] = False
        sync_state["stop_requested"] = False


async def resolve_chat_id(chat_ref: str) -> int:
    if not chat_ref:
        raise ValueError("频道引用为空")
    try:
        if chat_ref.lstrip("-").isdigit():
            return int(chat_ref)
        if chat_ref.startswith("@"):
            return int((await bot_engine.aiogram_bot.get_chat(chat_ref)).id)
        if "t.me/" in chat_ref:
            username = chat_ref.split("t.me/")[-1].split("/")[0].split("?")[0]
            if not username.startswith("@"):
                username = f"@{username}"
            return int((await bot_engine.aiogram_bot.get_chat(username)).id)
    except Exception as e:
        raise ValueError(f"无法解析频道 {chat_ref}: {e}")
    raise ValueError(f"无法解析频道 {chat_ref}")
