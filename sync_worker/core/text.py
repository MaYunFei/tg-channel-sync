from __future__ import annotations

import html
import re


BOT_HTML_TAG_ALIASES = (
    (re.compile(r"<(/?)spoiler>"), r"<\1tg-spoiler>"),
)


def normalize_bot_html(text: str | None) -> str:
    normalized = str(text or "")
    for pattern, replacement in BOT_HTML_TAG_ALIASES:
        normalized = pattern.sub(replacement, normalized)
    return normalized


def build_json_text(msg):
    text = msg.get("text", "")
    if not isinstance(text, list):
        return html.escape(str(text or ""))

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
            "pre": ("<pre>", "</pre>"),
            "blockquote": ("<blockquote>", "</blockquote>"),
        }

        if entity_type == "text_link" and href:
            return f'<a href="{html.escape(href, quote=True)}">{entity_text}</a>'
        if entity_type == "link":
            return f'<a href="{html.escape(raw_text, quote=True)}">{entity_text}</a>'
        if entity_type in wrappers:
            start, end = wrappers[entity_type]
            return f"{start}{entity_text}{end}"
        return entity_text

    return "".join(render_entity(item) for item in text)
