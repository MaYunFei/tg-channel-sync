from __future__ import annotations

import html
import re


BOT_HTML_TAG_ALIASES = (
    (re.compile(r"<(/?)spoiler>"), r"<\1tg-spoiler>"),
)


def _build_external_source_header(msg) -> str:
    parts: list[str] = []

    forwarded_from = str(msg.get("forwarded_from", "") or "").strip()
    if forwarded_from:
        parts.append(f"#转发自 {html.escape(forwarded_from)}")

    reply_to_peer_id = str(msg.get("reply_to_peer_id", "") or "").strip()
    reply_to_message_id = str(msg.get("reply_to_message_id", "") or "").strip()
    if reply_to_peer_id and reply_to_message_id:
        href = html.escape(
            f"tg://openmessage?chat_id={reply_to_peer_id}&message_id={reply_to_message_id}",
            quote=True,
        )
        parts.append(f'<a href="{href}">#回复自外部消息</a>')

    return "\n".join(parts)


def normalize_bot_html(text: str | None) -> str:
    normalized = str(text or "")
    for pattern, replacement in BOT_HTML_TAG_ALIASES:
        normalized = pattern.sub(replacement, normalized)
    return normalized


def build_json_text(msg, *, include_external_source_header: bool = False):
    text = msg.get("text", "")
    if not isinstance(text, list):
        rendered = html.escape(str(text or ""))
        if include_external_source_header:
            prefix = _build_external_source_header(msg)
            if prefix:
                return f"{prefix}\n{rendered}" if rendered else prefix
        return rendered

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
    if include_external_source_header:
        prefix = _build_external_source_header(msg)
        if prefix:
            return f"{prefix}\n{rendered}" if rendered else prefix
    return rendered
