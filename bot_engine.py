import asyncio
import logging
import time
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import Message
from pyrogram import Client, raw
from pyrogram.errors import SessionPasswordNeeded

import database as db
from app_config import get_config
from app_paths import pyrogram_user_session_base

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

aiogram_bot = None
pyro_user_app = None
dp = Dispatcher()
media_group_cache = {}
user_auth_state = {
    "client": None,
    "phone_number": "",
    "phone_code_hash": "",
    "awaiting_code": False,
    "awaiting_password": False,
    "password_hint": "",
    "send_code_available_at": 0.0,
}

MSG_TYPES = ["photo", "video", "animation", "audio", "voice", "sticker", "document"]


def _telegram_config():
    return get_config()["telegram"]


def _proxy_config():
    return get_config()["proxy"]


def build_proxy_url():
    proxy = _proxy_config()
    if not proxy.get("enabled") or not proxy.get("host"):
        return None
    if proxy.get("username") and proxy.get("password"):
        return f"socks5://{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}"
    return f"socks5://{proxy['host']}:{proxy['port']}"


def has_bot_token():
    return bool(_telegram_config().get("bot_token"))


def has_user_api_credentials():
    telegram = _telegram_config()
    return bool(telegram.get("api_id") and telegram.get("api_hash"))


def init_bot_client():
    global aiogram_bot
    telegram = _telegram_config()
    bot_token = telegram.get("bot_token", "").strip()
    if not bot_token:
        aiogram_bot = None
        return None

    session_kwargs = {"timeout": 3600, "proxy": build_proxy_url()}
    if telegram.get("bot_api_base_url"):
        session_kwargs["api"] = TelegramAPIServer.from_base(telegram["bot_api_base_url"])
    session = AiohttpSession(**session_kwargs)
    aiogram_bot = Bot(token=bot_token, session=session)
    return aiogram_bot


async def close_bot_client():
    global aiogram_bot
    if aiogram_bot is not None:
        try:
            await aiogram_bot.session.close()
        except Exception:
            pass
    aiogram_bot = None


def get_chat_name(chat):
    return f"@{chat.username}" if chat.username else (chat.title or str(chat.id))


def get_msg_type(msg: Message) -> str:
    return next((msg_type for msg_type in MSG_TYPES if getattr(msg, msg_type, None)), "text")


async def is_type_allowed(msg_type: str) -> bool:
    settings = await db.get_all_settings()
    key_map = {msg_type: f"sync_{msg_type}" for msg_type in MSG_TYPES}
    key_map["animation"] = "sync_gif"
    return settings.get(key_map.get(msg_type, "sync_text"), "1") == "1"


async def resolve_reply_for_forward(source_id: int, current_msg_id: int, reply_source_msg_id: int | None):
    if not reply_source_msg_id:
        return None
    target_reply_id = await db.get_target_msg_id(source_id, reply_source_msg_id)
    if target_reply_id:
        await db.add_msg_log("REPLY_MAP", f"source={source_id} message={current_msg_id} reply_target={target_reply_id}")
    else:
        await db.add_msg_log("REPLY_FALLBACK", f"source={source_id} message={current_msg_id} missing_reply_source={reply_source_msg_id}")
    return target_reply_id


