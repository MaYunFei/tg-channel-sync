from __future__ import annotations

import os

from aiogram.types import FSInputFile
from aiogram.types import InputMediaAudio as AioAudio
from aiogram.types import InputMediaDocument as AioDocument
from aiogram.types import InputMediaPhoto as AioPhoto
from aiogram.types import InputMediaVideo as AioVideo
from pyrogram.enums import ParseMode
from pyrogram.types import InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo

from ..core.progress import ProgressFSInputFile, UploadProgressTracker, format_upload_label


AIO_MEDIA_CLS = {"photo": AioPhoto, "video": AioVideo, "audio": AioAudio, "document": AioDocument}
PYRO_MEDIA_CLS = {"photo": InputMediaPhoto, "video": InputMediaVideo, "audio": InputMediaAudio, "document": InputMediaDocument}


def build_bot_media_group(downloaded_files, rewritten_captions, thumbnail_paths, total_bytes, label):
    tracker = UploadProgressTracker(f"上传媒体组 [{label}]", total_bytes)
    media_list = []
    for index, ((item, path, item_type), caption_html) in enumerate(zip(downloaded_files, rewritten_captions), start=1):
        media_cls = AIO_MEDIA_CLS.get(item_type, AIO_MEDIA_CLS["document"])
        file_label = format_upload_label(item_type, path, index=index, total=len(downloaded_files))
        media_input = ProgressFSInputFile(path, tracker, file_label)
        thumbnail_path = thumbnail_paths.get(getattr(item, "id", None) or item.get("id"))
        thumbnail_input = FSInputFile(thumbnail_path) if thumbnail_path and os.path.exists(thumbnail_path) else None
        media_kwargs = {"media": media_input, "caption": caption_html, "parse_mode": "HTML"}
        if item_type in {"video", "document"} and thumbnail_input is not None:
            media_kwargs["thumbnail"] = thumbnail_input
        if item_type == "video":
            media_kwargs["supports_streaming"] = True
        media_list.append(media_cls(**media_kwargs))
    return tracker, media_list


def build_user_media_group(downloaded_files, rewritten_captions, thumbnail_paths):
    media_list = []
    for item, path, item_type in downloaded_files:
        media_cls = PYRO_MEDIA_CLS.get(item_type, PYRO_MEDIA_CLS["document"])
        thumbnail_path = thumbnail_paths.get(getattr(item, "id", None) or item.get("id"))
        media_kwargs = {"media": path, "caption": rewritten_captions[len(media_list)], "parse_mode": ParseMode.HTML}
        if item_type in {"video", "document"} and thumbnail_path and os.path.exists(thumbnail_path):
            media_kwargs["thumb"] = thumbnail_path
        media_list.append(media_cls(**media_kwargs))
    return media_list
