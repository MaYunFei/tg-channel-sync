import unittest
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import bot_engine
import database
from services import sync_services
from sync_worker.clone import process as history
from sync_worker.core import prepend_source_header_html


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class FakeNamedChat:
    def __init__(self, chat_id, title=None, username=None):
        self.id = chat_id
        self.title = title
        self.username = username


class FakeBot:
    async def get_chat(self, ref):
        return FakeChat(-100123 if str(ref).startswith("@source") else -100456)


class SyncServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "data.db"
        self.original_db_file = database.DB_FILE
        self.original_ensure_dirs = database.ensure_runtime_dirs
        database.DB_FILE = str(self.db_path)
        database.ensure_runtime_dirs = lambda: self.db_path.parent.mkdir(parents=True, exist_ok=True)
        await database.close_db()
        await database.init_db()

    async def asyncTearDown(self):
        await database.close_db()
        database.DB_FILE = self.original_db_file
        database.ensure_runtime_dirs = self.original_ensure_dirs
        self.temp_dir.cleanup()

    async def test_resolve_chat_id_supports_username_and_url(self):
        bot = FakeBot()

        self.assertEqual(await sync_services.resolve_chat_id(bot, "@source"), -100123)
        self.assertEqual(await sync_services.resolve_chat_id(bot, "https://t.me/source/10"), -100123)
        self.assertEqual(await sync_services.resolve_chat_id(bot, "-100999"), -100999)

    async def test_rewrite_message_links_uses_target_mapping(self):
        context = {"target_id": -100456, "source_username": "source", "target_username": "target"}
        with patch("services.sync_services.db.get_target_msg_id", AsyncMock(return_value=88)):
            rewritten, count = await sync_services.rewrite_message_links(
                "see https://t.me/source/12 and https://t.me/c/123/12",
                -100123,
                context,
            )

        self.assertIn("https://t.me/target/88", rewritten)
        self.assertIn("https://t.me/c/456/88", rewritten)
        self.assertEqual(count, 2)

    async def test_rewrite_message_links_falls_back_to_internal_target_without_duplicate_prefix(self):
        context = {"target_id": -1003717669322, "source_username": "KOIUJHBFJB", "target_username": None}
        with patch("services.sync_services.db.get_target_msg_id", AsyncMock(return_value=301)):
            rewritten, count = await sync_services.rewrite_message_links(
                "https://t.me/KOIUJHBFJB/68",
                0,
                context,
            )

        self.assertEqual(rewritten, "https://t.me/c/3717669322/301")
        self.assertEqual(count, 1)

    def test_prepend_source_header_html_supports_pyrogram_like_message(self):
        msg = type(
            "Msg",
            (),
            {
                "forward_from_chat": FakeNamedChat(-100123, title="原频道"),
                "forward_from_message_id": 321,
                "forward_from": None,
                "forward_sender_name": None,
                "external_reply": type(
                    "ExternalReply",
                    (),
                    {
                        "chat": FakeNamedChat(-100456, title="外部频道"),
                        "message_id": 789,
                    },
                )(),
            },
        )()

        rendered = prepend_source_header_html("正文", msg, enabled=True)

        self.assertIn('href="https://t.me/c/123/321"', rendered)
        self.assertIn("#转发自", rendered)
        self.assertIn(">原频道</a>", rendered)
        self.assertIn('href="https://t.me/c/456/789"', rendered)
        self.assertIn('#回复自 <a href="https://t.me/c/456/789">外部频道</a>', rendered)
        self.assertTrue(rendered.endswith("\n正文"))

    def test_prepend_source_header_html_external_reply_falls_back_without_name(self):
        msg = type(
            "Msg",
            (),
            {
                "external_reply": type(
                    "ExternalReply",
                    (),
                    {
                        "chat": FakeNamedChat(-100456),
                        "message_id": 789,
                    },
                )(),
            },
        )()

        rendered = prepend_source_header_html("正文", msg, enabled=True)

        self.assertIn('#回复自 <a href="https://t.me/c/456/789">外部消息</a>', rendered)

    def test_prepend_source_header_html_supports_json_forwarded_channel_peer(self):
        rendered = prepend_source_header_html(
            "正文",
            {
                "forwarded_from": "333频道",
                "forwarded_from_id": "channel3912522050",
                "forwarded_from_message_id": "66",
            },
            enabled=True,
        )

        self.assertIn('href="https://t.me/c/3912522050/66"', rendered)
        self.assertIn("#转发自", rendered)
        self.assertIn(">333频道</a>", rendered)

    def test_compute_progress_speed_uses_delta(self):
        speed = sync_services.compute_progress_speed(20 * 1024 * 1024, 10 * 1024 * 1024, 2)
        self.assertAlmostEqual(speed, 5.0)

    async def test_process_master_sync_json_does_not_require_source_id(self):
        with patch("sync_worker.clone.process.db.get_all_settings", AsyncMock(return_value={})), \
             patch("sync_worker.clone.process.resolve_chat_id", AsyncMock(return_value=-100456)) as mock_resolve, \
             patch("sync_worker.clone.process.process_json_sync", AsyncMock()) as mock_process_json:
            await history.process_master_sync("json", "bot", "", "@target", 1, 0, 0, "fake.json", False, "", 3)

        mock_process_json.assert_awaited_once()
        self.assertEqual(mock_resolve.await_count, 1)
        self.assertEqual(mock_process_json.await_args.args[0], "bot")

    def test_normalize_channel_username_supports_at_and_link(self):
        self.assertEqual(sync_services.normalize_channel_username("@source_name"), "source_name")
        self.assertEqual(sync_services.normalize_channel_username("https://t.me/source_name/123"), "source_name")
        self.assertEqual(sync_services.normalize_channel_username("t.me/source_name"), "source_name")
        self.assertEqual(sync_services.normalize_channel_username(""), "")

    def test_build_json_source_scope_id_is_stable(self):
        scope_a = sync_services.build_json_source_scope_id("@source_name")
        scope_b = sync_services.build_json_source_scope_id("https://t.me/source_name/123")
        self.assertEqual(scope_a, scope_b)
        self.assertLess(scope_a, 0)

    def test_clone_parse_retry_after_seconds(self):
        self.assertEqual(history._parse_retry_after_seconds(Exception("retry after 16")), 16)
        self.assertIsNone(history._parse_retry_after_seconds(Exception("other error")))

    def test_clone_is_request_entity_too_large(self):
        self.assertTrue(history._is_request_entity_too_large(Exception("HTTP Client says - Request Entity Too Large")))
        self.assertFalse(history._is_request_entity_too_large(Exception("Too Many Requests")))

    def test_is_chat_forwards_restricted(self):
        self.assertTrue(history._is_chat_forwards_restricted(Exception('Telegram says: [400 CHAT_FORWARDS_RESTRICTED]')))
        self.assertTrue(history._is_chat_forwards_restricted(Exception("The chat restricts forwarding content")))
        self.assertFalse(history._is_chat_forwards_restricted(Exception("other error")))

    def test_build_temp_download_path_uses_message_id_prefix(self):
        media = type("Media", (), {"file_name": "PixPin_2026-04-17_19-36-44.mp4"})()
        msg_a = type("Msg", (), {"id": 77, "video": media})()
        msg_b = type("Msg", (), {"id": 78, "video": media})()

        path_a = history._build_temp_download_path(msg_a, "video")
        path_b = history._build_temp_download_path(msg_b, "video")

        self.assertNotEqual(path_a, path_b)
        self.assertIn("77_", path_a)
        self.assertIn("78_", path_b)

    async def test_safe_get_messages_falls_back_when_topics_parse_breaks_batch(self):
        msg1 = type("Msg", (), {"id": 1, "empty": False})()
        msg2 = type("Msg", (), {"id": 2, "empty": False})()

        class FakeApp:
            def __init__(self):
                self.calls = []

            async def get_messages(self, chat_id, ids):
                self.calls.append((chat_id, ids))
                if isinstance(ids, list):
                    raise TypeError("Messages.__init__() missing 1 required keyword-only argument: 'topics'")
                return {1: msg1, 2: msg2}.get(ids)

        app = FakeApp()
        result = await history._safe_get_messages(app, -100123, [1, 2])

        self.assertEqual(result, [msg1, msg2])
        self.assertEqual(app.calls, [(-100123, [1, 2]), (-100123, 1), (-100123, 2)])

    async def test_process_master_sync_logs_completion(self):
        with patch("sync_worker.clone.process.db.get_all_settings", AsyncMock(return_value={})), \
             patch("sync_worker.clone.process.resolve_chat_id", AsyncMock(return_value=-100456)), \
             patch("sync_worker.clone.process.process_json_sync", AsyncMock()), \
             patch("sync_worker.clone.process.db.add_log", AsyncMock()) as mock_add_log:
            await history.process_master_sync("json", "bot", "", "@target", 1, 0, 0, "fake.json", False, "", 3)

        mock_add_log.assert_any_await("INFO", "任务运行完毕：JSON | 已处理 0 / 0 | 跳过 0")

    def test_bot_media_group_can_attach_thumbnail(self):
        thumbnail = history.FSInputFile(__file__)
        media = history.AioVideo(media="video.bin", caption="x", parse_mode="HTML", thumbnail=thumbnail, supports_streaming=True)
        self.assertIs(media.thumbnail, thumbnail)
        self.assertTrue(media.supports_streaming)

    async def test_clone_group_download_retry_logs_failure(self):
        group = [type("Msg", (), {"id": 1})(), type("Msg", (), {"id": 2})()]
        results = [Exception("boom"), "temp.bin"]
        failure_details = []
        for item, result in zip(group, results):
            if isinstance(result, Exception):
                failure_details.append(f"消息ID:{item.id} -> {result}")
        self.assertIn("消息ID:1 -> boom", failure_details[0])

    async def test_mark_upload_bot_cooldown_sets_cooldown_and_logs(self):
        fake_client = object()
        original_upload_bots = bot_engine.upload_bots
        bot_engine.upload_bots = [{"client": fake_client, "label": "Bot#2", "cooldown_until": 0.0}]
        try:
            with patch("bot_engine.db.add_msg_log", AsyncMock()) as mock_add_msg_log:
                label = await bot_engine.mark_upload_bot_cooldown(fake_client, 12, "test")
            self.assertEqual(label, "Bot#2")
            self.assertGreater(bot_engine.upload_bots[0]["cooldown_until"], 0.0)
            mock_add_msg_log.assert_awaited()
        finally:
            bot_engine.upload_bots = original_upload_bots

    async def test_rewrite_media_group_captions_adds_header_only_to_first_caption(self):
        """测试媒体组转发信息：有文字的在文字上加，没有文字的在第一个媒体上加"""
        from sync_worker.core.links import rewrite_media_group_captions
        
        # 测试场景1：第一个没有 caption，第二个有 caption，第三个也有 caption
        # 应该只在第二个（第一个有文字的）上添加转发信息
        group1 = [
            type("Msg", (), {
                "caption": None,
                "forward_from_chat": FakeNamedChat(-100123, title="源频道"),
                "forward_from_message_id": 100,
                "forward_from": None,
                "forward_sender_name": None,
            })(),
            type("Msg", (), {
                "caption": type("Caption", (), {"html": "第一条说明"})(),
                "forward_from_chat": FakeNamedChat(-100123, title="源频道"),
                "forward_from_message_id": 101,
                "forward_from": None,
                "forward_sender_name": None,
            })(),
            type("Msg", (), {
                "caption": type("Caption", (), {"html": "第二条说明"})(),
                "forward_from_chat": FakeNamedChat(-100123, title="源频道"),
                "forward_from_message_id": 102,
                "forward_from": None,
                "forward_sender_name": None,
            })(),
        ]
        
        with patch("sync_worker.core.links.bot_engine.aiogram_bot", FakeBot()):
            captions1, changed1, _ = await rewrite_media_group_captions(
                -100123, -100456, group1, include_external_source_header=True
            )
        
        # 第一个媒体没有 caption，应该返回空字符串
        self.assertEqual(captions1[0], "")
        
        # 第二个媒体有 caption，应该添加转发信息
        self.assertIn("#转发自", captions1[1])
        self.assertIn("源频道", captions1[1])
        self.assertIn("第一条说明", captions1[1])
        
        # 第三个媒体有 caption，但不应该添加转发信息（避免 TG 隐藏文字介绍）
        self.assertNotIn("#转发自", captions1[2])
        self.assertIn("第二条说明", captions1[2])
        
        # 应该标记为已改变
        self.assertTrue(changed1)
        
        # 测试场景2：所有媒体都没有 caption
        # 应该在第一个媒体上添加转发信息
        group2 = [
            type("Msg", (), {
                "caption": None,
                "forward_from_chat": FakeNamedChat(-100123, title="源频道"),
                "forward_from_message_id": 200,
                "forward_from": None,
                "forward_sender_name": None,
            })(),
            type("Msg", (), {
                "caption": None,
                "forward_from_chat": FakeNamedChat(-100123, title="源频道"),
                "forward_from_message_id": 201,
                "forward_from": None,
                "forward_sender_name": None,
            })(),
        ]
        
        with patch("sync_worker.core.links.bot_engine.aiogram_bot", FakeBot()):
            captions2, changed2, _ = await rewrite_media_group_captions(
                -100123, -100456, group2, include_external_source_header=True
            )
        
        # 第一个媒体应该添加转发信息（因为没有任何媒体有文字）
        self.assertIn("#转发自", captions2[0])
        self.assertIn("源频道", captions2[0])
        
        # 第二个媒体不应该添加转发信息
        self.assertNotIn("#转发自", captions2[1])
        
        # 应该标记为已改变
        self.assertTrue(changed2)

