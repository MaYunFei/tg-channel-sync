import unittest

from sync_worker.core import build_json_text, normalize_bot_html, resolve_json_media


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
                "reply_to_peer_id": "-100123",
                "reply_to_message_id": "456",
                "text": "正文",
            },
            include_external_source_header=True,
        )
        self.assertIn("#转发自 test_channel", rendered)
        self.assertIn('href="tg://openmessage?chat_id=-100123&amp;message_id=456"', rendered)
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

        self.assertEqual(resolve_json_media(msg_video, "X")[1], "video")
        self.assertEqual(resolve_json_media(msg_animation, "X")[1], "animation")
        self.assertEqual(resolve_json_media(msg_sticker, "X")[1], "sticker")

    def test_normalize_bot_html_rewrites_spoiler_tag(self):
        self.assertEqual(
            normalize_bot_html("a<spoiler>x</spoiler>b"),
            "a<tg-spoiler>x</tg-spoiler>b",
        )
