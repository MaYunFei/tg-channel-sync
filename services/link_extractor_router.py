from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app_config import get_config
from services.link_extractor import (
    add_allowed_admin,
    extract_and_forward,
    get_default_target,
    is_user_authorized,
    parse_telegram_links,
    set_default_target,
)

logger = logging.getLogger("link_extractor_router")

link_extractor_router = Router(name="link_extractor_router")

# 用户级串行执行锁与排队计数器
_user_locks: dict[int, asyncio.Lock] = {}
_user_waiters: dict[int, int] = {}



def _get_help_text(default_target: str) -> str:
    return (
        "<b>📥 受限内容提取与转存机器人</b>\n\n"
        f"当前默认投递目标: <code>{default_target}</code>\n\n"
        "<b>常用指令：</b>\n"
        "• 直接发送消息链接：按默认目标投递\n"
        "• <code>/me &lt;链接&gt;</code>：强制发送到当前私聊\n"
        "• <code>/saved &lt;链接&gt;</code>：强制发送到辅助账号收藏夹\n"
        "• <code>/to &lt;频道ID或用户名&gt; &lt;链接&gt;</code>：发送到指定频道\n"
        "• <code>/target</code>：查看或设置默认投递目标\n"
        "• <code>/auth &lt;WebUI密码&gt;</code>：认领为机器人管理员\n"
    )


async def _build_target_keyboard() -> InlineKeyboardMarkup:
    """构建设置默认目标的内联键盘"""
    import database as db

    buttons = [
        [
            InlineKeyboardButton(text="👤 发给我 (私聊 me)", callback_data="set_tgt:me"),
            InlineKeyboardButton(text="⭐ 收藏夹 (saved)", callback_data="set_tgt:saved"),
        ]
    ]

    # 获取系统配置的映射目标频道列表
    try:
        mappings = await db.get_all_channel_mappings()
        unique_targets = {}
        for m in mappings:
            t_id = m.get("target_id")
            t_type = m.get("target_type")
            if t_id and t_type != "saved_messages":
                label = f"📢 频道 {t_id}"
                unique_targets[str(t_id)] = label

        channel_row = []
        for tid, label in list(unique_targets.items())[:6]:  # 最多放6个
            channel_row.append(InlineKeyboardButton(text=label, callback_data=f"set_tgt:{tid}"))
            if len(channel_row) == 2:
                buttons.append(channel_row)
                channel_row = []
        if channel_row:
            buttons.append(channel_row)
    except Exception as exc:
        logger.warning(f"获取频道映射失败: {exc}")

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@link_extractor_router.message(Command("start", "help"), F.chat.type == "private")
async def cmd_start_help(message: Message):
    user_id = message.from_user.id if message.from_user else 0
    import bot_engine

    pyro = getattr(bot_engine, "pyro_user_app", None)
    if not await is_user_authorized(user_id, pyro):
        await message.reply("🔒 您暂无使用此机器人的权限。如果您是管理员，请使用 <code>/auth &lt;WebUI密码&gt;</code> 进行身份认证。")
        return

    default_tgt = await get_default_target()
    await message.reply(_get_help_text(default_tgt), parse_mode="HTML")


@link_extractor_router.message(Command("auth"), F.chat.type == "private")
async def cmd_auth(message: Message):
    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.reply("用法: <code>/auth &lt;WebUI密码&gt;</code>", parse_mode="HTML")
        return

    input_pwd = parts[1].strip()
    correct_pwd = get_config().get("app", {}).get("webui_password", "").strip()

    if not correct_pwd:
        await message.reply("⚠️ 系统尚未设置 WebUI 密码，无法通过密码鉴权。请在 WebUI 中登录辅助账号或设置密码。")
        return

    if input_pwd == correct_pwd:
        user_id = message.from_user.id if message.from_user else 0
        await add_allowed_admin(user_id)
        await message.reply("✅ 认证成功！您已被添加为受限内容提取器的管理员。现在您可以向我发送受限链接了。")
    else:
        await message.reply("❌ 密码错误，认证失败。")


@link_extractor_router.message(Command("target", "set_target"), F.chat.type == "private")
async def cmd_target(message: Message):
    user_id = message.from_user.id if message.from_user else 0
    import bot_engine

    pyro = getattr(bot_engine, "pyro_user_app", None)
    if not await is_user_authorized(user_id, pyro):
        await message.reply("🔒 无权限访问。")
        return

    parts = (message.text or "").strip().split(maxsplit=1)
    if len(parts) >= 2:
        # 用户直接输入参数修改
        new_tgt = parts[1].strip()
        await set_default_target(new_tgt)
        await message.reply(f"✅ 默认投递目标已更新为: <code>{new_tgt}</code>", parse_mode="HTML")
        return

    current = await get_default_target()
    kb = await _build_target_keyboard()
    await message.reply(
        f"🎯 当前默认投递目标: <code>{current}</code>\n\n请在下方选择或回复 <code>/target &lt;目标ID/me/saved&gt;</code> 修改：",
        reply_markup=kb,
        parse_mode="HTML",
    )