@dp.channel_post()
async def handle_new_post(message: Message):
    if aiogram_bot is None:
        return

    source_id = message.chat.id
    target_id = await db.get_target_channel(source_id)
    if not target_id:
        return

    chat_name = get_chat_name(message.chat)
    msg_type = get_msg_type(message)

    if not await is_type_allowed(msg_type):
        await db.add_msg_log("DROP_TYPE", f"[{chat_name}] id={message.message_id} type={msg_type}")
        return

    if message.media_group_id:
        mg_id = message.media_group_id
        if mg_id not in media_group_cache:
            media_group_cache[mg_id] = [message]
            await asyncio.sleep(2)
            if mg_id in media_group_cache:
                group = sorted(media_group_cache.pop(mg_id), key=lambda item: item.message_id)
                reply_to_id = await resolve_reply_for_forward(source_id, group[0].message_id, getattr(group[0], "reply_to_message_id", None))
                for item in group:
                    text_html = item.html_text if item.text or item.caption else ""
                    file_name = item.document.file_name if item.document else (item.video.file_name if item.video else "")
                    should_skip, _ = await db.apply_message_filters(text_html, True, file_name or "")
                    if should_skip:
                        await db.add_msg_log("DROP_REGEX", f"[{chat_name}] group_ids={[m.message_id for m in group]}")
                        return

                msg_ids = [item.message_id for item in group]
                await db.add_msg_log("RECV_GROUP", f"[{chat_name}] group_ids={msg_ids}")
                try:
                    kwargs = {"chat_id": target_id, "from_chat_id": source_id, "message_ids": msg_ids}
                    if reply_to_id:
                        kwargs["reply_to_message_id"] = reply_to_id
                    copied_ids = await aiogram_bot.copy_messages(**kwargs)
                    for original, copied in zip(group, copied_ids):
                        await db.save_msg_mapping(source_id, original.message_id, copied.message_id)
                    await db.add_msg_log("SEND_GROUP", f"source={source_id} target={target_id} ids={msg_ids}")
                except Exception:
                    await db.add_msg_log("WARN", "copy_messages failed, fallback to single copy")
                    for item in group:
                        try:
                            kwargs = {"chat_id": target_id, "from_chat_id": source_id, "message_id": item.message_id}
                            if reply_to_id:
                                kwargs["reply_to_message_id"] = reply_to_id
                            copied = await aiogram_bot.copy_message(**kwargs)
                            await db.save_msg_mapping(source_id, item.message_id, copied.message_id)
                            await asyncio.sleep(1)
                        except Exception:
                            pass
        else:
            media_group_cache[mg_id].append(message)
        return
    has_media = msg_type != "text"
    file_name = getattr(getattr(message, msg_type, None), "file_name", "") if msg_type in ["document", "video"] else ""
    text_html = message.html_text if message.text or message.caption else ""

    await db.add_msg_log("RECV", f"[{chat_name}] id={message.message_id} type={msg_type}")

    should_skip, new_html = await db.apply_message_filters(text_html, has_media, file_name)
    if should_skip or (not has_media and not new_html.strip()):
        await db.add_msg_log("DROP_REGEX", f"[{chat_name}] id={message.message_id}")
        return

    reply_to_id = await resolve_reply_for_forward(source_id, message.message_id, getattr(message, "reply_to_message_id", None))

    try:
        if new_html != text_html:
            kwargs = {"chat_id": target_id, "parse_mode": "HTML"}
            if not has_media:
                kwargs["text"] = new_html
            else:
                kwargs.update({"from_chat_id": source_id, "message_id": message.message_id, "caption": new_html})
            if reply_to_id:
                kwargs["reply_to_message_id"] = reply_to_id
            copied = await (aiogram_bot.send_message(**kwargs) if not has_media else aiogram_bot.copy_message(**kwargs))
        else:
            kwargs = {"chat_id": target_id, "from_chat_id": source_id, "message_id": message.message_id}
            if reply_to_id:
                kwargs["reply_to_message_id"] = reply_to_id
            copied = await aiogram_bot.copy_message(**kwargs)

        await db.save_msg_mapping(source_id, message.message_id, copied.message_id)
        await db.add_msg_log("SEND", f"source={source_id} message={message.message_id} target={target_id} new={copied.message_id}")
    except Exception as exc:
        await db.add_msg_log("ERROR", f"send failed id={message.message_id} error={exc}")


