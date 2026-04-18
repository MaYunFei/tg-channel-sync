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
