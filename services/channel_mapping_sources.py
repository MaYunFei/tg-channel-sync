from __future__ import annotations

from services.sync_services import normalize_channel_username, resolve_chat_id


async def bot_can_receive_channel_posts(bot, source_id: int) -> bool:
    if bot is None:
        return False
    try:
        me = await bot.get_me()
        member = await bot.get_chat_member(source_id, me.id)
        status = str(getattr(member, "status", "") or "").lower()
        return "administrator" in status or "creator" in status
    except Exception:
        return False


async def resolve_public_user_source(loaded_bot_engine, source_ref: str) -> tuple[int, str]:
    clean_ref = str(source_ref or "").strip()
    username = normalize_channel_username(clean_ref)
    if clean_ref.lstrip("-").isdigit():
        peer = int(clean_ref)
    elif username:
        peer = username
    else:
        raise ValueError("公开频道免加入读取需要填写频道 ID、@username 或 https://t.me/username")

    user_app = getattr(loaded_bot_engine, "pyro_user_app", None)
    if not getattr(user_app, "is_initialized", False):
        raise ValueError("Bot 无法监听该源频道；若要免加入读取公开频道，请先完成辅助账号登录")
    chat = await user_app.get_chat(peer)
    chat_id = int(getattr(chat, "id", 0) or 0)
    if not chat_id:
        raise ValueError(f"辅助账号无法解析公开频道 {clean_ref}")
    resolved_ref = str(getattr(chat, "username", "") or clean_ref).lstrip("@")
    return chat_id, resolved_ref


async def resolve_mapping_source(
    loaded_bot_engine,
    source_ref: str,
    *,
    allow_public_user_fallback: bool = False,
) -> tuple[int, str, str]:
    source_username = normalize_channel_username(source_ref)
    is_numeric_source = str(source_ref or "").strip().lstrip("-").isdigit()
    if not allow_public_user_fallback:
        return await resolve_chat_id(loaded_bot_engine.aiogram_bot, source_ref), "bot", ""

    try:
        source_id = await resolve_chat_id(loaded_bot_engine.aiogram_bot, source_ref)
    except Exception:
        if not (source_username or is_numeric_source):
            raise
        public_source_id, public_source_ref = await resolve_public_user_source(loaded_bot_engine, source_ref)
        return public_source_id, "public_user", public_source_ref

    if (source_username or is_numeric_source) and not await bot_can_receive_channel_posts(
        loaded_bot_engine.aiogram_bot,
        source_id,
    ):
        public_source_id, public_source_ref = await resolve_public_user_source(loaded_bot_engine, source_ref)
        return public_source_id, "public_user", public_source_ref

    return source_id, "bot", ""
