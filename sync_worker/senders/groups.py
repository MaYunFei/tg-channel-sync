from __future__ import annotations

import os

from aiogram.types import FSInputFile
from aiogram.types import InputMediaAudio as AioAudio
from aiogram.types import InputMediaDocument as AioDocument
from aiogram.types import InputMediaPhoto as AioPhoto
from aiogram.types import InputMediaVideo as AioVideo
from pyrogram.enums import ParseMode
from pyrogram.types import InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo

from ..core.media import extract_upload_metadata
from ..core.progress import ProgressFSInputFile, UploadProgressTracker, format_upload_label
from ..core.text import normalize_bot_html, normalize_pyro_html


AIO_MEDIA_CLS = {"photo": AioPhoto, "video": AioVideo, "audio": AioAudio, "document": AioDocument}
PYRO_MEDIA_CLS = {"photo": InputMediaPhoto, "video": InputMediaVideo, "audio": InputMediaAudio, "document": InputMediaDocument}


def _item_id(item):
    if isinstance(item, dict):
        return item.get("id") or item.get("message_id")
    return getattr(item, "id", None) or getattr(item, "message_id", None)


def build_bot_media_group(downloaded_files, rewritten_captions, thumbnail_paths, total_bytes, label, spoiler_flags=None):
    """
    构建 aiogram 媒体组
    
    Args:
        downloaded_files: [(item, path, item_type), ...]
        rewritten_captions: [caption1, caption2, ...]
        thumbnail_paths: {item_id: thumbnail_path, ...}
        total_bytes: 总字节数
        label: 标签
        spoiler_flags: [bool, bool, ...] 每个媒体是否有遮罩
    """
    tracker = UploadProgressTracker(f"上传媒体组 [{label}]", total_bytes)
    media_list = []
    spoiler_flags = spoiler_flags or []
    
    for index, ((item, path, item_type), caption_html) in enumerate(zip(downloaded_files, rewritten_captions), start=1):
        media_cls = AIO_MEDIA_CLS.get(item_type, AIO_MEDIA_CLS["document"])
        file_label = format_upload_label(item_type, path, index=index, total=len(downloaded_files))
        media_input = ProgressFSInputFile(path, tracker, file_label)
        thumbnail_path = thumbnail_paths.get(_item_id(item))
        thumbnail_input = FSInputFile(thumbnail_path) if thumbnail_path and os.path.exists(thumbnail_path) else None
        
        media_kwargs = {"media": media_input, "caption": normalize_bot_html(caption_html), "parse_mode": "HTML"}
        media_kwargs.update(extract_upload_metadata(item, item_type))
        
        if index - 1 < len(spoiler_flags) and spoiler_flags[index - 1]:
            if item_type in {"photo", "video"}:
                media_kwargs["has_spoiler"] = True
        
        if item_type in {"video", "document"} and thumbnail_input is not None:
            media_kwargs["thumbnail"] = thumbnail_input
        media_list.append(media_cls(**media_kwargs))
    
    return tracker, media_list


def build_user_media_group(downloaded_files, rewritten_captions, thumbnail_paths, spoiler_flags=None):
    """
    构建 pyrofork 媒体组
    
    Args:
        downloaded_files: [(item, path, item_type), ...]
        rewritten_captions: [caption1, caption2, ...]
        thumbnail_paths: {item_id: thumbnail_path, ...}
        spoiler_flags: [bool, bool, ...] 每个媒体是否有遮罩
    """
    media_list = []
    spoiler_flags = spoiler_flags or []
    
    for index, (item, path, item_type) in enumerate(downloaded_files):
        media_cls = PYRO_MEDIA_CLS.get(item_type, PYRO_MEDIA_CLS["document"])
        thumbnail_path = thumbnail_paths.get(_item_id(item))
        
        media_kwargs = {
            "media": path,
            "caption": normalize_pyro_html(rewritten_captions[index]),
            "parse_mode": ParseMode.HTML,
        }
        media_kwargs.update(extract_upload_metadata(item, item_type))
        
        if index < len(spoiler_flags) and spoiler_flags[index]:
            if item_type in {"photo", "video"}:
                media_kwargs["has_spoiler"] = True
        
        if item_type in {"video", "document"} and thumbnail_path and os.path.exists(thumbnail_path):
            media_kwargs["thumb"] = thumbnail_path
        
        media_list.append(media_cls(**media_kwargs))
    
    return media_list
