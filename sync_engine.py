import asyncio
import json
import os
import time
from aiogram.types import FSInputFile
from aiogram.types import InputMediaPhoto as AioPhoto, InputMediaVideo as AioVideo, InputMediaDocument as AioDoc, InputMediaAudio as AioAudio
from pyrogram.enums import ParseMode
from pyrogram.types import InputMediaPhoto, InputMediaVideo, InputMediaAudio, InputMediaDocument
import bot_engine
import database as db

TYPE_MAP = {
    'photo': 'sync_photo', 'video': 'sync_video', 'animation': 'sync_gif',
    'audio': 'sync_audio', 'voice': 'sync_voice', 'document': 'sync_document', 'sticker': 'sync_sticker'
}
AIO_MEDIA_CLS = {'photo': AioPhoto, 'video': AioVideo, 'audio': AioAudio, 'document': AioDoc}
PYRO_MEDIA_CLS = {'photo': InputMediaPhoto, 'video': InputMediaVideo, 'audio': InputMediaAudio, 'document': InputMediaDocument}

sync_state = {
    "is_syncing": False, "mode": "", "total": 0, "current": 0, "current_text": "",
    "current_link": "", "skipped": 0, "stop_requested": False, "source_id_raw": "",
    "target_id_raw": "", "delay": 5, "start_id": "", "end_id": "", "json_path": ""
}

def get_msg_meta(msg, mode):
    if mode in ['api', 'clone']:
        for attr, key in TYPE_MAP.items():
            if getattr(msg, attr, None): return attr, key
        return 'text', 'sync_text'
    else:
        if msg.get('photo'): return 'photo', 'sync_photo'
        t = msg.get('media_type')
        json_map = {'video_file': ('video','sync_video'), 'animation': ('animation','sync_gif'), 'audio_file': ('audio','sync_audio'), 'voice_message': ('voice','sync_voice')}
        if t in json_map: return json_map[t]
        if 'file' in msg: return 'document', 'sync_document'
        return 'text', 'sync_text'

async def dynamic_send(client, msg_type, chat_id, file_ref, caption, parse_mode):
    method_name = f"send_{msg_type}"
    method = getattr(client, method_name, None) or getattr(client, 'send_document')
    kwargs = {"chat_id": chat_id, "parse_mode": parse_mode}
    if msg_type != 'text':
        kwargs["caption"] = caption
        kwargs[msg_type if hasattr(client, method_name) else 'document'] = file_ref
    else:
        kwargs["text"] = caption
    return await method(**kwargs)

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
    last_upd, current, total, spd_mb = [0], 0, 0, 0.0
    def progress(downloaded, total_bytes):
        nonlocal current, total, spd_mb
        if sync_state.get("stop_requested"): raise Exception("STOP_REQUESTED")
        now = time.time()
        if now - last_upd[0] > 0.5 or downloaded == total_bytes:
            last_upd[0] = now
            spd_mb = (downloaded / (now - last_upd[0] + 0.001)) / (1024 * 1024) if downloaded > 0 else 0
            sync_state["current_text"] = f"{action_name} {downloaded/total_bytes*100:.1f}% ({spd_mb:.1f} MB/s)" if total_bytes > 0 else action_name
    return progress

async def update_state_and_check_skip(source_id, msg_id, text):
    sync_state["current"] += 1
    sync_state["current_link"] = f"t.me/c/{str(source_id).replace('-100', '')}/{msg_id}"
    sync_state["current_text"] = text
    if await db.is_message_synced(source_id, msg_id):
        sync_state["skipped"] += 1
        return True
    return False

async def record_success(source_id, msg_id, target_msg_id):
    await db.save_msg_mapping(source_id, msg_id, target_msg_id)

async def handle_floodwait(wait_time):
    await db.add_log("ERROR", f"触发风控休眠 {wait_time} 秒...")
    await asyncio.sleep(wait_time)

