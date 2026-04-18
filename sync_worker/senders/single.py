from __future__ import annotations

from aiogram.types import ReplyParameters

import bot_engine

from ..core.text import normalize_bot_html


async def dynamic_send(
    client,
    msg_type,
    chat_id,
    file_ref,
    caption,
    parse_mode,
    reply_to_message_id=None,
    quote_data=None,
    progress=None,
    progress_args=None,
    thumbnail=None,
):
    method_name = "send_message" if msg_type == "text" else f"send_{msg_type}"
    method = getattr(client, method_name, None) or getattr(client, "send_document")
    kwargs = {"chat_id": chat_id}
    if msg_type != "sticker":
        kwargs["parse_mode"] = parse_mode
    if quote_data and reply_to_message_id:
        if bot_engine.is_bot_client(client):
            kwargs["reply_parameters"] = ReplyParameters(
                message_id=reply_to_message_id,
                quote=quote_data["text"],
                quote_position=quote_data.get("position"),
            )
        else:
            kwargs["reply_to_message_id"] = reply_to_message_id
            kwargs["quote_text"] = quote_data["text"]
            if quote_data.get("entities"):
                kwargs["quote_entities"] = quote_data["entities"]
    elif reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
    if msg_type == "sticker":
        kwargs["sticker"] = file_ref
    elif msg_type != "text":
        kwargs["caption"] = normalize_bot_html(caption) if bot_engine.is_bot_client(client) else caption
        kwargs[msg_type if hasattr(client, method_name) else "document"] = file_ref
        if thumbnail is not None:
            if bot_engine.is_bot_client(client):
                kwargs["thumbnail"] = thumbnail
                if msg_type == "video":
                    kwargs["supports_streaming"] = True
            else:
                kwargs["thumb"] = thumbnail
    else:
        kwargs["text"] = normalize_bot_html(caption) if bot_engine.is_bot_client(client) else caption
    if not bot_engine.is_bot_client(client) and progress is not None:
        kwargs["progress"] = progress
        if progress_args:
            kwargs["progress_args"] = progress_args
    return await method(**kwargs)
