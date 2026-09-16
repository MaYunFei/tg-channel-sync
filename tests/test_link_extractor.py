import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from services import link_extractor


class TestLinkExtractor(unittest.IsolatedAsyncioTestCase):
    def test_parse_telegram_links(self):
        # 1. 私有链接
        text1 = "请帮我提取: https://t.me/c/1234567890/456 谢谢"
        res1 = link_extractor.parse_telegram_links(text1)
        self.assertEqual(len(res1), 1)
        self.assertEqual(res1[0]["chat_id"], -1001234567890)
        self.assertEqual(res1[0]["message_id"], 456)
        self.assertTrue(res1[0]["is_private"])

        # 2. 公开频道链接（带参数）
        text2 = "看这个: https://t.me/test_channel/789?single"
        res2 = link_extractor.parse_telegram_links(text2)
        self.assertEqual(len(res2), 1)
        self.assertEqual(res2[0]["chat_id"], "test_channel")
        self.assertEqual(res2[0]["message_id"], 789)
        self.assertFalse(res2[0]["is_private"])

        # 3. 多个链接提取
        text3 = "链接1: https://t.me/c/111/10 链接2: https://t.me/chan/20"
        res3 = link_extractor.parse_telegram_links(text3)
        self.assertEqual(len(res3), 2)
        self.assertEqual(res3[0]["message_id"], 10)
        self.assertEqual(res3[1]["message_id"], 20)

    @patch("database.get_all_settings")
    async def test_get_default_target(self, mock_settings):
        mock_settings.return_value = {"link_extractor_target": " -100123456 "}
        tgt = await link_extractor.get_default_target()
        self.assertEqual(tgt, "-100123456")

        mock_settings.return_value = {}
        tgt_default = await link_extractor.get_default_target()
        self.assertEqual(tgt_default, "me")

    @patch("database.update_settings")
    async def test_set_default_target(self, mock_update):
        await link_extractor.set_default_target("saved")
        mock_update.assert_called_once_with({"link_extractor_target": "saved"})

    @patch("database.get_all_settings")
    async def test_is_user_authorized(self, mock_settings):
        mock_settings.return_value = {"link_extractor_admins": "12345, 67890"}

        # 1. 在白名单里的用户
        auth1 = await link_extractor.is_user_authorized(12345)
        self.assertTrue(auth1)

        # 2. 不在白名单里的用户
        auth2 = await link_extractor.is_user_authorized(99999)
        self.assertFalse(auth2)

        # 3. 辅助账号本人的 ID
        fake_pyro = MagicMock()
        fake_pyro.is_connected = True
        fake_pyro.me = MagicMock(id=99999)
        auth3 = await link_extractor.is_user_authorized(99999, fake_pyro)
        self.assertTrue(auth3)

    @patch("database.get_all_settings")
    @patch("database.update_settings")
    async def test_add_allowed_admin(self, mock_update, mock_settings):
        mock_settings.return_value = {"link_extractor_admins": "111"}
        await link_extractor.add_allowed_admin(222)
        mock_update.assert_called_once_with({"link_extractor_admins": "111,222"})

    async def test_extract_and_forward_text(self):
        pyro_mock = AsyncMock()
        pyro_mock.is_connected = True

        fake_msg = MagicMock()
        fake_msg.empty = False
        fake_msg.media_group_id = None
        fake_msg.text = MagicMock(html="<b>测试文本</b>")
        for attr in ["photo", "video", "document", "audio", "voice", "animation", "sticker"]:
            setattr(fake_msg, attr, None)

        pyro_mock.get_messages.return_value = fake_msg

        aiobot_mock = AsyncMock()

        # 投递到 'me' -> 用户私聊
        res = await link_extractor.extract_and_forward(
            -100111,
            123,
            "me",
            pyro_user_app=pyro_mock,
            aiogram_bot=aiobot_mock,
            sender_user_id=88888,
        )
        self.assertTrue(res["success"])
        aiobot_mock.send_message.assert_called_once_with(88888, "<b>测试文本</b>", parse_mode="HTML")

    async def test_extract_and_forward_photo(self):
        pyro_mock = AsyncMock()
        pyro_mock.is_connected = True

        fake_photo = MagicMock(file_size=1024, file_name="pic.jpg")
        fake_msg = MagicMock()
        fake_msg.empty = False
        fake_msg.id = 55
        fake_msg.media_group_id = None
        fake_msg.text = None
        fake_msg.photo = fake_photo
        fake_msg.caption = MagicMock(html="Photo caption")
        for attr in ["video", "document", "audio", "voice", "animation", "sticker"]:
            setattr(fake_msg, attr, None)

        pyro_mock.get_messages.return_value = fake_msg

        # mock download_media 生成临时文件
        async def fake_download(m, file_name):
            os.makedirs(os.path.dirname(file_name), exist_ok=True)
            with open(file_name, "wb") as f:
                f.write(b"dummy image data")
            return file_name

        pyro_mock.download_media.side_effect = fake_download
        aiobot_mock = AsyncMock()

        res = await link_extractor.extract_and_forward(
            "my_chan",
            55,
            -100999999,
            pyro_user_app=pyro_mock,
            aiogram_bot=aiobot_mock,
            sender_user_id=88888,
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["via"], "bot")
        aiobot_mock.send_photo.assert_called_once()
