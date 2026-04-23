import asyncio
import json
import shutil
import signal
import sys
from contextlib import asynccontextmanager

import uvicorn
from fastapi import BackgroundTasks, FastAPI, Form, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import bot_engine
import database as db
from app_config import get_config, get_setup_status, save_config
from app_paths import ensure_runtime_dirs, static_dir, temp_dir
from server_runtime import launch_browser_when_ready, reuse_existing_instance_or_exit, should_auto_open_browser
from services.sync_services import normalize_channel_username, resolve_chat_id
from services.version_service import GITHUB_REPO, get_local_version, get_remote_version_info, is_version_at_least
from sync_worker import process_master_sync, sync_state

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

STATUS_NOT_CONFIGURED = "未配置"
STATUS_INITIALIZING = "初始化中"
STATUS_CONNECTED = "已连接"
STATUS_LOGGED_IN = "已登录"
STATUS_LOGIN_REQUIRED = "需要登录"
STATUS_TIMEOUT = "连接超时"
STATUS_START_FAILED = "启动失败"

app_info_cache = {
    "bot": {"name": "", "username": "", "status": STATUS_NOT_CONFIGURED},
    "user": {"name": "", "status": STATUS_NOT_CONFIGURED},
}
polling_task = None
TEMP_DIR = str(temp_dir())
_cleanup_done = False
SHUTDOWN_EVENT = asyncio.Event()
SERVER = None
RESTART_REQUESTED = False
STOP_REQUESTED = False


def refresh_app_info(bot_info=None, user_info=None):
    if bot_info:
        app_info_cache["bot"].update(bot_info)
    if user_info:
        app_info_cache["user"].update(user_info)


async def _force_cleanup():
    global polling_task
    SHUTDOWN_EVENT.set()
    sync_state["stop_requested"] = True

    if polling_task:
        try:
            if not polling_task.done():
                try:
                    await bot_engine.dp.stop_polling()
                except RuntimeError:
                    pass
            await asyncio.wait_for(polling_task, timeout=5)
        except asyncio.TimeoutError:
            polling_task.cancel()
            try:
                await polling_task
            except Exception:
                pass
        except Exception:
            pass
        polling_task = None

    await bot_engine.close_user_client()
    await bot_engine.close_bot_client()
    await db.close_db()


def _request_server_exit():
    global SERVER
    SHUTDOWN_EVENT.set()
    if SERVER is not None:
        if SERVER.should_exit:
            SERVER.force_exit = True
        else:
            SERVER.should_exit = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    global polling_task, _cleanup_done

    def _sigint_handler(signum, frame):
        print("\n[INFO] 收到关闭信号，正在退出...")
        _request_server_exit()

    signal.signal(signal.SIGINT, _sigint_handler)

    ensure_runtime_dirs()
    await db.init_db()
    if temp_dir().exists():
        shutil.rmtree(temp_dir(), ignore_errors=True)
    temp_dir().mkdir(parents=True, exist_ok=True)

    config = get_config()
    telegram = config["telegram"]

    refresh_app_info(
        {
            "name": "",
            "username": "",
            "status": STATUS_NOT_CONFIGURED if not telegram.get("bot_token") else STATUS_INITIALIZING,
        },
        {
            "name": "",
            "status": STATUS_NOT_CONFIGURED
            if not (telegram.get("api_id") and telegram.get("api_hash"))
            else STATUS_INITIALIZING,
        },
    )

    try:
        bot = bot_engine.init_bot_client()
        if bot:
            me = await bot.get_me()
            refresh_app_info({"name": me.first_name, "username": me.username, "status": STATUS_CONNECTED})
            polling_task = asyncio.create_task(
                bot_engine.dp.start_polling(
                    bot,
                    handle_signals=False,
                    close_bot_session=False,
                )
            )
    except Exception as exc:
        refresh_app_info({"status": STATUS_START_FAILED})
        await db.add_sys_log("ERROR", f"Bot 启动失败: {exc}")

    if bot_engine.has_user_api_credentials():
        try:
            user_me = await asyncio.wait_for(bot_engine.start_user_client_if_authorized(), timeout=30)
            if user_me:
                refresh_app_info(user_info={"name": user_me.first_name, "status": STATUS_LOGGED_IN})
            else:
                refresh_app_info(user_info={"name": "", "status": STATUS_LOGIN_REQUIRED})
        except asyncio.TimeoutError:
            refresh_app_info(user_info={"status": STATUS_TIMEOUT})
            await db.add_sys_log("WARNING", "辅助账号连接超时，API 模式暂不可用")
        except Exception as exc:
            refresh_app_info(user_info={"status": STATUS_START_FAILED})
            await db.add_sys_log("WARNING", f"辅助账号启动失败: {exc}")

    yield

    print("\n[INFO] 收到关闭信号，正在安全释放系统资源...")
    SHUTDOWN_EVENT.set()
    try:
        if not _cleanup_done:
            _cleanup_done = True
            await _force_cleanup()
    except (asyncio.CancelledError, RuntimeError):
        pass


