import asyncio
import json
import os
import shutil
import sys
import signal
import subprocess
import uvicorn
from contextlib import asynccontextmanager

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI, BackgroundTasks, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from dotenv import load_dotenv

import database as db
import bot_engine
import sync_engine
from sync_engine import sync_state, process_master_sync

load_dotenv()
PORT = int(os.getenv("PORT", 8011))

app_info_cache = {"bot": {"name": "", "username": ""}, "user": {"name": "", "status": "未配置"}}
polling_task, TEMP_DIR, _cleanup_done = None, "temp", False
SHUTDOWN_EVENT = asyncio.Event()
_sse_disconnect_callbacks = {}

async def _force_cleanup():
    SHUTDOWN_EVENT.set()
    sync_state["stop_requested"] = True
    if polling_task:
        polling_task.cancel()
        try: await polling_task
        except Exception: pass
    if bot_engine.pyro_user_app and bot_engine.pyro_user_app.is_initialized:
        try: await bot_engine.pyro_user_app.stop()
        except Exception: pass
    try: await bot_engine.aiogram_bot.session.close()
    except Exception: pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    global polling_task
    def _sigint_handler(signum, frame):
        print("\n[INFO] 收到关机信号，正在退出...")
        os._exit(0)
    signal.signal(signal.SIGINT, _sigint_handler)
    await db.init_db()
    if os.path.exists(TEMP_DIR): shutil.rmtree(TEMP_DIR, ignore_errors=True)
    os.makedirs(TEMP_DIR, exist_ok=True)
    try:
        me = await bot_engine.aiogram_bot.get_me()
        app_info_cache["bot"] = {"name": me.first_name, "username": me.username}
        polling_task = asyncio.create_task(bot_engine.dp.start_polling(bot_engine.aiogram_bot, request_timeout=60))
    except Exception as e: await db.add_log("ERROR", f"Bot启动失败: {e}")

    bot_engine.init_user_client()
    if bot_engine.pyro_user_app:
        try:
            await asyncio.wait_for(bot_engine.pyro_user_app.start(), timeout=30)
            user_me = await bot_engine.pyro_user_app.get_me()
            app_info_cache["user"] = {"name": user_me.first_name, "status": "已登录"}
        except asyncio.TimeoutError:
            await db.add_log("WARNING", "辅助账号连接超时，API模式暂不可用")
        except Exception as e:
            await db.add_log("WARNING", f"辅助账号启动失败: {e}")

    yield

    print("\n[INFO] 收到关机信号，正在安全释放系统资源...")
    SHUTDOWN_EVENT.set()
    try:
        if not _cleanup_done:
            _cleanup_done = True
            await _force_cleanup()
    except (asyncio.CancelledError, RuntimeError):
        pass
    os._exit(0)