@dp.edited_channel_post()
async def handle_edited_post(message: Message):
    if aiogram_bot is None:
        return

    source_id = message.chat.id
    msg_id = message.message_id
    target_id = await db.get_target_channel(source_id)
    target_msg_id = await db.get_target_msg_id(source_id, msg_id) if target_id else None
    if not target_msg_id:
        return

    has_media = get_msg_type(message) != "text"
    should_skip, new_html = await db.apply_message_filters(message.html_text if message.text or message.caption else "", has_media, "")
    if should_skip:
        return

    try:
        kwargs = {"chat_id": target_id, "message_id": target_msg_id, "parse_mode": "HTML"}
        if message.text:
            await aiogram_bot.edit_message_text(text=new_html, **kwargs)
        else:
            await aiogram_bot.edit_message_caption(caption=new_html, **kwargs)
        await db.add_msg_log("EDIT", f"source={msg_id} target={target_msg_id}")
    except Exception:
        pass


def init_user_client():
    global pyro_user_app
    telegram = _telegram_config()
    proxy = _proxy_config()
    if not telegram.get("api_id") or not telegram.get("api_hash"):
        pyro_user_app = None
        return None

    from pyrogram.connection.transport.tcp.tcp import TCP

    TCP.TIMEOUT = 60
    if proxy.get("enabled") and proxy.get("host"):
        async def _patched_connect(self, destination):
            from python_socks.async_.asyncio.v2 import Proxy
            if proxy.get("username") and proxy.get("password"):
                proxy_url = f"socks5://{proxy['username']}:{proxy['password']}@{proxy['host']}:{proxy['port']}"
            else:
                proxy_url = f"socks5://{proxy['host']}:{proxy['port']}"
            sock = await Proxy.from_url(proxy_url).connect(dest_host=destination[0], dest_port=destination[1])
            self.reader = sock.reader
            self.writer = sock.writer
        TCP._connect = _patched_connect

    pyro_user_app = Client(str(pyrogram_user_session_base()), api_id=telegram["api_id"], api_hash=telegram["api_hash"], ipv6=False)
    return pyro_user_app


async def _dispose_client(client):
    if not client:
        return
    try:
        if getattr(client, "is_initialized", False):
            await client.stop()
        elif getattr(client, "is_connected", False):
            await client.disconnect()
    except Exception:
        pass


def _clear_user_auth_state():
    user_auth_state.update({
        "client": None,
        "phone_number": "",
        "phone_code_hash": "",
        "awaiting_code": False,
        "awaiting_password": False,
        "password_hint": "",
        "send_code_available_at": 0.0,
    })


def get_send_code_cooldown_seconds():
    remaining = int(user_auth_state["send_code_available_at"] - time.time())
    return max(0, remaining)


async def _finalize_user_client(client):
    global pyro_user_app
    await client.invoke(raw.functions.updates.GetState())
    client.me = await client.get_me()
    await client.initialize()
    pyro_user_app = client
    _clear_user_auth_state()
    return client.me


async def start_user_client_if_authorized():
    client = init_user_client()
    if client is None:
        _clear_user_auth_state()
        return None

    authorized = await client.connect()
    if not authorized:
        await _dispose_client(client)
        if pyro_user_app is client:
            globals()["pyro_user_app"] = None
        return None

    return await _finalize_user_client(client)


def get_user_auth_status():
    if pyro_user_app and getattr(pyro_user_app, "is_initialized", False):
        me = getattr(pyro_user_app, "me", None)
        return {
            "status": "authorized",
            "awaiting_code": False,
            "awaiting_password": False,
            "phone_number": "",
            "password_hint": "",
            "send_code_cooldown": get_send_code_cooldown_seconds(),
            "user": {
                "id": getattr(me, "id", None),
                "name": getattr(me, "first_name", "") or "",
                "username": getattr(me, "username", "") or "",
            },
        }
    return {
        "status": "awaiting_password" if user_auth_state["awaiting_password"] else "awaiting_code" if user_auth_state["awaiting_code"] else "idle",
        "awaiting_code": user_auth_state["awaiting_code"],
        "awaiting_password": user_auth_state["awaiting_password"],
        "phone_number": user_auth_state["phone_number"],
        "password_hint": user_auth_state["password_hint"],
        "send_code_cooldown": get_send_code_cooldown_seconds(),
        "user": None,
    }