async def process_master_sync(mode: str, sender: str, source_id_raw: str, target_id_raw: str, delay: float, start_id: int, end_id: int, json_path: str):
    safe_delay = max(0.5, float(delay))
    if mode == "api": sender = "user"
    elif mode == "json": sender = "bot"

    sync_state.update({"is_syncing": True, "mode": mode.upper(), "source_id_raw": source_id_raw, "target_id_raw": target_id_raw, "delay": safe_delay, "start_id": start_id, "end_id": end_id, "json_path": json_path, "current": 0, "skipped": 0, "total": 0, "stop_requested": False})
    settings = await db.get_all_settings()

    try:
        source_id = await resolve_chat_id(source_id_raw)
        target_id = await resolve_chat_id(target_id_raw)
    except Exception as e:
        await db.add_log("ERROR", f"❌ 任务中止，频道有误: {e}")
        sync_state["is_syncing"] = False
        return

    if mode == "clone":
        for f in os.listdir(TEMP_DIR):
            try: os.remove(os.path.join(TEMP_DIR, f))
            except: pass
        await db.add_log("INFO", "🧹 已清空 temp，准备下载")

    try:
        if mode in ["api", "clone"]:
            app, bot = bot_engine.pyro_user_app, bot_engine.aiogram_bot
            if not start_id: start_id = 1
            if not end_id:
                async for msg in app.get_chat_history(source_id, limit=1): end_id = msg.id
            if not end_id: end_id = 1
            sync_state["total"] = end_id - start_id + 1

            for chunk_start in range(start_id, end_id + 1, 100):
                if sync_state["stop_requested"]: break
                try: msgs = await app.get_messages(source_id, list(range(chunk_start, min(chunk_start + 99, end_id) + 1)))
                except Exception: continue

                filtered_msgs = []
                for msg in msgs:
                    if msg is None or msg.empty: continue
                    msg_type, sync_key = get_msg_meta(msg, mode)
                    if settings.get(sync_key, '1') == '0': continue
                    filtered_msgs.append(msg)

                grouped_msgs, current_group = [], []
                for msg in filtered_msgs:
                    if msg.media_group_id:
                        if not current_group or current_group[0].media_group_id == msg.media_group_id: current_group.append(msg)
                        else: grouped_msgs.append(current_group); current_group = [msg]
                    else:
                        if current_group: grouped_msgs.append(current_group); current_group = []
                        grouped_msgs.append([msg])
                if current_group: grouped_msgs.append(current_group)

                for group in grouped_msgs:
                    if sync_state["stop_requested"]: break
                    if len(group) == 1:
                        msg = group[0]
                        msg_type, _ = get_msg_meta(msg, mode)
                        has_media = msg_type != 'text'
                        file_name = getattr(getattr(msg, msg_type, None), 'file_name', "") if msg_type in ['document', 'video'] else ""
                        text_html = msg.text.html if msg.text else (msg.caption.html if msg.caption else "") if hasattr(msg, 'text') else ""

                        should_skip, new_html = await db.apply_message_filters(text_html, has_media, file_name or "")
                        if should_skip or (not has_media and not new_html.strip()): continue
                        if await update_state_and_check_skip(source_id, msg.id, new_html[:50] or "[媒体]"): continue

                        try:
                            if mode == "api":
                                if new_html != text_html:
                                    kwargs = {"chat_id": target_id, "parse_mode": ParseMode.HTML}
                                    if not has_media: kwargs["text"] = new_html
                                    else: kwargs.update({"from_chat_id": source_id, "message_id": msg.id, "caption": new_html})
                                    sent_id = (await safe_execute(app.send_message(**kwargs) if not has_media else app.copy_message(**kwargs))).id
                                else: sent_id = (await safe_execute(app.copy_message(chat_id=target_id, from_chat_id=source_id, message_id=msg.id))).id

                            elif mode == "clone":
                                if not has_media:
                                    sent = await safe_execute(dynamic_send(bot if sender=='bot' else app, 'text', target_id, None, new_html, "HTML" if sender=='bot' else ParseMode.HTML))
                                    sent_id = sent.message_id if sender=='bot' else sent.id
                                else:
                                    file_path = None
                                    for _ in range(3):
                                        if sync_state["stop_requested"]: break
                                        try:
                                            file_path = await safe_execute(app.download_media(msg, file_name=f"{TEMP_DIR}/", progress=create_progress_callback("⏬ 下载")))
                                            if file_path: break
                                        except Exception as e:
                                            if "STOP_REQUESTED" in str(e): raise e
                                            await asyncio.sleep(2)
                                    if not file_path or sync_state["stop_requested"]: continue

                                    actual_sender = 'user' if (sender == 'bot' and os.path.getsize(file_path) > 50*1024*1024) else sender
                                    client = bot if actual_sender == 'bot' else app
                                    pm = "HTML" if actual_sender == 'bot' else ParseMode.HTML

                                    for _ in range(3):
                                        if sync_state["stop_requested"]: break
                                        try:
                                            sync_state["current_text"] = "⏫ 上传中..."
                                            media_arg = FSInputFile(file_path) if actual_sender == 'bot' else file_path
                                            sent = await safe_execute(dynamic_send(client, msg_type, target_id, media_arg, new_html, pm))
                                            sent_id = sent.message_id if actual_sender == 'bot' else sent.id
                                            break
                                        except Exception as e:
                                            if "STOP_REQUESTED" in str(e): raise e
                                            await asyncio.sleep(2)
                                    try: os.remove(file_path)
                                    except: pass

                            await record_success(source_id, msg.id, sent_id)
                            await db.add_msg_log(f"{mode.upper()}_SEND", f"原始:[{source_id}] 消息ID:{msg.id} | 目标:[{target_id}] 新ID:{sent_id} | 同步成功")
                        except Exception as e:
                            if sync_state["stop_requested"]: break
                            await db.add_log("ERROR", f"❌ 单条同步抛出异常 ID {msg.id}: {e}")
                        await asyncio.sleep(safe_delay)
                    else:
                        if await update_state_and_check_skip(source_id, group[0].id, "[媒体组]"): continue
                        if mode == "api":
                            for _ in range(3):
                                if sync_state["stop_requested"]: break
                                try:
                                    copied_msgs = await safe_execute(app.copy_media_group(chat_id=target_id, from_chat_id=source_id, message_id=group[0].id))
                                    for orig_m, new_m in zip(group, copied_msgs): await record_success(source_id, orig_m.id, new_m.id)
                                    break
                                except TypeError as e:
                                    if "topics" in str(e):
                                        for m in group: await record_success(source_id, m.id, 0)
                                        break
                                except Exception as e:
                                    if "STOP_REQUESTED" in str(e): raise e

                        elif mode == "clone":
                            downloaded_files, dl_success = [], False
                            for _ in range(3):
                                if sync_state["stop_requested"]: break
                                try:
                                    sem = asyncio.Semaphore(3)
                                    async def dl_album_item(m_item, idx):
                                        async with sem: return await safe_execute(app.download_media(m_item, file_name=f"{TEMP_DIR}/", progress=create_progress_callback(f"⏬ 并发下载 [{idx}]")))
                                    results = await asyncio.gather(*[dl_album_item(m, i+1) for i, m in enumerate(group)], return_exceptions=True)
                                    if any(isinstance(r, Exception) for r in results):
                                        if any("STOP_REQUESTED" in str(r) for r in results): sync_state["stop_requested"] = True
                                        await asyncio.sleep(2)
                                        continue
                                    downloaded_files = [(m, p) for m, p in zip(group, results) if isinstance(p, str)]
                                    dl_success = True
                                    break
                                except Exception: pass

                            if not dl_success or sync_state["stop_requested"]:
                                for _, p in downloaded_files:
                                    try: os.remove(p)
                                    except: pass
                                continue

                            actual_sender = 'user' if (sender == 'bot' and any(os.path.getsize(p) > 50*1024*1024 for _, p in downloaded_files)) else sender
                            cls_map = AIO_MEDIA_CLS if actual_sender == 'bot' else PYRO_MEDIA_CLS
                            client = bot if actual_sender == 'bot' else app

                            for _ in range(3):
                                if sync_state["stop_requested"]: break
                                try:
                                    sync_state["current_text"] = "⏫ 上传相册..."
                                    media_list = []
                                    for m, p in downloaded_files:
                                        m_type, _ = get_msg_meta(m, mode)
                                        media_cls = cls_map.get(m_type, cls_map['document'])
                                        media_list.append(media_cls(media=FSInputFile(p) if actual_sender == 'bot' else p, caption=m.caption.html if m.caption else "", parse_mode="HTML" if actual_sender == 'bot' else ParseMode.HTML))
                                    sent_msgs = await safe_execute(client.send_media_group(target_id, media_list))
                                    for orig_m, new_m in zip(group, sent_msgs): await record_success(source_id, orig_m.id, new_m.message_id if actual_sender == 'bot' else new_m.id)
                                    break
                                except Exception as e:
                                    if "STOP_REQUESTED" in str(e): raise e
                                    await asyncio.sleep(2)
                            for _, p in downloaded_files:
                                try: os.remove(p)
                                except: pass
                        await asyncio.sleep(safe_delay)

        elif mode == "json":
            if not json_path or not os.path.exists(json_path):
                await db.add_log("ERROR", "JSON 文件不存在或路径无效")
                return

            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except Exception as e:
                await db.add_log("ERROR", f"JSON 解析失败: {e}")
                return

            messages = data.get('messages', [])
            json_dir = os.path.dirname(os.path.abspath(json_path))
            target_id = await resolve_chat_id(target_id_raw)
            sync_state["total"] = len(messages)

            for msg in messages:
                if sync_state["stop_requested"]: break
                if msg.get('type') != 'message': continue

                msg_id = msg.get('id', 0)
                text = msg.get('text', '')
                if isinstance(text, list):
                    text_parts = []
                    for t in text:
                        if isinstance(t, str): text_parts.append(t)
                        elif isinstance(t, dict): text_parts.append(t.get('text', ''))
                    text = ''.join(text_parts)

                if await update_state_and_check_skip(0, msg_id, text[:50] or "[媒体]"): continue

                media_path = None
                media_type = None
                for key in ['photo', 'video', 'file', 'audio', 'voice']:
                    if msg.get(key):
                        media_path = os.path.join(json_dir, msg[key])
                        media_type = key
                        break

                reply_to_id = None
                if msg.get('reply_to_message_id'):
                    reply_to_id = await db.get_target_msg_id(0, msg['reply_to_message_id'])

                try:
                    if media_path and os.path.exists(media_path):
                        sync_state["current_text"] = f"⏫ 上传: {os.path.basename(media_path)}"
                        file = FSInputFile(media_path)
                        caption = text if text else None

                        if media_type == 'photo':
                            sent = await bot_engine.aiogram_bot.send_photo(target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id)
                        elif media_type == 'video':
                            sent = await bot_engine.aiogram_bot.send_video(target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id)
                        elif media_type == 'audio':
                            sent = await bot_engine.aiogram_bot.send_audio(target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id)
                        elif media_type == 'voice':
                            sent = await bot_engine.aiogram_bot.send_voice(target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id)
                        else:
                            sent = await bot_engine.aiogram_bot.send_document(target_id, file, caption=caption, parse_mode="HTML", reply_to_message_id=reply_to_id)
                        sent_id = sent.message_id
                    elif text:
                        sent = await bot_engine.aiogram_bot.send_message(target_id, text, parse_mode="HTML", reply_to_message_id=reply_to_id)
                        sent_id = sent.message_id
                    else:
                        continue

                    await record_success(0, msg_id, sent_id)
                    await db.add_msg_log("JSON_SEND", f"消息ID:{msg_id} | 目标:[{target_id}] 新ID:{sent_id} | 上传成功")
                except Exception as e:
                    if sync_state["stop_requested"]: break
                    await db.add_log("ERROR", f"JSON 消息上传失败 ID {msg_id}: {e}")

                await asyncio.sleep(safe_delay)

    except asyncio.CancelledError: pass
    except Exception as e: await db.add_log("ERROR", f"同步中断: {e}")
    finally:
        sync_state["is_syncing"] = False
        sync_state["stop_requested"] = False

TEMP_DIR = "temp"
async def resolve_chat_id(chat_ref: str) -> int:
    if not chat_ref: raise ValueError("频道引用为空")
    try:
        if chat_ref.lstrip('-').isdigit(): return int(chat_ref)
        if chat_ref.startswith('@'): return int((await bot_engine.aiogram_bot.get_chat(chat_ref)).id)
        if 't.me/' in chat_ref:
            username = chat_ref.split('t.me/')[-1].split('/')[0].split('?')[0]
            return int((await bot_engine.aiogram_bot.get_chat(f"@{username}" if not username.startswith('@') else username)).id)
    except Exception as e: raise ValueError(f"无法解析频道 {chat_ref}: {e}")
    raise ValueError(f"无法解析频道 {chat_ref}")
