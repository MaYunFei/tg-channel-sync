from __future__ import annotations

import asyncio
import os

from aiogram.types import FSInputFile
from aiogram.types import InputMediaAudio as AioAudio
from aiogram.types import InputMediaDocument as AioDoc
from aiogram.types import InputMediaPhoto as AioPhoto
from aiogram.types import InputMediaVideo as AioVideo
from aiogram.types import ReplyParameters
from pyrogram.enums import ParseMode

import bot_engine
import database as db
from services.sync_services import (
    build_link_rewrite_context,
    create_progress_callback,
    get_quote_payload,
    log_sync_error,
    resolve_chat_id,
    resolve_reply_target,
    rewrite_message_links,
    safe_execute,
)
from .common import (
    PYRO_MEDIA_CLS,
    dynamic_send,
    get_media_reference,
    get_msg_meta,
    get_reply_source_msg_id,
    resolve_clone_upload_target,
    rewrite_media_group_captions,
)
from .hash_perturb import perturb_clone_media
from .json_sync import process_json_sync
from .state import (
    TEMP_DIR,
    clear_temp_dir_files,
    finish_sync_session,
    record_success,
    start_sync_session,
    sync_state,
    update_state_and_check_skip,
)


AIO_MEDIA_CLS = {"photo": AioPhoto, "video": AioVideo, "audio": AioAudio, "document": AioDoc}


def describe_hash_perturb_reason(reason: str) -> str:
    if reason == "disabled":
        return "未启用指纹重置"
    if reason == "unsupported_type":
        return "当前类型不支持处理"
    if reason == "tail_bytes_appended":
        return "已在文件尾部追加随机字节"
    if reason.startswith("append_error:"):
        detail = reason.split(":", 1)[1].strip()
        return f"追加尾部字节失败: {detail or '未知错误'}"
    return reason or "未知状态"


async def maybe_perturb_clone_media(file_path: str, msg_type: str, msg_id: int, enabled: bool) -> str:
    if msg_type not in {"photo", "video"}:
        return file_path

    if not enabled:
        await db.add_msg_log("HASH_PERTURB_SKIP", f"消息ID:{msg_id} | 类型:{msg_type} | {describe_hash_perturb_reason('disabled')}")
        return file_path

    result = perturb_clone_media(file_path, msg_type)
    if result.changed:
        await db.add_msg_log("HASH_PERTURB_OK", f"消息ID:{msg_id} | 类型:{msg_type} | {describe_hash_perturb_reason(result.reason)}")
    else:
        await db.add_msg_log("HASH_PERTURB_SKIP", f"消息ID:{msg_id} | 类型:{msg_type} | {describe_hash_perturb_reason(result.reason)}")
    return result.path


