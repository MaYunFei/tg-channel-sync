from __future__ import annotations

import os

from aiogram.types import ReplyParameters
from pyrogram.enums import ParseMode
from pyrogram.types import InputMediaAudio
from pyrogram.types import InputMediaDocument
from pyrogram.types import InputMediaPhoto
from pyrogram.types import InputMediaVideo

import bot_engine
from services.sync_services import build_link_rewrite_context, rewrite_message_links


TYPE_MAP = {
    "photo": "sync_photo",
    "video": "sync_video",
    "animation": "sync_gif",
    "audio": "sync_audio",
    "voice": "sync_voice",
    "document": "sync_document",
    "sticker": "sync_sticker",
}
PYRO_MEDIA_CLS = {
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
    "audio": InputMediaAudio,
    "document": InputMediaDocument,
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


async def rewrite_media_group_captions(source_id, target_id, group, source_username_override=None):
    link_context = await build_link_rewrite_context(
        bot_engine.aiogram_bot,
        source_id,
        target_id,
        source_username_override=source_username_override,
    )
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


async def dynamic_send(
    client,
    msg_type,
    chat_id,
    file_ref,
    caption,
    parse_mode,
    reply_to_message_id=None,
    quote_data=None,
):
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


def get_reply_source_msg_id(msg, mode):
    if mode == "json":
        return msg.get("reply_to_message_id")
    return getattr(msg, "reply_to_message_id", None)


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
