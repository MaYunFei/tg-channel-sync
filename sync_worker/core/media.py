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


def get_media_reference(msg, msg_type):
    media_obj = getattr(msg, msg_type, None)
    return getattr(media_obj, "file_id", None) if media_obj else None


def get_reply_source_msg_id(msg, mode):
    if mode == "json":
        return msg.get("reply_to_message_id")
    return getattr(msg, "reply_to_message_id", None)


def has_media_spoiler(msg, msg_type, mode):
    """检查消息是否有媒体遮罩（spoiler）"""
    if mode == "json":
        # JSON 导出格式中检查 media_type 是否包含 spoiler 标记
        # 或者检查特定字段
        return False  # JSON 导出通常不包含 spoiler 信息
    
    # aiogram/pyrofork 消息对象
    if msg_type in ["photo", "video", "animation"]:
        media_obj = getattr(msg, msg_type, None)
        if media_obj:
            # aiogram 3.x 使用 has_media_spoiler
            return getattr(media_obj, "has_media_spoiler", False) or getattr(msg, "has_media_spoiler", False)
    
    return False


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