async def sync_single_message(mode, sender, app, bot, source_id, target_id, msg, safe_delay, force_send, hash_perturb=False):
    msg_type, _ = get_msg_meta(msg, mode)
    has_media = msg_type != "text"
    file_name = getattr(getattr(msg, msg_type, None), "file_name", "") if msg_type in ["document", "video"] else ""
    text_html = msg.text.html if msg.text else (msg.caption.html if msg.caption else "") if hasattr(msg, "text") else ""
    quote_data = get_quote_payload(msg)

    should_skip, new_html = await db.apply_message_filters(text_html, has_media, file_name or "")
    if should_skip or (not has_media and not new_html.strip()):
        return
    if await update_state_and_check_skip(source_id, target_id, msg.id, new_html[:50] or "[媒体]", force_send=force_send):
        return

    reply_to_id = await resolve_reply_target(source_id, target_id, get_reply_source_msg_id(msg, mode), mode.upper(), msg.id)
    link_context = await build_link_rewrite_context(bot_engine.aiogram_bot, source_id, target_id)
    new_html, rewrite_count = await rewrite_message_links(new_html, source_id, link_context)
    if rewrite_count:
        await db.add_msg_log(f"{mode.upper()}_LINK_REWRITE", f"原始:[{source_id}] 消息ID:{msg.id} | 命中 {rewrite_count} 个链接改写")

    try:
        if mode == "api":
            if quote_data and reply_to_id:
                if not has_media:
                    kwargs = {
                        "chat_id": target_id,
                        "text": new_html,
                        "parse_mode": ParseMode.HTML,
                        "reply_to_message_id": reply_to_id,
                        "quote_text": quote_data["text"],
                    }
                    if quote_data.get("entities"):
                        kwargs["quote_entities"] = quote_data["entities"]
                    sent_id = (await safe_execute(app.send_message(**kwargs), sync_state)).id
                else:
                    media_ref = get_media_reference(msg, msg_type)
                    if not media_ref:
                        raise ValueError(f"引用回复媒体缺少可复用 file_id: {msg.id}")
                    sent = await safe_execute(
                        dynamic_send(app, msg_type, target_id, media_ref, new_html, ParseMode.HTML, reply_to_message_id=reply_to_id, quote_data=quote_data),
                        sync_state,
                    )
                    sent_id = sent.id
                await db.add_msg_log("API_QUOTE_SEND", f"原始:[{source_id}] 消息ID:{msg.id} | 已按引用回复发送")
            elif new_html != text_html:
                if not has_media:
                    kwargs = {"chat_id": target_id, "text": new_html, "parse_mode": ParseMode.HTML}
                    if reply_to_id:
                        kwargs["reply_to_message_id"] = reply_to_id
                    sent_id = (await safe_execute(app.send_message(**kwargs), sync_state)).id
                else:
                    kwargs = {"chat_id": target_id, "from_chat_id": source_id, "message_id": msg.id, "caption": new_html}
                    if reply_to_id:
                        kwargs["reply_to_message_id"] = reply_to_id
                    sent_id = (await safe_execute(app.copy_message(**kwargs), sync_state)).id
            else:
                kwargs = {"chat_id": target_id, "from_chat_id": source_id, "message_id": msg.id}
                if reply_to_id:
                    kwargs["reply_to_message_id"] = reply_to_id
                sent_id = (await safe_execute(app.copy_message(**kwargs), sync_state)).id
        else:
            if not has_media:
                sent = await safe_execute(
                    dynamic_send(
                        bot if sender == "bot" else app,
                        "text",
                        target_id,
                        None,
                        new_html,
                        "HTML" if sender == "bot" else ParseMode.HTML,
                        reply_to_message_id=reply_to_id,
                        quote_data=quote_data if reply_to_id else None,
                    ),
                    sync_state,
                )
                sent_id = sent.message_id if sender == "bot" else sent.id
            else:
                file_path = None
                for _ in range(3):
                    if sync_state["stop_requested"]:
                        break
                    try:
                        file_path = await safe_execute(
                            app.download_media(
                                msg,
                                file_name=f"{TEMP_DIR}/",
                                progress=create_progress_callback("下载中", sync_state),
                            ),
                            sync_state,
                        )
                        if file_path:
                            break
                    except Exception as exc:
                        if "STOP_REQUESTED" in str(exc):
                            raise
                        await asyncio.sleep(2)
                if not file_path or sync_state["stop_requested"]:
                    return

                file_path = await maybe_perturb_clone_media(file_path, msg_type, msg.id, hash_perturb)
                file_size = os.path.getsize(file_path)
                upload_target = await safe_execute(resolve_clone_upload_target(sender, app, [file_size]), sync_state)
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
                        sent = await safe_execute(
                            dynamic_send(
                                client,
                                msg_type,
                                target_id,
                                media_arg,
                                new_html,
                                parse_mode,
                                reply_to_message_id=reply_to_id,
                                quote_data=quote_data if reply_to_id else None,
                            ),
                            sync_state,
                        )
                        sent_id = sent.message_id if actual_sender == "bot" else sent.id
                        if actual_sender == "bot":
                            await bot_engine.note_upload_success(client, file_size)
                        break
                    except Exception as exc:
                        if "STOP_REQUESTED" in str(exc):
                            raise
                        await asyncio.sleep(2)

                if sent_id is None and sender == "bot" and actual_sender == "bot":
                    await db.add_msg_log("BOT_FALLBACK", f"原始:[{source_id}] 消息ID:{msg.id} | {upload_target['label']} 上传失败，回退辅助账号重传")
                    for _ in range(3):
                        if sync_state["stop_requested"]:
                            break
                        try:
                            sync_state["current_text"] = "上传中... [辅助账号回退]"
                            sent = await safe_execute(
                                dynamic_send(
                                    app,
                                    msg_type,
                                    target_id,
                                    file_path,
                                    new_html,
                                    ParseMode.HTML,
                                    reply_to_message_id=reply_to_id,
                                    quote_data=quote_data if reply_to_id else None,
                                ),
                                sync_state,
                            )
                            sent_id = sent.id
                            break
                        except Exception as exc:
                            if "STOP_REQUESTED" in str(exc):
                                raise
                            await asyncio.sleep(2)

                try:
                    os.remove(file_path)
                except Exception:
                    pass
                if sent_id is None:
                    return

        await record_success(source_id, target_id, msg.id, sent_id, force_send=force_send)
        if mode == "clone" and quote_data and reply_to_id:
            await db.add_msg_log("CLONE_QUOTE_SEND", f"原始:[{source_id}] 消息ID:{msg.id} | 已按引用回复发送")
        await db.add_msg_log(f"{mode.upper()}_SEND", f"原始:[{source_id}] 消息ID:{msg.id} | 目标:[{target_id}] 新ID:{sent_id} | 同步成功")
    except Exception as exc:
        if sync_state["stop_requested"]:
            return
        await log_sync_error(f"单条同步异常 ID {msg.id}", exc)

    await asyncio.sleep(safe_delay)


