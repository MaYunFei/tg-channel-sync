from __future__ import annotations

import os


TYPE_MAP = {
    "photo": "sync_photo",
    "video": "sync_video",
    "animation": "sync_gif",
    "audio": "sync_audio",
    "voice": "sync_voice",
    "document": "sync_document",
    "sticker": "sync_sticker",
}

JSON_VIDEO_VISUAL_FIELDS = ("thumbnail", "width", "height", "duration_seconds")


def is_json_video_file_visual(msg: dict) -> bool:
    """Return True when Telegram JSON video_file has enough metadata to send as video."""
    if not isinstance(msg, dict):
        return False
    if msg.get("media_type") != "video_file" or not msg.get("file"):
        return False
    mime_type = str(msg.get("mime_type") or "").lower()
    if mime_type and not mime_type.startswith("video/"):
        return False
    return any(bool(msg.get(field)) for field in JSON_VIDEO_VISUAL_FIELDS)


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
        "video_file": ("video", "sync_video") if is_json_video_file_visual(msg) else ("document", "sync_document"),
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


def get_media_reference(msg, msg_type):
    media_obj = getattr(msg, msg_type, None)
    return getattr(media_obj, "file_id", None) if media_obj else None


def extract_upload_metadata(item, msg_type):
    if not item or msg_type == "text":
        return {}

    if isinstance(item, dict):
        media_obj = item
    else:
        media_obj = getattr(item, msg_type, None) or item

    metadata = {}

    def _copy_number(field):
        value = getattr(media_obj, field, None) if not isinstance(media_obj, dict) else media_obj.get(field)
        if value is not None:
            metadata[field] = value

    def _copy_text(field):
        value = getattr(media_obj, field, None) if not isinstance(media_obj, dict) else media_obj.get(field)
        if value:
            metadata[field] = value

    if msg_type == "video":
        _copy_number("duration")
        _copy_number("width")
        _copy_number("height")
        metadata["supports_streaming"] = True
    elif msg_type == "animation":
        _copy_number("duration")
        _copy_number("width")
        _copy_number("height")
    elif msg_type == "audio":
        _copy_number("duration")
        _copy_text("performer")
        _copy_text("title")

    return metadata


def get_reply_source_msg_id(msg, mode):
    if mode == "json":
        return msg.get("reply_to_message_id")
    return getattr(msg, "reply_to_message_id", None)


def has_media_spoiler(msg, msg_type, mode):
    """检查消息是否有媒体遮罩（spoiler）"""
    if mode == "json":
        return bool(msg.get("media_spoiler") or msg.get("has_spoiler") or msg.get("has_media_spoiler"))

    if msg_type not in {"photo", "video", "animation"}:
        return False

    media_obj = getattr(msg, msg_type, None)
    raw_media = getattr(getattr(msg, "raw", None), "media", None)
    candidates = (msg, media_obj, getattr(msg, "media", None), raw_media)
    spoiler_fields = ("has_media_spoiler", "has_spoiler", "spoiler")
    return any(
        bool(getattr(candidate, field, False))
        for candidate in candidates
        if candidate
        for field in spoiler_fields
    )


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
            return file_path, "video" if is_json_video_file_visual(msg) else "document", None
        if media_type == "animation":
            return file_path, "animation", None
        if media_type == "audio_file":
            return file_path, "audio", None
        if media_type == "voice_message":
            return file_path, "voice", None
        return file_path, "document", None
    return None, None, None
