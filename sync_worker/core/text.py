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


def _format_linked_label(prefix: str, label: str, href: str) -> str:
    escaped_label = html.escape(label)
    if not href:
        return f"{prefix} {escaped_label}"
    return f'{prefix} <a href="{href}">{escaped_label}</a>'


def _build_external_source_header(msg) -> str:
    parts: list[str] = []

    forwarded_from = str(_message_value(msg, "forwarded_from", "") or "").strip()
    forwarded_href = ""
    if not forwarded_from:
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
                forwarded_href = html.escape(f"tg://resolve?domain={forward_chat_username}", quote=True)
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
                forwarded_href = html.escape(f"tg://resolve?domain={forward_user_username}", quote=True)
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
        parts.append(_format_linked_label("#转发自", forwarded_from, forwarded_href))

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
        href = html.escape(
            f"tg://openmessage?chat_id={reply_to_peer_id}&message_id={reply_to_message_id}",
            quote=True,
        )
        parts.append(f'<a href="{href}">#回复自外部消息</a>')

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
