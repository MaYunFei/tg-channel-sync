import unittest
from unittest.mock import AsyncMock, patch

import bot_engine
from services import sync_services
from sync_worker.clone import process as history


class FakeChat:
    def __init__(self, chat_id):
        self.id = chat_id


class FakeBot:
    async def get_chat(self, ref):
        return FakeChat(-100123 if str(ref).startswith("@source") else -100456)


class SyncServiceTests(unittest.IsolatedAsyncioTestCase):
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
