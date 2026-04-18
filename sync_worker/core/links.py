from __future__ import annotations

from pyrogram.enums import ParseMode
from pyrogram.types import InputMediaAudio, InputMediaDocument, InputMediaPhoto, InputMediaVideo

import bot_engine
from services.sync_services import build_link_rewrite_context, rewrite_message_links

from .text import normalize_bot_html


PYRO_MEDIA_CLS = {
    "photo": InputMediaPhoto,
    "video": InputMediaVideo,
    "audio": InputMediaAudio,
    "document": InputMediaDocument,
}


async def rewrite_media_group_captions(source_id, target_id, group, source_username_override=None):
    link_context = await build_link_rewrite_context(
        bot_engine.aiogram_bot,
        source_id,
        target_id,
        source_username_override=source_username_override,
    )
    captions = []
    changed = False
    total_rewrites = 0

    for item in group:
        original_caption = item.caption.html if item.caption else ""
        rewritten_caption, rewrite_count = await rewrite_message_links(original_caption, source_id, link_context)
        rewritten_caption = normalize_bot_html(rewritten_caption)
        if rewritten_caption != original_caption:
            changed = True
        total_rewrites += rewrite_count
        captions.append(rewritten_caption)

    return captions, changed, total_rewrites
