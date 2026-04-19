from __future__ import annotations

import html
import re


BOT_HTML_TAG_ALIASES = (
    (re.compile(r"<(/?)spoiler>"), r"<\1tg-spoiler>"),
)


def _message_value(msg, key, default=None):
    if isinstance(msg, dict):
        return msg.get(key, default)
    return getattr(msg, key, default)


def _normalize_peer_chat_id(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("-"):
        return raw
    if raw.isdigit():
        return raw if raw.startswith("100") else f"-100{raw}"

    match = re.fullmatch(r"channel(\d+)", raw)
    if match:
        return f"-100{match.group(1)}"
    match = re.fullmatch(r"chat(\d+)", raw)
    if match:
        return f"-{match.group(1)}"
    return raw


def _build_chat_href(chat_id: str) -> str:
    normalized_chat_id = _normalize_peer_chat_id(chat_id)
    if not normalized_chat_id:
        return ""
    if normalized_chat_id.startswith("-100"):
        internal_id = normalized_chat_id.removeprefix("-100")
        if re.fullmatch(r"\d+", internal_id):
            return html.escape(f"https://t.me/c/{internal_id}", quote=True)
    return html.escape(f"tg://openmessage?chat_id={normalized_chat_id}", quote=True)


def _build_user_href(user_id: str) -> str:
    raw = str(user_id or "").strip()
    if not raw:
        return ""
    match = re.fullmatch(r"user(\d+)", raw)
    normalized_user_id = match.group(1) if match else raw
    if not normalized_user_id or not re.fullmatch(r"\d+", normalized_user_id):
        return ""
    return html.escape(f"tg://user?id={normalized_user_id}", quote=True)


def _build_username_href(username: str) -> str:
    cleaned = str(username or "").strip().lstrip("@")
    if not re.fullmatch(r"[A-Za-z0-9_]{3,}", cleaned):
        return ""
    return html.escape(f"https://t.me/{cleaned}", quote=True)


def _build_external_reply_href(peer_id: str, message_id: str) -> str:
    normalized_peer = _normalize_peer_chat_id(peer_id)
    message_id = str(message_id or "").strip()
    if normalized_peer.startswith("-100") and re.fullmatch(r"\d+", message_id):
        internal_id = normalized_peer.removeprefix("-100")
        if re.fullmatch(r"\d+", internal_id):
            return html.escape(f"https://t.me/c/{internal_id}/{message_id}", quote=True)
    if normalized_peer and message_id:
        return html.escape(
            f"tg://openmessage?chat_id={normalized_peer}&message_id={message_id}",
            quote=True,
        )
    return ""


def _extract_forward_message_id(msg) -> str:
    # 优先使用新的 forward_origin API（aiogram 3.x+）
    forward_origin = _message_value(msg, "forward_origin")
    if forward_origin is not None:
        normalized = str(getattr(forward_origin, "message_id", "") or "").strip()
        if re.fullmatch(r"\d+", normalized):
            return normalized
    
    # 回退到旧的 API（兼容性）
    # 注意：forward_from_message_id 在 aiogram 3.x 中已弃用，但 pyrofork 仍在使用
    direct_fields = (
        _message_value(msg, "forwarded_from_message_id", ""),
        _message_value(msg, "forward_from_message_id", ""),
    )
    for value in direct_fields:
        normalized = str(value or "").strip()
        if re.fullmatch(r"\d+", normalized):
            return normalized
    
    return ""


def _append_message_id_to_forward_href(href: str, message_id: str) -> str:
    if not href:
        return ""
    clean_href = str(href or "").strip()
    normalized_msg_id = str(message_id or "").strip()
    if not re.fullmatch(r"\d+", normalized_msg_id):
        return clean_href
    if clean_href.startswith("https://t.me/c/"):
        return f"{clean_href}/{normalized_msg_id}"
    if clean_href.startswith("https://t.me/"):
        # Public channels/users support message deep-link via /<message_id>.
        return f"{clean_href}/{normalized_msg_id}"
    return clean_href


def _format_linked_label(prefix: str, label: str, href: str) -> str:
    escaped_label = html.escape(label)
    if not href:
        return f"{prefix} {escaped_label}"
    return f'{prefix} <a href="{href}">{escaped_label}</a>'


def _build_external_source_header(msg) -> str:
    parts: list[str] = []
    forward_msg_id = _extract_forward_message_id(msg)

    forwarded_from = str(_message_value(msg, "forwarded_from", "") or "").strip()
    forwarded_href = ""
    
    if not forwarded_from:
        # 优先使用新的 forward_origin API（aiogram 3.x+）
        forward_origin = _message_value(msg, "forward_origin")
        
        if forward_origin is not None:
            origin_type = getattr(forward_origin, "__class__", None).__name__ if hasattr(forward_origin, "__class__") else ""
            
            # MessageOriginChannel - 来自频道
            if hasattr(forward_origin, "chat"):
                forward_from_chat = getattr(forward_origin, "chat", None)
                if forward_from_chat is not None:
                    forwarded_from = str(
                        getattr(forward_from_chat, "title", None)
                        or getattr(forward_from_chat, "username", None)
                        or getattr(forward_from_chat, "id", "")
                        or ""
                    ).strip()
                    forward_chat_username = str(getattr(forward_from_chat, "username", "") or "").strip()
                    if forward_chat_username:
                        forwarded_href = _build_username_href(forward_chat_username)
                    else:
                        forwarded_href = _build_chat_href(getattr(forward_from_chat, "id", ""))
            
            # MessageOriginUser - 来自用户
            elif hasattr(forward_origin, "sender_user"):
                forward_from_user = getattr(forward_origin, "sender_user", None)
                if forward_from_user is not None:
                    forwarded_from = str(
                        getattr(forward_from_user, "full_name", None)
                        or " ".join(
                            part
                            for part in [
                                getattr(forward_from_user, "first_name", None),
                                getattr(forward_from_user, "last_name", None),
                            ]
                            if part
                        )
                        or getattr(forward_from_user, "username", None)
                        or getattr(forward_from_user, "id", "")
                        or ""
                    ).strip()
                    forward_user_username = str(getattr(forward_from_user, "username", "") or "").strip()
                    if forward_user_username:
                        forwarded_href = _build_username_href(forward_user_username)
                    else:
                        forwarded_href = _build_user_href(getattr(forward_from_user, "id", ""))
            
            # MessageOriginHiddenUser - 隐藏用户
            elif hasattr(forward_origin, "sender_user_name"):
                forward_sender_name = str(getattr(forward_origin, "sender_user_name", "") or "").strip()
                if forward_sender_name:
                    forwarded_from = forward_sender_name
        
        # 回退到旧的 API（兼容性）
        else:
            forward_from_chat = _message_value(msg, "forward_from_chat")
            forward_from_user = _message_value(msg, "forward_from")
            forward_sender_name = str(_message_value(msg, "forward_sender_name", "") or "").strip()
            
            if forward_from_chat is not None:
                forwarded_from = str(
                    getattr(forward_from_chat, "title", None)
                    or getattr(forward_from_chat, "username", None)
                    or getattr(forward_from_chat, "id", "")
                    or ""
                ).strip()
                forward_chat_username = str(getattr(forward_from_chat, "username", "") or "").strip()
                if forward_chat_username:
                    forwarded_href = _build_username_href(forward_chat_username)
                else:
                    forwarded_href = _build_chat_href(getattr(forward_from_chat, "id", ""))
            elif forward_from_user is not None:
                forwarded_from = str(
                    getattr(forward_from_user, "full_name", None)
                    or " ".join(
                        part
                        for part in [
                            getattr(forward_from_user, "first_name", None),
                            getattr(forward_from_user, "last_name", None),
                        ]
                        if part
                    )
                    or getattr(forward_from_user, "username", None)
                    or getattr(forward_from_user, "id", "")
                    or ""
                ).strip()
                forward_user_username = str(getattr(forward_from_user, "username", "") or "").strip()
                if forward_user_username:
                    forwarded_href = _build_username_href(forward_user_username)
                else:
                    forwarded_href = _build_user_href(getattr(forward_from_user, "id", ""))
            elif forward_sender_name:
                forwarded_from = forward_sender_name
    else:
        forwarded_from_id = _message_value(msg, "forwarded_from_id", "")
        forwarded_href = _build_chat_href(forwarded_from_id)
        if not forwarded_href:
            forwarded_href = _build_user_href(forwarded_from_id)
    
    if forwarded_from:
        parts.append(_format_linked_label("#转发自", forwarded_from, _append_message_id_to_forward_href(forwarded_href, forward_msg_id)))

    reply_to_peer_id = str(_message_value(msg, "reply_to_peer_id", "") or "").strip()
    reply_to_message_id = str(_message_value(msg, "reply_to_message_id", "") or "").strip()
    if (not reply_to_peer_id or not reply_to_message_id) and not isinstance(msg, dict):
        external_reply = _message_value(msg, "external_reply")
        if external_reply is not None:
            external_chat = getattr(external_reply, "chat", None)
            reply_to_peer_id = str(
                getattr(external_chat, "id", None)
                or getattr(external_reply, "chat_id", "")
                or ""
            ).strip()
            reply_to_message_id = str(
                getattr(external_reply, "message_id", None)
                or getattr(external_reply, "id", "")
                or ""
            ).strip()
    if reply_to_peer_id and reply_to_message_id:
        href = _build_external_reply_href(reply_to_peer_id, reply_to_message_id)
        if href:
            parts.append(f'<a href="{href}">#回复自外部消息</a>')
        else:
            parts.append("#回复自外部消息")

    return "\n".join(parts)


def prepend_source_header_html(text: str | None, msg, *, enabled: bool = False) -> str:
    rendered = str(text or "")
    if not enabled:
        return rendered
    prefix = _build_external_source_header(msg)
    if not prefix:
        return rendered
    return f"{prefix}\n{rendered}" if rendered else prefix


def normalize_bot_html(text: str | None) -> str:
    normalized = str(text or "")
    for pattern, replacement in BOT_HTML_TAG_ALIASES:
        normalized = pattern.sub(replacement, normalized)
    return normalized


def build_json_text(msg, *, include_external_source_header: bool = False):
    text = msg.get("text", "")
    if not isinstance(text, list):
        return prepend_source_header_html(html.escape(str(text or "")), msg, enabled=include_external_source_header)

    def render_entity(item):
        if isinstance(item, str):
            return html.escape(item)
        if not isinstance(item, dict):
            return html.escape(str(item))

        entity_type = item.get("type", "plain")
        raw_text = str(item.get("text", ""))
        entity_text = html.escape(raw_text)
        href = str(item.get("href", "") or "")

        wrappers = {
            "bold": ("<b>", "</b>"),
            "italic": ("<i>", "</i>"),
            "underline": ("<u>", "</u>"),
            "strikethrough": ("<s>", "</s>"),
            "spoiler": ("<tg-spoiler>", "</tg-spoiler>"),
            "code": ("<code>", "</code>"),
            "blockquote": ("<blockquote>", "</blockquote>"),
        }

        if entity_type == "text_link" and href:
            return f'<a href="{html.escape(href, quote=True)}">{entity_text}</a>'
        if entity_type == "link":
            return f'<a href="{html.escape(raw_text, quote=True)}">{entity_text}</a>'
        if entity_type == "pre":
            language = str(item.get("language", "") or "").strip()
            if language:
                return f'<pre><code class="language-{html.escape(language, quote=True)}">{entity_text}</code></pre>'
            return f"<pre>{entity_text}</pre>"
        if entity_type == "blockquote":
            if bool(item.get("collapsed", False)):
                return f"<blockquote expandable>{entity_text}</blockquote>"
            return f"<blockquote>{entity_text}</blockquote>"
        if entity_type in wrappers:
            start, end = wrappers[entity_type]
            return f"{start}{entity_text}{end}"
        return entity_text

    rendered = "".join(render_entity(item) for item in text)
    return prepend_source_header_html(rendered, msg, enabled=include_external_source_header)