async def sync_media_group(mode, sender, app, bot, source_id, target_id, group, safe_delay, force_send, hash_perturb=False):
    if await update_state_and_check_skip(source_id, target_id, group[0].id, "[媒体组]", force_send=force_send):
        return

    reply_to_id = await resolve_reply_target(source_id, target_id, get_reply_source_msg_id(group[0], mode), mode.upper(), group[0].id)
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
                    copied_msgs = await safe_execute(app.send_media_group(**kwargs), sync_state)
                else:
                    kwargs = {"chat_id": target_id, "from_chat_id": source_id, "message_id": group[0].id}
                    if reply_to_id:
                        kwargs["reply_to_message_id"] = reply_to_id
                    if quote_data and reply_to_id:
                        kwargs["quote_text"] = quote_data["text"]
                        if quote_data.get("entities"):
                            kwargs["quote_entities"] = quote_data["entities"]
                    copied_msgs = await safe_execute(app.copy_media_group(**kwargs), sync_state)
                for orig_m, new_m in zip(group, copied_msgs):
                    await record_success(source_id, target_id, orig_m.id, new_m.id, force_send=force_send)
                if captions_changed:
                    await db.add_msg_log("API_GROUP_CAPTION_REWRITE", f"原始:[{source_id}] 组首ID:{group[0].id} | 命中 {caption_rewrite_count} 个 caption 链接改写")
                if quote_data and reply_to_id:
                    await db.add_msg_log("API_QUOTE_GROUP_SEND", f"原始:[{source_id}] 组首ID:{group[0].id} | 已按引用回复发送媒体组")
                break
            except TypeError as exc:
                if "topics" in str(exc):
                    for item in group:
                        await record_success(source_id, target_id, item.id, 0, force_send=force_send)
                    break
            except Exception as exc:
                if "STOP_REQUESTED" in str(exc):
                    raise
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
                        return await safe_execute(
                            app.download_media(
                                m_item,
                                file_name=f"{TEMP_DIR}/",
                                progress=create_progress_callback(f"并发下载 [{idx}]", sync_state),
                            ),
                            sync_state,
                        )

                results = await asyncio.gather(
                    *[dl_album_item(item, index + 1) for index, item in enumerate(group)],
                    return_exceptions=True,
                )
                if any(isinstance(result, Exception) for result in results):
                    if any("STOP_REQUESTED" in str(result) for result in results):
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

        if hash_perturb:
            perturbed_files = []
            for item, path in downloaded_files:
                item_type, _ = get_msg_meta(item, mode)
                new_path = await maybe_perturb_clone_media(path, item_type, item.id, True)
                perturbed_files.append((item, new_path))
            downloaded_files = perturbed_files
        else:
            for item, _ in downloaded_files:
                item_type, _ = get_msg_meta(item, mode)
                if item_type in {"photo", "video"}:
                    await db.add_msg_log("HASH_PERTURB_SKIP", f"消息ID:{item.id} | 类型:{item_type} | {describe_hash_perturb_reason('disabled')}")

        file_sizes = [os.path.getsize(path) for _, path in downloaded_files]
        upload_target = await safe_execute(resolve_clone_upload_target(sender, app, file_sizes), sync_state)
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
                sent_msgs = await safe_execute(client.send_media_group(**send_kwargs), sync_state)
                for orig_m, new_m in zip(group, sent_msgs):
                    await record_success(source_id, target_id, orig_m.id, new_m.message_id if actual_sender == "bot" else new_m.id, force_send=force_send)
                if actual_sender == "bot":
                    await bot_engine.note_upload_success(client, sum(file_sizes))
                sent_group_success = True
                if captions_changed:
                    await db.add_msg_log("CLONE_GROUP_CAPTION_REWRITE", f"原始:[{source_id}] 组首ID:{group[0].id} | 命中 {caption_rewrite_count} 个 caption 链接改写")
                if quote_data and reply_to_id:
                    await db.add_msg_log("CLONE_QUOTE_GROUP_SEND", f"原始:[{source_id}] 组首ID:{group[0].id} | 已按引用回复发送媒体组")
                break
            except Exception as exc:
                if "STOP_REQUESTED" in str(exc):
                    raise
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
            sent_msgs = await safe_execute(app.send_media_group(**send_kwargs), sync_state)
            for orig_m, new_m in zip(group, sent_msgs):
                await record_success(source_id, target_id, orig_m.id, new_m.id, force_send=force_send)

        for _, path in downloaded_files:
            try:
                os.remove(path)
            except Exception:
                pass

    await asyncio.sleep(safe_delay)


