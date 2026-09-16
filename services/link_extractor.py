from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import logging
from typing import Any, Optional

from pyrogram.enums import ParseMode as PyroParseMode

logger = logging.getLogger("link_extractor")

LINK_REGEX = re.compile(
    r"https?://(?:www\.)?(?:t\.me|telegram\.me)/(?:c/(\d+)|([a-zA-Z0-9_]+))/(\d+)(?:\?.*)?",
    re.IGNORECASE,
)

SETTING_TARGET_KEY = "link_extractor_target"
SETTING_ADMINS_KEY = "link_extractor_admins"


def parse_telegram_links(text: str) -> list[dict[str, Any]]:
    """
    解析文本中的所有 Telegram 消息链接。
    返回列表: [{'url': str, 'chat_id': int | str, 'message_id': int}, ...]
    """
    results = []
    if not text:
        return results

    matches = LINK_REGEX.findall(text)
    for private_id, public_name, msg_id_str in matches:
        try:
            msg_id = int(msg_id_str)
        except ValueError:
            continue

        if private_id:
            # 私有频道/群组链接: t.me/c/1234567890/456 -> 真实 chat_id 为 -1001234567890
            chat_id = int(f"-100{private_id}")
            results.append({
                "chat_id": chat_id,
                "message_id": msg_id,
                "is_private": True,
            })
        elif public_name:
            # 公开频道/群组链接: t.me/channel_name/456
            results.append({
                "chat_id": public_name,
                "message_id": msg_id,
                "is_private": False,
            })

    return results


async def get_default_target() -> str:
    """获取默认投递目标，默认为 'me' (当前私聊)"""
    import database as db

    try:
        await db.init_db()
        settings = await db.get_all_settings()
        target = settings.get(SETTING_TARGET_KEY, "").strip()
        return target if target else "me"
    except Exception as exc:
        logger.warning("获取默认投递目标失败，降级为 'me': %s", exc)
        return "me"


async def set_default_target(target: str) -> None:
    """保存默认投递目标"""
    import database as db

    try:
        await db.init_db()
        await db.update_settings({SETTING_TARGET_KEY: target.strip()})
    except Exception as exc:
        logger.error("保存默认投递目标失败: %s", exc)


async def get_allowed_admins() -> set[int]:
    """获取允许使用该提取器的管理员 User ID 列表"""
    import database as db

    try:
        await db.init_db()
        settings = await db.get_all_settings()
        admins_str = settings.get(SETTING_ADMINS_KEY, "").strip()
        admins = set()
        if admins_str:
            for part in admins_str.split(","):
                part = part.strip()
                if part.isdigit():
                    admins.add(int(part))
        return admins
    except Exception as exc:
        logger.warning("获取管理员列表失败: %s", exc)
        return set()


async def add_allowed_admin(user_id: int) -> None:
    """添加管理员 ID"""
    import database as db

    try:
        await db.init_db()
        admins = await get_allowed_admins()
        admins.add(user_id)
        await db.update_settings({SETTING_ADMINS_KEY: ",".join(str(uid) for uid in sorted(admins))})
    except Exception as exc:
        logger.error("添加管理员 ID 失败: %s", exc)


async def is_user_authorized(user_id: int, pyro_client=None) -> bool:
    """
    鉴权：
    1. 若辅助账号已登录，辅助账号本人的 ID 自动为管理员；
    2. 若系统配置的 link_extractor_admins 中包含该 ID；
    3. 若系统当前 link_extractor_admins 为空，且辅助账号未登录，默认暂不开放（需私聊绑定或通过 WebUI 登录）。
    """
    if pyro_client:
        try:
            me = getattr(pyro_client, "me", None)
            if me and getattr(me, "id", None) == user_id:
                return True
            if hasattr(pyro_client, "get_me") and getattr(pyro_client, "is_connected", False):
                me = await pyro_client.get_me()
                if me and getattr(me, "id", None) == user_id:
                    return True
        except Exception:
            pass

    try:
        allowed = await get_allowed_admins()
        if user_id in allowed:
            return True
    except Exception:
        pass

    # 如果系统完全没有设置任何管理员，且辅助账号也未连接，允许通过环境变量 ADMIN_USER_ID
    env_admin = os.environ.get("ADMIN_USER_ID", "").strip()
    if env_admin and env_admin.isdigit() and int(env_admin) == user_id:
        return True

    return False


