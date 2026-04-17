from __future__ import annotations

import html
import os
import re
import time

from aiogram.types import FSInputFile
from aiogram.types import ReplyParameters
from pyrogram.enums import ParseMode
from pyrogram.types import InputMediaAudio
from pyrogram.types import InputMediaDocument
from pyrogram.types import InputMediaPhoto
from pyrogram.types import InputMediaVideo

import bot_engine
from services.sync_services import build_link_rewrite_context, rewrite_message_links
from .state import sync_state


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
CLONE_UPLOAD_LABELS = {
    "photo": "上传图片",
    "video": "上传视频",
    "animation": "上传动图",
    "audio": "上传音频",
    "voice": "上传语音",
    "sticker": "上传贴纸",
    "document": "上传文件",
}
BOT_HTML_TAG_ALIASES = (
    (re.compile(r"<(/?)spoiler>"), r"<\1tg-spoiler>"),
)


class UploadProgressTracker:
    def __init__(self, label: str, total_bytes: int):
        self.label = label
        self.total_bytes = max(1, int(total_bytes or 0))
        self.started_at = time.time()
        self.last_update_at = 0.0

    def _render(self, sent_bytes: int, file_label: str) -> None:
        sent_bytes = max(0, min(int(sent_bytes or 0), self.total_bytes))
        now = time.time()
        if sent_bytes < self.total_bytes and now - self.last_update_at < 0.2:
            return
        elapsed = max(0.001, now - self.started_at)
        percent = min(100.0, sent_bytes / self.total_bytes * 100)
        sent_mb = sent_bytes / (1024 * 1024)
        total_mb = self.total_bytes / (1024 * 1024)
        speed_mb = sent_mb / elapsed
        sync_state["current_text"] = (
            f"{file_label}\n{self.label} {percent:.1f}% ({sent_mb:.1f}/{total_mb:.1f} MB, {speed_mb:.1f} MB/s)"
        )
        self.last_update_at = now

    def advance(self, chunk_size: int, file_label: str) -> None:
        sent_bytes = min(self.total_bytes, getattr(self, "_sent_bytes", 0) + max(0, int(chunk_size or 0)))
        self._sent_bytes = sent_bytes
        self._render(sent_bytes, file_label)

    def set_absolute(self, sent_bytes: int, file_label: str, total_bytes: int | None = None) -> None:
        if total_bytes:
            self.total_bytes = max(1, int(total_bytes))
        self._sent_bytes = max(0, min(int(sent_bytes or 0), self.total_bytes))
        self._render(self._sent_bytes, file_label)


class ProgressFSInputFile(FSInputFile):
    def __init__(self, path: str, tracker: UploadProgressTracker, file_label: str, filename: str | None = None):
        super().__init__(path, filename=filename)
        self.tracker = tracker
        self.file_label = file_label

    async def read(self, bot):
        async for chunk in super().read(bot):
            self.tracker.advance(len(chunk), self.file_label)
            yield chunk


def format_upload_label(msg_type: str | None, media_path: str, *, index: int | None = None, total: int | None = None) -> str:
    action = CLONE_UPLOAD_LABELS.get(str(msg_type or ""), "上传媒体")
    name = os.path.basename(media_path)
    if index is not None and total is not None:
        return f"{action} {index}/{total}: {name}"
    return f"{action}: {name}"


def build_pyro_progress_callback(
    tracker: UploadProgressTracker,
    file_label: str,
    *,
    total_bytes: int | None = None,
):
    def _callback(current: int, total: int, *args):
        tracker.set_absolute(current, file_label, total_bytes or total)

    return _callback


def normalize_bot_html(text: str | None) -> str:
    normalized = str(text or "")
    for pattern, replacement in BOT_HTML_TAG_ALIASES:
        normalized = pattern.sub(replacement, normalized)
    return normalized


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
        rewritten_caption = normalize_bot_html(rewritten_caption)
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
    progress=None,
    progress_args=None,
    thumbnail=None,
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
        kwargs["caption"] = normalize_bot_html(caption) if bot_engine.is_bot_client(client) else caption
        kwargs[msg_type if hasattr(client, method_name) else "document"] = file_ref
        if thumbnail is not None:
            if bot_engine.is_bot_client(client):
                kwargs["thumbnail"] = thumbnail
                if msg_type == "video":
                    kwargs["supports_streaming"] = True
            else:
                kwargs["thumb"] = thumbnail
    else:
        kwargs["text"] = normalize_bot_html(caption) if bot_engine.is_bot_client(client) else caption
    if not bot_engine.is_bot_client(client) and progress is not None:
        kwargs["progress"] = progress
        if progress_args:
            kwargs["progress_args"] = progress_args
    return await method(**kwargs)


