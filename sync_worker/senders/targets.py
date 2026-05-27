from __future__ import annotations

from pyrogram.enums import ParseMode


def should_fallback_to_user(sender: str, fallback_to_user: bool) -> bool:
    return sender == "bot" and bool(fallback_to_user)


async def resolve_upload_target(
    sender: str,
    user_client,
    file_sizes,
    *,
    allow_user_fallback: bool = True,
    wait_for_available_bot: bool = True,
):
    import bot_engine

    total_size = sum(file_sizes)
    if sender != "bot":
        return {"sender": "user", "client": user_client, "parse_mode": ParseMode.HTML, "label": "辅助账号", "bytes": total_size}
    if not bot_engine.should_upload_via_bot(max(file_sizes) if file_sizes else 0):
        if not allow_user_fallback:
            raise RuntimeError("当前文件超出 Bot 上传限制，请开启“Bot 发送失败时改用辅助账号继续发送”或切换为辅助账号发送")
        return {"sender": "user", "client": user_client, "parse_mode": ParseMode.HTML, "label": "辅助账号", "bytes": total_size}
    selection = await bot_engine.acquire_upload_bot(total_size, wait_if_unavailable=wait_for_available_bot)
    if selection is None:
        if allow_user_fallback:
            return {"sender": "user", "client": user_client, "parse_mode": ParseMode.HTML, "label": "辅助账号", "bytes": total_size}
        raise RuntimeError("当前没有可用 Bot 可继续上传，请等待冷却结束或开启“Bot 发送失败时改用辅助账号继续发送”")
    return {"sender": "bot", "client": selection["client"], "parse_mode": "HTML", "label": selection["label"], "bytes": total_size}