def _build_download_filename(msg: Any, item_attr: Any, media_type: str, idx: int | None = None) -> str:
    """构造带有正确扩展名的本地文件名，避免 Telegram 上传报 PHOTO_EXT_INVALID 等错误"""
    original_name = str(getattr(item_attr, "file_name", "") or "").strip()
    safe_name = re.sub(r'[<>:"/\\|?*]+', "_", original_name)
    base_name, ext = os.path.splitext(safe_name)
    if not ext:
        default_ext = {
            "photo": ".jpg",
            "video": ".mp4",
            "animation": ".mp4",
            "audio": ".mp3",
            "voice": ".ogg",
            "sticker": ".webp",
            "document": "",
        }
        ext = default_ext.get(media_type, "")
    if not base_name:
        mid = getattr(msg, "id", "media")
        base_name = f"{media_type}_{mid}"
    prefix = f"{idx}_" if idx is not None else ""
    return f"{prefix}{base_name}{ext}"


def _get_temp_dir() -> str:
    temp_base = os.path.join(os.getcwd(), "temp", "link_extractor")
    os.makedirs(temp_base, exist_ok=True)
    return tempfile.mkdtemp(dir=temp_base)


async def _download_media_thumb(pyro_app: Any, msg: Any, msg_type: str, temp_dir: str) -> str | None:
    """尝试下载原消息视频/音频的封面图 (thumbnail)"""
    media_obj = getattr(msg, msg_type, None)
    thumbs = getattr(media_obj, "thumbs", None) if media_obj else None
    if not thumbs:
        return None
    thumb = thumbs[-1]
    thumb_ref = getattr(thumb, "file_id", None)
    if not thumb_ref:
        return None
    thumb_path = os.path.join(temp_dir, f"{getattr(msg, 'id', 'media')}_{msg_type}_thumb.jpg")
    try:
        downloaded = await pyro_app.download_media(thumb_ref, file_name=thumb_path)
        if isinstance(downloaded, str) and os.path.exists(downloaded):
            return downloaded
    except Exception:
        pass
    return None


async def extract_and_forward(
    chat_id: int | str,
    message_id: int,
    target_chat_id: int | str,
    *,
    pyro_user_app: Any,
    aiogram_bot: Any,
    sender_user_id: int,
    status_callback=None,
) -> dict[str, Any]:
    """
    执行受限链接提取与投递核心逻辑。
    target_chat_id:
      - 'me' / 发送者私聊 ID: 由 Bot 发送给用户（大于50MB由辅助账号发）
      - 'saved': 辅助账号的收藏夹
      - 负数 ID / 频道 username: 指定频道
    """
    if not pyro_user_app or not getattr(pyro_user_app, "is_connected", False):
        raise RuntimeError("辅助账号未登录，无法读取受限内容。请先在 Web 控制台扫码/手机号登录辅助账号。")

    if status_callback:
        await status_callback("🔍 正在拉取源消息...")

    # 1. 获取消息
    try:
        msg = await pyro_user_app.get_messages(chat_id, message_id)
    except Exception as exc:
        raise RuntimeError(f"获取源消息失败: {exc}")

    if not msg or getattr(msg, "empty", False):
        raise RuntimeError(f"消息不存在或辅助账号无权访问（Chat: {chat_id}, Msg: {message_id}）")

    # 2. 判断是否是相册 (media_group_id)
    if msg.media_group_id:
        return await _extract_media_group(
            chat_id,
            message_id,
            msg,
            target_chat_id,
            pyro_user_app=pyro_user_app,
            aiogram_bot=aiogram_bot,
            sender_user_id=sender_user_id,
            status_callback=status_callback,
        )

    # 3. 单条消息处理
    return await _extract_single_message(
        msg,
        target_chat_id,
        pyro_user_app=pyro_user_app,
        aiogram_bot=aiogram_bot,
        sender_user_id=sender_user_id,
        status_callback=status_callback,
    )