async def resolve_clone_upload_target(sender, app, file_sizes, allow_user_fallback: bool = True, wait_for_available_bot: bool = True):
    total_size = sum(file_sizes)
    if sender != "bot":
        return {"sender": "user", "client": app, "parse_mode": ParseMode.HTML, "label": "辅助账号", "bytes": total_size}
    if not bot_engine.should_upload_via_bot(max(file_sizes) if file_sizes else 0):
        if not allow_user_fallback:
            raise RuntimeError("当前文件超出 Bot 上传限制，请开启“Bot 上传失败时回退辅助账号重传”或切换为辅助账号发送")
        return {"sender": "user", "client": app, "parse_mode": ParseMode.HTML, "label": "辅助账号", "bytes": total_size}
    selection = await bot_engine.acquire_upload_bot(total_size, wait_if_unavailable=wait_for_available_bot)
    if selection is None:
        if allow_user_fallback:
            return {"sender": "user", "client": app, "parse_mode": ParseMode.HTML, "label": "辅助账号", "bytes": total_size}
        raise RuntimeError("当前没有可用 Bot 可继续上传，请等待冷却结束或开启“Bot 上传失败时回退辅助账号重传”")
    return {"sender": "bot", "client": selection["client"], "parse_mode": "HTML", "label": selection["label"], "bytes": total_size}


def get_reply_source_msg_id(msg, mode):
    if mode == "json":
        return msg.get("reply_to_message_id")
    return getattr(msg, "reply_to_message_id", None)


def build_json_text(msg):
    text = msg.get("text", "")
    if not isinstance(text, list):
        return html.escape(str(text or ""))

    def render_entity(item):
        if isinstance(item, str):
            return html.escape(item)
        if not isinstance(item, dict):
            return html.escape(str(item))

        entity_type = item.get("type", "plain")
        raw_text = str(item.get("text", ""))
        entity_text = html.escape(raw_text)
        href = str(item.get("href", "") or "")

        wrappers = {
            "bold": ("<b>", "</b>"),
            "italic": ("<i>", "</i>"),
            "underline": ("<u>", "</u>"),
            "strikethrough": ("<s>", "</s>"),
            "spoiler": ("<tg-spoiler>", "</tg-spoiler>"),
            "code": ("<code>", "</code>"),
            "pre": ("<pre>", "</pre>"),
            "blockquote": ("<blockquote>", "</blockquote>"),
        }

        if entity_type == "text_link" and href:
            return f'<a href="{html.escape(href, quote=True)}">{entity_text}</a>'
        if entity_type == "link":
            return f'<a href="{html.escape(raw_text, quote=True)}">{entity_text}</a>'
        if entity_type in wrappers:
            start, end = wrappers[entity_type]
            return f"{start}{entity_text}{end}"
        return entity_text

    parts = []
    for item in text:
        parts.append(render_entity(item))
    return "".join(parts)


def resolve_json_media(msg, json_dir):
    if msg.get("photo"):
        return os.path.join(json_dir, msg["photo"]), "photo", None
    if msg.get("video"):
        return os.path.join(json_dir, msg["video"]), "video", None
    media_type = msg.get("media_type")
    if msg.get("audio"):
        return os.path.join(json_dir, msg["audio"]), "audio", None
    if msg.get("voice"):
        return os.path.join(json_dir, msg["voice"]), "voice", None
    if msg.get("file"):
        file_path = os.path.join(json_dir, msg["file"])
        if media_type == "sticker":
            return file_path, "sticker", None
        if media_type == "video_file":
            return file_path, "video", None
        if media_type == "animation":
            return file_path, "animation", None
        if media_type == "audio_file":
            return file_path, "audio", None
        if media_type == "voice_message":
            return file_path, "voice", None
        return file_path, "document", None
    return None, None, None