def group_messages(messages):
    grouped_msgs = []
    current_group = []
    for msg in messages:
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
    return grouped_msgs


async def process_master_sync(
    mode: str,
    sender: str,
    source_id_raw: str,
    target_id_raw: str,
    delay: float,
    start_id: int,
    end_id: int,
    json_path: str,
    force_send: bool = False,
    json_source_username: str = "",
    hash_perturb: bool = False,
):
    safe_delay = max(0.5, float(delay))
    if mode == "api":
        sender = "user"
    elif mode == "json":
        sender = "bot"

    start_sync_session(
        mode,
        source_id_raw,
        target_id_raw,
        safe_delay,
        start_id,
        end_id,
        json_path,
        force_send,
        json_source_username,
        hash_perturb,
    )
    settings = await db.get_all_settings()

    try:
        source_id = 0 if mode == "json" else await resolve_chat_id(bot_engine.aiogram_bot, source_id_raw)
        target_id = await resolve_chat_id(bot_engine.aiogram_bot, target_id_raw)
    except Exception as exc:
        await log_sync_error("任务中止，频道有误", exc)
        sync_state["is_syncing"] = False
        return

    if mode == "clone":
        await clear_temp_dir_files()
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

                for group in group_messages(filtered_msgs):
                    if sync_state["stop_requested"]:
                        break
                    if len(group) == 1:
                        await sync_single_message(mode, sender, app, bot, source_id, target_id, group[0], safe_delay, force_send, hash_perturb=hash_perturb)
                    else:
                        await sync_media_group(mode, sender, app, bot, source_id, target_id, group, safe_delay, force_send, hash_perturb=hash_perturb)
        else:
            await process_json_sync(target_id_raw, json_path, safe_delay, force_send, json_source_username=json_source_username)

    except asyncio.CancelledError:
        pass
    except Exception as exc:
        await log_sync_error("同步中断", exc)
    finally:
        finish_sync_session()