@link_extractor_router.callback_query(F.data.startswith("set_tgt:"))
async def on_target_callback(callback: CallbackQuery):
    user_id = callback.from_user.id if callback.from_user else 0
    import bot_engine

    pyro = getattr(bot_engine, "pyro_user_app", None)
    if not await is_user_authorized(user_id, pyro):
        await callback.answer("🔒 无权限", show_alert=True)
        return

    new_target = callback.data.split(":", 1)[1]
    await set_default_target(new_target)
    await callback.answer(f"目标已切换为 {new_target}")

    # 更新文本
    kb = await _build_target_keyboard()
    try:
        await callback.message.edit_text(
            f"🎯 默认投递目标已切换为: <code>{new_target}</code>\n\n请在下方选择或回复 <code>/target &lt;目标ID/me/saved&gt;</code> 修改：",
            reply_markup=kb,
            parse_mode="HTML",
        )
    except Exception:
        pass


@link_extractor_router.message(Command("me", "saved", "to"), F.chat.type == "private")
async def cmd_explicit_target_extract(message: Message):
    """带显式目标的提取命令: /me <link>, /saved <link>, /to <channel> <link>"""
    user_id = message.from_user.id if message.from_user else 0
    import bot_engine

    pyro = getattr(bot_engine, "pyro_user_app", None)
    if not await is_user_authorized(user_id, pyro):
        await message.reply("🔒 无权限访问。")
        return

    text = message.text or ""
    parts = text.split()
    cmd = parts[0].lower().lstrip("/")

    target = "me"
    if cmd == "me":
        target = "me"
    elif cmd == "saved":
        target = "saved"
    elif cmd == "to":
        if len(parts) < 3:
            await message.reply("用法: <code>/to &lt;目标频道ID或用户名&gt; &lt;链接&gt;</code>", parse_mode="HTML")
            return
        target = parts[1]

    links = parse_telegram_links(text)
    if not links:
        await message.reply("⚠️ 未在消息中找到有效的 Telegram 消息链接。")
        return

    await _process_extracted_links(message, links, target, user_id)


@link_extractor_router.message(F.chat.type == "private", F.text.regexp(r"https?://(?:www\.)?(?:t\.me|telegram\.me)/\S+"))
async def on_private_link_received(message: Message):
    """用户私聊直接发送消息链接（按默认 target 处理）"""
    user_id = message.from_user.id if message.from_user else 0
    import bot_engine

    pyro = getattr(bot_engine, "pyro_user_app", None)
    if not await is_user_authorized(user_id, pyro):
        await message.reply("🔒 无权限访问。请使用 <code>/auth &lt;WebUI密码&gt;</code> 授权。", parse_mode="HTML")
        return

    links = parse_telegram_links(message.text or "")
    if not links:
        return

    default_target = await get_default_target()
    await _process_extracted_links(message, links, default_target, user_id)


async def _process_extracted_links(
    message: Message,
    links: list[dict[str, Any]],
    target: str,
    user_id: int,
):
    """统一处理链接提取流水线（按用户排队，保序执行）"""
    import bot_engine

    pyro = getattr(bot_engine, "pyro_user_app", None)
    aiobot = message.bot or getattr(bot_engine, "aiogram_bot", None)

    # 获取或初始化用户的排队锁
    lock = _user_locks.setdefault(user_id, asyncio.Lock())

    # 如果锁已被占用，提示正在排队
    status_msg = None
    if lock.locked():
        _user_waiters[user_id] = _user_waiters.get(user_id, 0) + 1
        pos = _user_waiters[user_id]
        status_msg = await message.reply(f"⏳ 前方还有 {pos} 个任务正在处理，已为您排队...")
        try:
            await lock.acquire()
        finally:
            _user_waiters[user_id] = max(0, _user_waiters.get(user_id, 1) - 1)
    else:
        await lock.acquire()

    try:
        if status_msg is None:
            status_msg = await message.reply("⏳ 正在解析链接并准备下载受限内容...")
        else:
            try:
                await status_msg.edit_text("⏳ 轮到您的任务了，正在解析链接并准备下载...")
            except Exception:
                pass

        async def update_status(text: str):
            try:
                await status_msg.edit_text(text)
            except Exception:
                pass

        success_count = 0
        fail_count = 0
        total = len(links)

        for idx, item in enumerate(links, start=1):
            c_id = item["chat_id"]
            m_id = item["message_id"]
            prefix = f"[{idx}/{total}] " if total > 1 else ""

            try:
                await update_status(f"{prefix}⏳ 正在提取 ({c_id} / {m_id})...")
                await extract_and_forward(
                    c_id,
                    m_id,
                    target,
                    pyro_user_app=pyro,
                    aiogram_bot=aiobot,
                    sender_user_id=user_id,
                    status_callback=lambda txt: update_status(f"{prefix}{txt}"),
                )
                success_count += 1
            except Exception as exc:
                logger.error(f"提取链接失败: {c_id}/{m_id}, 错误: {exc}", exc_info=True)
                fail_count += 1
                await message.reply(f"❌ 链接处理失败 (ID: {m_id}): {exc}")

        # 最终状态更新
        if success_count > 0 and fail_count == 0:
            await update_status(f"✅ 处理完成！已成功解除限制并投递到: <code>{target}</code>")
        elif success_count > 0 and fail_count > 0:
            await update_status(f"⚠️ 处理完成：{success_count} 个成功，{fail_count} 个失败。投递目标: <code>{target}</code>")
        else:
            await update_status("❌ 处理失败，请检查上方报错信息。")
    finally:
        lock.release()