app = FastAPI(title="杏铃同步台", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def serve_index(): return FileResponse("static/index.html")

@app.get("/api/app_info")
async def get_app_info(): return app_info_cache

@app.get("/api/stream")
async def sse_stream(request: Request):
    async def event_generator():
        last_sys_id, last_msg_id = 0, 0
        sys_logs = await db.get_sys_logs_after(0)
        msg_logs = await db.get_msg_logs_after(0)
        if sys_logs: last_sys_id = sys_logs[0][0]
        if msg_logs: last_msg_id = msg_logs[0][0]

        try:
            while not SHUTDOWN_EVENT.is_set():
                if await request.is_disconnected(): break
                payload = {"status": sync_state}
                new_sys = await db.get_sys_logs_after(last_sys_id)
                if new_sys:
                    last_sys_id = new_sys[0][0]
                    payload["sys_logs"] = [{"id": r[0], "time": r[1], "level": r[2], "msg": r[3]} for r in reversed(new_sys)]
                new_msg = await db.get_msg_logs_after(last_msg_id)
                if new_msg:
                    last_msg_id = new_msg[0][0]
                    payload["msg_logs"] = [{"id": r[0], "time": r[1], "action": r[2], "detail": r[3]} for r in reversed(new_msg)]

                yield f"data: {json.dumps(payload)}\n\n"
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            if await request.is_disconnected():
                pass
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.post("/api/server/stop")
async def stop_server():
    async def shutdown():
        global _cleanup_done
        if _cleanup_done:
            return
        _cleanup_done = True
        await _force_cleanup()
        print("[INFO] 正在强制物理断电释放端口...")
        os._exit(0)
    asyncio.create_task(shutdown())
    return {"status": "success", "message": "服务端正在关闭，请稍候关闭此页面..."}

@app.post("/api/server/restart")
async def restart_server():
    async def restart():
        global _cleanup_done
        if _cleanup_done:
            return
        _cleanup_done = True
        await _force_cleanup()
        print("[INFO] 正在移交端口并准备重启...")
        if sys.platform == "win32":
            cmd = f'ping 127.0.0.1 -n 3 > nul && "{sys.executable}" ' + ' '.join(f'"{arg}"' for arg in sys.argv)
            subprocess.Popen(cmd, shell=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            cmd = f'sleep 2 && "{sys.executable}" ' + ' '.join(f'"{arg}"' for arg in sys.argv)
            subprocess.Popen(cmd, shell=True, preexec_fn=os.setsid)
        os._exit(0)
    asyncio.create_task(restart())
    return {"status": "success", "message": "服务端正在重载配置并重启..."}

async def resolve_chat_id(chat_ref: str) -> int:
    chat_ref = str(chat_ref).strip()
    if chat_ref.lstrip('-').isdigit(): return int(chat_ref)
    if "t.me/" in chat_ref: chat_ref = "@" + chat_ref.split("/")[-1].split("?")[0]
    if not chat_ref.startswith("@"): chat_ref = "@" + chat_ref
    try: return (await bot_engine.aiogram_bot.get_chat(chat_ref)).id
    except Exception as e: raise ValueError(f"无法解析频道 {chat_ref}")

@app.get("/api/mappings")
async def get_mappings(): return [{"source_id": m[0], "target_id": m[1]} for m in await db.get_all_channel_mappings()]

@app.post("/api/mappings")
async def add_mapping(source_id: str = Form(...), target_id: str = Form(...)):
    try:
        src = await resolve_chat_id(source_id)
        tgt = await resolve_chat_id(target_id)
        await db.add_channel_mapping(src, tgt)
        await db.add_sys_log("INFO", f"添加频道映射: {src} → {tgt}")
        return {"status": "success", "message": "映射规则添加成功"}
    except Exception as e: return {"status": "error", "message": str(e)}

@app.delete("/api/mappings/{source_id}")
async def delete_mapping(source_id: int):
    await db.delete_channel_mapping(source_id)
    return {"status": "success", "message": "规则已删除"}

@app.get("/api/filter_rules")
async def get_filter_rules():
    return [{"id": r[0], "rule_type": r[1], "pattern": r[2], "replacement": r[3], "is_case_sensitive": r[4]} for r in await db.get_all_filter_rules()]

@app.post("/api/filter_rules")
async def add_filter_rule(rule_type: str = Form(...), pattern: str = Form(...), replacement: str = Form(""), is_case_sensitive: int = Form(0)):
    await db.add_filter_rule(rule_type, pattern, replacement, is_case_sensitive)
    await db.add_sys_log("INFO", f"添加过滤规则 [{rule_type}]: {pattern}")
    return {"status": "success", "message": "过滤规则添加成功"}

@app.delete("/api/filter_rules/{rule_id}")
async def delete_filter_rule(rule_id: int):
    await db.delete_filter_rule(rule_id)
    return {"status": "success", "message": "规则已删除"}

@app.get("/api/global_settings")
async def get_global_settings(): return await db.get_all_settings()

@app.post("/api/global_settings")
async def update_global_settings(
    sync_text: str = Form("1"), sync_photo: str = Form("1"), sync_video: str = Form("1"),
    sync_document: str = Form("1"), sync_sticker: str = Form("1"), sync_gif: str = Form("1"),
    sync_audio: str = Form("1"), sync_voice: str = Form("1")
):
    await db.update_settings({"sync_text": sync_text, "sync_photo": sync_photo, "sync_video": sync_video, "sync_document": sync_document, "sync_sticker": sync_sticker, "sync_gif": sync_gif, "sync_audio": sync_audio, "sync_voice": sync_voice})
    await db.add_sys_log("INFO", "全局消息过滤配置已保存")
    return {"status": "success", "message": "全局消息过滤配置已保存"}

@app.post("/api/stop_sync")
async def stop_sync():
    if sync_state["is_syncing"]:
        sync_state["stop_requested"] = True
        return {"status": "success", "message": "已下发中断指令！"}
    return {"status": "error", "message": "无运行中任务"}

@app.post("/api/start_sync")
async def start_sync(
        background_tasks: BackgroundTasks, mode: str = Form(...), sender: str = Form("bot"),
        source_id: str = Form(...), target_id: str = Form(...), delay: float = Form(...),
        start_id: int = Form(0), end_id: int = Form(0), json_path: str = Form("")
):
    if sync_state["is_syncing"]: return {"status": "error", "message": "任务运行中"}
    if mode in ["api", "clone"] and not bot_engine.pyro_user_app: return {"status": "error", "message": "请配置 API 账号"}
    background_tasks.add_task(process_master_sync, mode, sender, source_id, target_id, delay, start_id, end_id, json_path)
    if mode == "json":
        await db.add_sys_log("INFO", f"启动 JSON 任务 → {target_id}")
    else:
        await db.add_sys_log("INFO", f"启动 {mode.upper()} 任务: {source_id} → {target_id}")
    return {"status": "success", "message": f"启动 {mode.upper()} 任务"}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT, timeout_keep_alive=0)