async def _extract_single_message(
    msg: Any,
    target_chat_id: int | str,
    *,
    pyro_user_app: Any,
    aiogram_bot: Any,
    sender_user_id: int,
    status_callback=None,
) -> dict[str, Any]:
    """处理单条消息（纯文本或单媒体）"""
    # 纯文本消息
    if msg.text:
        text_html = msg.text.html if hasattr(msg.text, "html") else msg.text
        return await _dispatch_text(
            text_html,
            target_chat_id,
            sender_user_id=sender_user_id,
            pyro_user_app=pyro_user_app,
            aiogram_bot=aiogram_bot,
        )

    # 媒体消息
    temp_dir = _get_temp_dir()
    try:
        media_attr = None
        media_type = "document"
        for attr in ["photo", "video", "document", "audio", "voice", "animation", "sticker"]:
            val = getattr(msg, attr, None)
            if val is not None:
                media_attr = val
                media_type = attr
                break

        if not media_attr:
            # 可能是未知服务消息或无媒体文本
            caption = msg.caption.html if hasattr(getattr(msg, "caption", None), "html") else getattr(msg, "caption", "")
            if caption:
                return await _dispatch_text(
                    caption,
                    target_chat_id,
                    sender_user_id=sender_user_id,
                    pyro_user_app=pyro_user_app,
                    aiogram_bot=aiogram_bot,
                )
            raise RuntimeError("未识别到支持的消息内容或媒体。")

        file_size = getattr(media_attr, "file_size", 0) or 0
        file_name = _build_download_filename(msg, media_attr, media_type)

        size_mb = file_size / (1024 * 1024)
        if status_callback:
            await status_callback(f"⬇️ 正在下载媒体文件 ({size_mb:.1f} MB)...")

        file_path = await pyro_user_app.download_media(msg, file_name=os.path.join(temp_dir, file_name))
        if not file_path or not os.path.exists(file_path):
            raise RuntimeError("下载媒体文件失败，文件未落盘。")

        caption_html = msg.caption.html if hasattr(getattr(msg, "caption", None), "html") else getattr(msg, "caption", "")

        # 提取原媒体的元数据（如视频宽高 width、height、时长 duration、流式播放等）以及封面缩略图
        metadata = {}
        try:
            from sync_worker.core.media import extract_upload_metadata
            metadata = extract_upload_metadata(msg, media_type) or {}
        except Exception:
            pass

        thumb_path = await _download_media_thumb(pyro_user_app, msg, media_type, temp_dir)

        if status_callback:
            await status_callback("⬆️ 正在解除限制并上传中...")

        return await _dispatch_single_media(
            file_path,
            media_type,
            caption_html,
            target_chat_id,
            file_size=file_size,
            sender_user_id=sender_user_id,
            pyro_user_app=pyro_user_app,
            aiogram_bot=aiogram_bot,
            metadata=metadata,
            thumb_path=thumb_path,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _extract_media_group(
    chat_id: int | str,
    message_id: int,
    seed_msg: Any,
    target_chat_id: int | str,
    *,
    pyro_user_app: Any,
    aiogram_bot: Any,
    sender_user_id: int,
    status_callback=None,
) -> dict[str, Any]:
    """处理相册/媒体组"""
    if status_callback:
        await status_callback("📦 正在拉取相册媒体组...")

    try:
        group_msgs = await pyro_user_app.get_media_group(chat_id, message_id)
    except Exception:
        # 回退单个消息
        group_msgs = [seed_msg]

    temp_dir = _get_temp_dir()
    downloaded = []
    try:
        total_items = len(group_msgs)
        for idx, item in enumerate(group_msgs, start=1):
            if status_callback:
                await status_callback(f"⬇️ 正在下载相册媒体 [{idx}/{total_items}]...")
            item_type = "document"
            item_attr = None
            for attr in ["photo", "video", "document", "audio"]:
                val = getattr(item, attr, None)
                if val is not None:
                    item_attr = val
                    item_type = attr
                    break
            name = _build_download_filename(item, item_attr, item_type, idx=idx)
            dest = os.path.join(temp_dir, name)
            path = await pyro_user_app.download_media(item, file_name=dest)
            if path and os.path.exists(path):
                cap = item.caption.html if hasattr(getattr(item, "caption", None), "html") else getattr(item, "caption", "")
                
                # 提取元数据与缩略图
                item_meta = {}
                try:
                    from sync_worker.core.media import extract_upload_metadata
                    item_meta = extract_upload_metadata(item, item_type) or {}
                except Exception:
                    pass
                item_thumb = await _download_media_thumb(pyro_user_app, item, item_type, temp_dir)

                downloaded.append({
                    "path": path,
                    "type": item_type,
                    "caption": cap,
                    "size": getattr(item_attr, "file_size", 0) or os.path.getsize(path),
                    "metadata": item_meta,
                    "thumb_path": item_thumb,
                })

        if not downloaded:
            raise RuntimeError("相册内所有媒体下载失败。")

        if status_callback:
            await status_callback(f"⬆️ 正在上传相册媒体组 ({len(downloaded)} 件)...")

        return await _dispatch_media_group(
            downloaded,
            target_chat_id,
            sender_user_id=sender_user_id,
            pyro_user_app=pyro_user_app,
            aiogram_bot=aiogram_bot,
        )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def _dispatch_text(
    text_html: str,
    target_chat_id: int | str,
    *,
    sender_user_id: int,
    pyro_user_app: Any,
    aiogram_bot: Any,
) -> dict[str, Any]:
    """发送纯文本"""
    actual_target = _resolve_target(target_chat_id, sender_user_id)
    if actual_target == "saved":
        await pyro_user_app.send_message("me", text_html, parse_mode=PyroParseMode.HTML)
        return {"success": True, "target": "saved", "type": "text"}
    
    # 优先使用 aiogram_bot 发送
    if aiogram_bot:
        try:
            await aiogram_bot.send_message(actual_target, text_html, parse_mode="HTML")
            return {"success": True, "target": str(actual_target), "type": "text"}
        except Exception:
            pass

    # 回退辅助账号
    await pyro_user_app.send_message(actual_target, text_html, parse_mode=PyroParseMode.HTML)
    return {"success": True, "target": str(actual_target), "type": "text"}


async def _dispatch_single_media(
    file_path: str,
    media_type: str,
    caption_html: str,
    target_chat_id: int | str,
    *,
    file_size: int,
    sender_user_id: int,
    pyro_user_app: Any,
    aiogram_bot: Any,
    metadata: dict[str, Any] | None = None,
    thumb_path: str | None = None,
) -> dict[str, Any]:
    """发送单个媒体"""
    from aiogram.types import FSInputFile

    metadata = metadata or {}
    actual_target = _resolve_target(target_chat_id, sender_user_id)
    is_to_user = (actual_target == sender_user_id)
    is_saved = (actual_target == "saved")
    is_large = file_size > (50 * 1024 * 1024)

    # 1. 目标是收藏夹
    if is_saved:
        dest = "me"
        method = getattr(pyro_user_app, f"send_{media_type}", pyro_user_app.send_document)
        pyro_kwargs = dict(metadata)
        if thumb_path and os.path.exists(thumb_path):
            pyro_kwargs["thumb"] = thumb_path
        await method(dest, file_path, caption=caption_html, parse_mode=PyroParseMode.HTML, **pyro_kwargs)
        return {"success": True, "target": "saved", "type": media_type}

    # 2. 如果文件超过 50MB 或者是动画/贴纸等 Bot 限制类型，直接用辅助账号发送
    if is_large or not aiogram_bot:
        dest = actual_target
        method = getattr(pyro_user_app, f"send_{media_type}", pyro_user_app.send_document)
        pyro_kwargs = dict(metadata)
        if thumb_path and os.path.exists(thumb_path):
            pyro_kwargs["thumb"] = thumb_path
        await method(dest, file_path, caption=caption_html, parse_mode=PyroParseMode.HTML, **pyro_kwargs)
        return {"success": True, "target": str(actual_target), "type": media_type, "via": "user"}

    # 3. 文件小于 50MB，使用 Bot API 发送
    input_file = FSInputFile(file_path)
    kwargs = {"chat_id": actual_target, "caption": caption_html, "parse_mode": "HTML"}
    
    # 注入元数据与缩略图到 Bot API 请求中
    if media_type == "video":
        for k in ("duration", "width", "height", "supports_streaming"):
            if k in metadata:
                kwargs[k] = metadata[k]
        if thumb_path and os.path.exists(thumb_path):
            kwargs["thumbnail"] = FSInputFile(thumb_path)
    elif media_type in ("audio", "voice"):
        if "duration" in metadata:
            kwargs["duration"] = metadata["duration"]
        if thumb_path and os.path.exists(thumb_path):
            kwargs["thumbnail"] = FSInputFile(thumb_path)

    try:
        if media_type == "photo":
            await aiogram_bot.send_photo(photo=input_file, **kwargs)
        elif media_type == "video":
            await aiogram_bot.send_video(video=input_file, **kwargs)
        elif media_type == "audio":
            await aiogram_bot.send_audio(audio=input_file, **kwargs)
        elif media_type == "voice":
            await aiogram_bot.send_voice(voice=input_file, **kwargs)
        elif media_type == "animation":
            await aiogram_bot.send_animation(animation=input_file, **kwargs)
        elif media_type == "sticker":
            kwargs.pop("caption", None)
            kwargs.pop("parse_mode", None)
            await aiogram_bot.send_sticker(sticker=input_file, **kwargs)
        else:
            await aiogram_bot.send_document(document=input_file, **kwargs)
        return {"success": True, "target": str(actual_target), "type": media_type, "via": "bot"}
    except Exception as exc:
        logger.warning(f"Bot API 发送失败: {exc}")
        if is_to_user:
            raise RuntimeError(f"无法通过 Bot 发送给用户: {exc}")
        # 回退辅助账号
        method = getattr(pyro_user_app, f"send_{media_type}", pyro_user_app.send_document)
        pyro_kwargs = dict(metadata)
        if thumb_path and os.path.exists(thumb_path):
            pyro_kwargs["thumb"] = thumb_path
        await method(actual_target, file_path, caption=caption_html, parse_mode=PyroParseMode.HTML, **pyro_kwargs)
        return {"success": True, "target": str(actual_target), "type": media_type, "via": "user_fallback"}


async def _dispatch_media_group(
    items: list[dict[str, Any]],
    target_chat_id: int | str,
    *,
    sender_user_id: int,
    pyro_user_app: Any,
    aiogram_bot: Any,
) -> dict[str, Any]:
    """发送媒体组/相册"""
    from pyrogram.types import InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo
    from aiogram.types import InputMediaAudio as AioAudio
    from aiogram.types import InputMediaDocument as AioDoc
    from aiogram.types import InputMediaPhoto as AioPhoto
    from aiogram.types import InputMediaVideo as AioVid
    from aiogram.types import FSInputFile

    actual_target = _resolve_target(target_chat_id, sender_user_id)
    is_to_user = (actual_target == sender_user_id)
    is_saved = (actual_target == "saved")
    any_large = any(item.get("size", 0) > 50 * 1024 * 1024 for item in items)

    # 优先使用 Bot API 发送（只要没有超过 50MB 且 aiogram_bot 可用，并且目标不是收藏夹）
    if aiogram_bot and not is_saved and not any_large:
        aio_cls_map = {
            "photo": AioPhoto,
            "video": AioVid,
            "audio": AioAudio,
            "document": AioDoc,
        }
        aio_media = []
        for item in items:
            media_cls = aio_cls_map.get(item["type"], AioDoc)
            m_kwargs = {
                "media": FSInputFile(item["path"]),
                "caption": item.get("caption", ""),
                "parse_mode": "HTML",
            }
            item_meta = item.get("metadata") or {}
            thumb_path = item.get("thumb_path")
            if item["type"] == "video":
                for k in ("duration", "width", "height", "supports_streaming"):
                    if k in item_meta:
                        m_kwargs[k] = item_meta[k]
                if thumb_path and os.path.exists(thumb_path):
                    m_kwargs["thumbnail"] = FSInputFile(thumb_path)
            elif item["type"] in ("audio", "voice"):
                if "duration" in item_meta:
                    m_kwargs["duration"] = item_meta["duration"]
                if thumb_path and os.path.exists(thumb_path):
                    m_kwargs["thumbnail"] = FSInputFile(thumb_path)

            aio_media.append(media_cls(**m_kwargs))

        try:
            await aiogram_bot.send_media_group(chat_id=actual_target, media=aio_media)
            return {"success": True, "target": str(actual_target), "count": len(items), "via": "bot"}
        except Exception as exc:
            logger.warning(f"Bot API 发送媒体组失败: {exc}")
            if is_to_user:
                raise RuntimeError(f"无法通过 Bot 发送给用户: {exc}")
            logger.info("尝试转用辅助账号发送...")

    # 目标为收藏夹、或有超大文件、或 Bot 失败时，走辅助账号
    dest = "me" if is_saved else actual_target
    pyro_media = []
    cls_map = {
        "photo": InputMediaPhoto,
        "video": InputMediaVideo,
        "audio": InputMediaAudio,
        "document": InputMediaDocument,
    }
    for item in items:
        media_cls = cls_map.get(item["type"], InputMediaDocument)
        p_kwargs = {
            "media": item["path"],
            "caption": item.get("caption", ""),
            "parse_mode": PyroParseMode.HTML,
        }
        item_meta = item.get("metadata") or {}
        thumb_path = item.get("thumb_path")
        if item["type"] == "video":
            for k in ("duration", "width", "height", "supports_streaming"):
                if k in item_meta:
                    p_kwargs[k] = item_meta[k]
            if thumb_path and os.path.exists(thumb_path):
                p_kwargs["thumb"] = thumb_path
        elif item["type"] in ("audio", "voice"):
            if "duration" in item_meta:
                p_kwargs["duration"] = item_meta["duration"]
            if thumb_path and os.path.exists(thumb_path):
                p_kwargs["thumb"] = thumb_path

        pyro_media.append(media_cls(**p_kwargs))

    await pyro_user_app.send_media_group(dest, pyro_media)
    return {"success": True, "target": str(actual_target), "count": len(items), "via": "user"}


def _resolve_target(target: str | int, sender_user_id: int) -> int | str:
    """解析目标参数为最终发送目标 peer"""
    t_str = str(target).strip()
    if t_str.lower() == "me":
        return sender_user_id
    if t_str.lower() == "saved":
        return "saved"
    try:
        val = int(t_str)
        # 如果用户输入的是普通频道纯数字ID (如 2264185942，长度 >= 9 且 > 0)，自动补齐 -100 前缀
        if val > 0 and len(str(val)) >= 9:
            val = int(f"-100{val}")
        return val
    except ValueError:
        return target
