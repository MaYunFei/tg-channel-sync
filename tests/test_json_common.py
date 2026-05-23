import unittest

from sync_worker.core import (
    build_json_text,
    get_msg_meta,
    has_media_spoiler,
    has_text_spoiler,
    normalize_bot_html,
    normalize_pyro_html,
    resolve_json_media,
)


class JsonCommonTests(unittest.TestCase):
    def test_build_json_text_renders_entities_as_html(self):
        msg = {
            "text": [
                {"type": "bold", "text": "B"},
                " ",
                {"type": "italic", "text": "I"},
                " ",
                {"type": "text_link", "text": "文档", "href": "https://example.com/doc"},
                " ",
                {"type": "link", "text": "https://t.me/source/1"},
            ]
        }
        rendered = build_json_text(msg)
        self.assertIn("<b>B</b>", rendered)
        self.assertIn("<i>I</i>", rendered)
        self.assertIn('<a href="https://example.com/doc">文档</a>', rendered)
        self.assertIn('<a href="https://t.me/source/1">https://t.me/source/1</a>', rendered)

    def test_build_json_text_keeps_pre_language(self):
        rendered = build_json_text(
            {
                "text": [
                    {"type": "pre", "text": '{"a":1}', "language": "Json"},
                ]
            }
        )
        self.assertEqual(rendered, '<pre><code class="language-Json">{&quot;a&quot;:1}</code></pre>')

    def test_build_json_text_can_prepend_external_source_header(self):
        rendered = build_json_text(
            {
                "forwarded_from": "test_channel",
                "forwarded_from_id": "channel3717669322",
                "forwarded_from_message_id": "888",
                "reply_to_peer_id": "-100123",
                "reply_to_message_id": "456",
                "text": "正文",
            },
            include_external_source_header=True,
        )
        self.assertIn('href="https://t.me/c/3717669322/888"', rendered)
        self.assertIn("#转发自", rendered)
        self.assertIn(">test_channel</a>", rendered)
        self.assertIn('href="https://t.me/c/123/456"', rendered)
        self.assertIn('#回复自 <a href="https://t.me/c/123/456">外部消息</a>', rendered)
        self.assertTrue(rendered.endswith("\n正文"))

    def test_build_json_text_supports_expandable_blockquote(self):
        rendered = build_json_text(
            {
                "text": [
                    {"type": "blockquote", "text": "普通引用", "collapsed": False},
                    {"type": "plain", "text": "\n\n"},
                    {"type": "blockquote", "text": "折叠引用", "collapsed": True},
                ]
            }
        )
        self.assertEqual(
            rendered,
            "<blockquote>普通引用</blockquote>\n\n<blockquote expandable>折叠引用</blockquote>",
        )

    def test_resolve_json_media_uses_media_type_for_file_payload(self):
        msg_video = {"media_type": "video_file", "file": "video_files/a.mp4"}
        msg_animation = {"media_type": "animation", "file": "video_files/b.mp4"}
        msg_sticker = {"media_type": "sticker", "file": "stickers/a.tgs"}

        self.assertEqual(resolve_json_media(msg_video, "X")[1], "document")
        self.assertEqual(resolve_json_media(msg_animation, "X")[1], "animation")
        self.assertEqual(resolve_json_media(msg_sticker, "X")[1], "sticker")

    def test_get_msg_meta_treats_json_video_file_as_document(self):
        self.assertEqual(
            get_msg_meta({"media_type": "video_file", "file": "video_files/a.mp4"}, "json"),
            ("document", "sync_document"),
        )

    def test_has_media_spoiler_reads_json_export_flag(self):
        self.assertTrue(has_media_spoiler({"media_spoiler": True}, "photo", "json"))
        self.assertFalse(has_media_spoiler({"media_spoiler": False}, "photo", "json"))

    def test_has_media_spoiler_reads_pyrogram_message_flag(self):
        class Media:
            file_id = "file-id"

        class Message:
            photo = Media()
            has_media_spoiler = True

        self.assertTrue(has_media_spoiler(Message(), "photo", "api"))

    def test_has_media_spoiler_reads_pyrogram_raw_media_flag(self):
        class RawMedia:
            spoiler = True

        class RawMessage:
            media = RawMedia()

        class Media:
            file_id = "file-id"

        class Message:
            photo = Media()
            raw = RawMessage()

        self.assertTrue(has_media_spoiler(Message(), "photo", "api"))

    def test_has_text_spoiler_reads_html_and_entities(self):
        self.assertTrue(has_text_spoiler(html_text="a<tg-spoiler>x</tg-spoiler>"))
        self.assertTrue(has_text_spoiler({"entities": [{"type": "spoiler"}]}, "plain"))
        self.assertFalse(has_text_spoiler({"entities": [{"type": "bold"}]}, "plain"))

    def test_normalize_bot_html_rewrites_spoiler_tag(self):
        self.assertEqual(
            normalize_bot_html("a<spoiler>x</spoiler>b"),
            "a<tg-spoiler>x</tg-spoiler>b",
        )

    def test_normalize_pyro_html_rewrites_tg_spoiler_tag(self):
        self.assertEqual(
            normalize_pyro_html("a<tg-spoiler>x</tg-spoiler>b"),
            "a<spoiler>x</spoiler>b",
        )