app = FastAPI(title="TG Channel Sync", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(static_dir())), name="static")


@app.get("/")
async def serve_index():
    return FileResponse(str(static_dir() / "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/setup/status")
async def get_setup_state():
    return get_setup_status(get_config())


@app.get("/api/config")
async def get_runtime_config():
    return get_config()


@app.post("/api/config")
async def update_runtime_config(request: Request):
    payload = await request.json()
    config = save_config(payload)
    return {"status": "success", "message": "配置已保存", "config": config}


@app.get("/api/app_info")
async def get_app_info():
    return app_info_cache


@app.get("/api/version")
async def get_version_info():
    current_version = get_local_version()
    try:
        remote = await asyncio.to_thread(get_remote_version_info)
        latest_version = remote.get("latest_version", "")
        return {
            "status": "success",
            "repo": GITHUB_REPO,
            "current_version": current_version,
            "latest_version": latest_version,
            "source": remote.get("source", ""),
            "url": remote.get("url", f"https://github.com/{GITHUB_REPO}"),
            "up_to_date": is_version_at_least(current_version, latest_version),
        }
    except Exception as exc:
        return {
            "status": "error",
            "repo": GITHUB_REPO,
            "current_version": current_version,
            "latest_version": "",
            "source": "",
            "url": f"https://github.com/{GITHUB_REPO}",
            "up_to_date": False,
            "message": str(exc),
        }


@app.get("/api/user_auth/status")
async def get_user_auth_status():
    return bot_engine.get_user_auth_status()


@app.post("/api/user_auth/send_code")
async def send_user_auth_code(request: Request):
    try:
        payload = await request.json()
        result = await bot_engine.begin_user_auth(payload.get("phone_number", ""))
        if result["status"] == "authorized":
            user = result["user"]
            refresh_app_info(user_info={"name": user["name"], "status": STATUS_LOGGED_IN})
        else:
            refresh_app_info(user_info={"name": "", "status": "等待验证码"})
        return result
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.post("/api/user_auth/sign_in")
async def sign_in_user_auth(request: Request):
    try:
        payload = await request.json()
        result = await bot_engine.complete_user_auth(payload.get("phone_code", ""))
        if result["status"] == "authorized":
            user = result["user"]
            refresh_app_info(user_info={"name": user["name"], "status": STATUS_LOGGED_IN})
        elif result["status"] == "password_required":
            refresh_app_info(user_info={"name": "", "status": "等待两步验证"})
        return result
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.post("/api/user_auth/check_password")
async def check_user_auth_password(request: Request):
    try:
        payload = await request.json()
        result = await bot_engine.complete_user_password(payload.get("password", ""))
        user = result["user"]
        refresh_app_info(user_info={"name": user["name"], "status": STATUS_LOGGED_IN})
        return result
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.post("/api/user_auth/cancel")
async def cancel_user_auth():
    try:
        result = await bot_engine.cancel_user_auth()
        refresh_app_info(
            user_info={
                "name": "",
                "status": STATUS_LOGIN_REQUIRED if bot_engine.has_user_api_credentials() else STATUS_NOT_CONFIGURED,
            }
        )
        return result
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.post("/api/user_auth/switch_account")
async def switch_user_account():
    try:
        result = await bot_engine.switch_user_account()
        refresh_app_info(
            user_info={
                "name": "",
                "status": STATUS_LOGIN_REQUIRED if bot_engine.has_user_api_credentials() else STATUS_NOT_CONFIGURED,
            }
        )
        return result
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.get("/api/stream")
async def sse_stream(request: Request):
    async def event_generator():
        last_sys_id, last_msg_id = 0, 0
        sys_logs = await db.get_sys_logs_after(0)
        msg_logs = await db.get_msg_logs_after(0)
        if sys_logs:
            last_sys_id = sys_logs[0][0]
        if msg_logs:
            last_msg_id = msg_logs[0][0]

        try:
            while not SHUTDOWN_EVENT.is_set():
                if await request.is_disconnected():
                    break
                payload = {"status": sync_state, "app_info": app_info_cache}
                new_sys = await db.get_sys_logs_after(last_sys_id)
                if new_sys:
                    last_sys_id = new_sys[0][0]
                    payload["sys_logs"] = [
                        {"id": row[0], "time": row[1], "level": row[2], "msg": row[3]}
                        for row in reversed(new_sys)
                    ]
                new_msg = await db.get_msg_logs_after(last_msg_id)
                if new_msg:
                    last_msg_id = new_msg[0][0]
                    payload["msg_logs"] = [
                        {"id": row[0], "time": row[1], "action": row[2], "detail": row[3]}
                        for row in reversed(new_msg)
                    ]

                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/logs/system")
async def get_system_logs():
    rows = await db.get_recent_sys_logs()
    return [{"id": row[0], "time": row[1], "level": row[2], "msg": row[3]} for row in reversed(rows)]


@app.delete("/api/logs/system")
async def clear_system_logs():
    await db.clear_sys_logs()
    return {"status": "success", "message": "系统日志已清理"}


@app.get("/api/logs/message")
async def get_message_logs():
    rows = await db.get_recent_msg_logs()
    return [{"id": row[0], "time": row[1], "action": row[2], "detail": row[3]} for row in reversed(rows)]


@app.delete("/api/logs/message")
async def clear_message_logs():
    await db.clear_msg_logs()
    return {"status": "success", "message": "消息日志已清理"}


@app.post("/api/server/stop")
async def stop_server():
    async def shutdown():
        global _cleanup_done, STOP_REQUESTED, RESTART_REQUESTED
        if _cleanup_done:
            return
        _cleanup_done = True
        STOP_REQUESTED = True
        RESTART_REQUESTED = False
        await db.add_sys_log("WARNING", "收到关闭服务请求，正在停止服务...")
        print("[INFO] 正在关闭服务进程...")
        _request_server_exit()

    asyncio.create_task(shutdown())
    return {"status": "success", "message": "服务端正在关闭，请稍候关闭此页面..."}


@app.post("/api/server/restart")
async def restart_server():
    async def restart():
        global _cleanup_done, RESTART_REQUESTED, STOP_REQUESTED
        if _cleanup_done:
            return
        _cleanup_done = True
        RESTART_REQUESTED = True
        STOP_REQUESTED = False
        await db.add_sys_log("WARNING", "收到重启服务请求，正在准备重启...")
        print("[INFO] 正在当前终端原地重启服务...")
        _request_server_exit()

    asyncio.create_task(restart())
    return {"status": "success", "message": "服务端正在重载配置并重启..."}


@app.get("/api/mappings")
async def get_mappings():
    mappings = [
        {
            "source_id": row[0],
            "target_id": row[1],
            "realtime_sender": row[2] or "bot",
            "realtime_fallback_to_user": bool(row[3]),
            "realtime_hash_perturb": bool(row[4]),
        }
        for row in await db.get_all_channel_mappings()
    ]
    grouped = {}
    for item in mappings:
        target_id = item["target_id"]
        grouped.setdefault(target_id, []).append(item)
    grouped_mappings = [
        {"target_id": target_id, "sources": sorted(sources, key=lambda item: item["source_id"])}
        for target_id, sources in sorted(grouped.items(), key=lambda pair: pair[0])
    ]
    return {"mappings": mappings, "grouped_mappings": grouped_mappings}


@app.post("/api/mappings")
async def add_mapping(
    source_id: str = Form(...),
    target_id: str = Form(...),
    realtime_sender: str = Form("bot"),
    realtime_fallback_to_user: str = Form("1"),
    realtime_hash_perturb: str = Form("0"),
):
    try:
        src = await resolve_chat_id(bot_engine.aiogram_bot, source_id)
        tgt = await resolve_chat_id(bot_engine.aiogram_bot, target_id)
        if src == tgt:
            return {"status": "error", "message": "源频道和目标频道不能相同"}
        if await db.has_channel_mapping(src, tgt):
            return {"status": "error", "message": "该频道映射已存在，请先删除后重新添加"}
        if await db.would_create_channel_mapping_cycle(src, tgt):
            return {"status": "error", "message": "该映射会形成循环同步，已拒绝保存"}
        await db.add_channel_mapping(
            src,
            tgt,
            realtime_sender=realtime_sender,
            realtime_fallback_to_user=realtime_fallback_to_user == "1",
            realtime_hash_perturb=realtime_hash_perturb == "1",
        )
        await db.add_sys_log("INFO", f"添加频道映射: {src} -> {tgt}")
        return {"status": "success", "message": "映射规则添加成功"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


@app.delete("/api/mappings/{source_id}")
async def delete_mapping(source_id: int, target_id: int | None = None):
    await db.delete_channel_mapping(source_id, target_id=target_id)
    return {"status": "success", "message": "规则已删除"}


@app.get("/api/filter_rules")
async def get_filter_rules():
    return [
        {
            "id": row[0],
            "rule_type": row[1],
            "pattern": row[2],
            "replacement": row[3],
            "is_case_sensitive": row[4],
        }
        for row in await db.get_all_filter_rules()
    ]


@app.post("/api/filter_rules")
async def add_filter_rule(
    rule_type: str = Form(...),
    pattern: str = Form(...),
    replacement: str = Form(""),
    is_case_sensitive: int = Form(0),
):
    await db.add_filter_rule(rule_type, pattern, replacement, is_case_sensitive)
    await db.add_sys_log("INFO", f"添加过滤规则 [{rule_type}]: {pattern}")
    return {"status": "success", "message": "过滤规则添加成功"}


@app.delete("/api/filter_rules/{rule_id}")
async def delete_filter_rule(rule_id: int):
    await db.delete_filter_rule(rule_id)
    return {"status": "success", "message": "规则已删除"}


@app.get("/api/global_settings")
async def get_global_settings():
    return await db.get_all_settings()


@app.post("/api/global_settings")
async def update_global_settings(
    sync_text: str = Form("1"),
    sync_photo: str = Form("1"),
    sync_video: str = Form("1"),
    sync_document: str = Form("1"),
    sync_sticker: str = Form("1"),
    sync_gif: str = Form("1"),
    sync_audio: str = Form("1"),
    sync_voice: str = Form("1"),
):
    await db.update_settings(
        {
            "sync_text": sync_text,
            "sync_photo": sync_photo,
            "sync_video": sync_video,
            "sync_document": sync_document,
            "sync_sticker": sync_sticker,
            "sync_gif": sync_gif,
            "sync_audio": sync_audio,
            "sync_voice": sync_voice,
        }
    )
    await db.add_sys_log("INFO", "全局消息过滤配置已保存")
    return {"status": "success", "message": "全局消息过滤配置已保存"}


@app.post("/api/stop_sync")
async def stop_sync():
    if sync_state["is_syncing"]:
        sync_state["stop_requested"] = True
        return {"status": "success", "message": "已下发中断指令"}
    return {"status": "error", "message": "当前没有运行中的任务"}


@app.post("/api/start_sync")
async def start_sync(
    background_tasks: BackgroundTasks,
    mode: str = Form(...),
    sender: str = Form("bot"),
    source_id: str = Form(""),
    target_id: str = Form(""),
    delay: float = Form(5),
    start_id: int = Form(0),
    end_id: int = Form(0),
    json_path: str = Form(""),
    json_source_username: str = Form(""),
    json_media_group_window_seconds: int = Form(3),
    force_send: str = Form("0"),
    hash_perturb: str = Form("0"),
    clone_fallback_to_user: str = Form("1"),
):
    if sync_state["is_syncing"]:
        return {"status": "error", "message": "任务正在运行中"}
    if bot_engine.aiogram_bot is None:
        return {"status": "error", "message": "请先在设置中配置并重启 BOT"}
    if mode in ["api", "clone"] and not bot_engine.pyro_user_app:
        return {"status": "error", "message": "请先完成辅助账号登录"}
    if mode == "json" and sender == "user" and not bot_engine.pyro_user_app:
        return {"status": "error", "message": "JSON 导入使用辅助账号发送前，请先完成辅助账号登录"}

    json_media_group_window_seconds = getattr(
        json_media_group_window_seconds,
        "default",
        json_media_group_window_seconds,
    )

    background_tasks.add_task(
        process_master_sync,
        mode,
        sender,
        source_id,
        target_id,
        delay,
        start_id,
        end_id,
        json_path,
        force_send == "1",
        json_source_username,
        max(1, int(json_media_group_window_seconds or 3)),
        hash_perturb == "1",
        clone_fallback_to_user == "1",
    )

    if mode == "json":
        normalized_source_username = normalize_channel_username(json_source_username)
        await db.add_sys_log(
            "INFO",
            f"启动 JSON 任务 -> {target_id} | 发送身份:{'辅助账号' if sender == 'user' else '机器人'}"
            + (f" | 源频道用户名:@{normalized_source_username}" if normalized_source_username else ""),
        )
    else:
        await db.add_sys_log("INFO", f"启动 {mode.upper()} 任务: {source_id} -> {target_id}")
    return {"status": "success", "message": f"启动 {mode.upper()} 任务成功"}


def run_server():
    global SERVER, _cleanup_done
    SHUTDOWN_EVENT.clear()
    _cleanup_done = False
    config = get_config()
    server_cfg = config["server"]
    host = str(server_cfg.get("host", "127.0.0.1") or "127.0.0.1")
    port = int(server_cfg.get("port", 8011))
    if should_auto_open_browser(server_cfg):
        launch_browser_when_ready(host, port, SHUTDOWN_EVENT)
    uvicorn_config = uvicorn.Config(
        app,
        host=host,
        port=port,
        timeout_keep_alive=0,
    )
    SERVER = uvicorn.Server(uvicorn_config)
    SERVER.run()
    SERVER = None

if __name__ == "__main__":
    startup_config = get_config()
    startup_server_cfg = startup_config["server"]
    startup_host = str(startup_server_cfg.get("host", "127.0.0.1") or "127.0.0.1")
    startup_port = int(startup_server_cfg.get("port", 8011))
    startup_auto_open_browser = should_auto_open_browser(startup_server_cfg)
    if not reuse_existing_instance_or_exit(startup_host, startup_port, startup_auto_open_browser):
        raise SystemExit(0)

    while True:
        run_server()
        if RESTART_REQUESTED:
            RESTART_REQUESTED = False
            print("[INFO] 服务已停止，正在当前终端重新启动...")
            continue
        break