async def begin_user_auth(phone_number: str):
    global pyro_user_app
    phone_number = (phone_number or "").strip()
    if not phone_number:
        raise ValueError("手机号不能为空")
    if not has_user_api_credentials():
        raise ValueError("请先配置 API_ID 和 API_HASH")

    cooldown = get_send_code_cooldown_seconds()
    if cooldown > 0:
        raise ValueError(f"请等待 {cooldown} 秒后再发送验证码")

    await close_user_client()
    client = init_user_client()
    if client is None:
        raise ValueError("辅助账号客户端初始化失败")

    authorized = await client.connect()
    if authorized:
        me = await _finalize_user_client(client)
        return {"status": "authorized", "message": "辅助账号已登录", "user": {"id": me.id, "name": me.first_name or "", "username": me.username or ""}}

    sent_code = await client.send_code(phone_number)
    pyro_user_app = None
    user_auth_state.update({
        "client": client,
        "phone_number": phone_number,
        "phone_code_hash": sent_code.phone_code_hash,
        "awaiting_code": True,
        "awaiting_password": False,
        "password_hint": "",
        "send_code_available_at": time.time() + 30,
    })
    return {"status": "code_sent", "message": "验证码已发送，请在页面中继续输入验证码", "send_code_cooldown": 30}


async def complete_user_auth(phone_code: str):
    client = user_auth_state["client"]
    phone_number = user_auth_state["phone_number"]
    phone_code_hash = user_auth_state["phone_code_hash"]
    if not client or not user_auth_state["awaiting_code"] or not phone_code_hash:
        raise ValueError("当前没有待确认的登录请求")

    try:
        me = await client.sign_in(phone_number, phone_code_hash, (phone_code or "").strip())
    except SessionPasswordNeeded:
        user_auth_state["awaiting_code"] = False
        user_auth_state["awaiting_password"] = True
        user_auth_state["password_hint"] = await client.get_password_hint()
        return {"status": "password_required", "message": "该账号已开启两步验证，请输入密码", "password_hint": user_auth_state["password_hint"]}

    me = me or await _finalize_user_client(client)
    if not getattr(pyro_user_app, "is_initialized", False):
        me = await _finalize_user_client(client)
    return {"status": "authorized", "message": "辅助账号登录成功", "user": {"id": me.id, "name": me.first_name or "", "username": me.username or ""}}


async def complete_user_password(password: str):
    client = user_auth_state["client"]
    if not client or not user_auth_state["awaiting_password"]:
        raise ValueError("当前没有待输入密码的登录请求")

    me = await client.check_password(password or "")
    if not getattr(pyro_user_app, "is_initialized", False):
        me = await _finalize_user_client(client)
    return {"status": "authorized", "message": "辅助账号登录成功", "user": {"id": me.id, "name": me.first_name or "", "username": me.username or ""}}


async def cancel_user_auth():
    client = user_auth_state["client"]
    _clear_user_auth_state()
    await _dispose_client(client)
    return {"status": "cancelled", "message": "已取消辅助账号登录流程"}


async def switch_user_account():
    await close_user_client()
    session_base = pyrogram_user_session_base()
    for path in session_base.parent.glob(f"{session_base.name}*"):
        try:
            if path.is_file():
                path.unlink()
        except Exception:
            pass
    return {"status": "success", "message": "已清除当前辅助账号会话，请重新登录新账号"}


async def close_user_client():
    global pyro_user_app
    pending_client = user_auth_state["client"]
    _clear_user_auth_state()
    await _dispose_client(pyro_user_app)
    if pending_client is not pyro_user_app:
        await _dispose_client(pending_client)
    pyro_user_app = None







