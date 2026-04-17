import unittest
from unittest.mock import AsyncMock, patch

from services import sync_services
from sync_worker import history


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

    def test_compute_progress_speed_uses_delta(self):
        speed = sync_services.compute_progress_speed(20 * 1024 * 1024, 10 * 1024 * 1024, 2)
        self.assertAlmostEqual(speed, 5.0)

    async def test_process_master_sync_json_does_not_require_source_id(self):
        with patch("sync_worker.history.db.get_all_settings", AsyncMock(return_value={})), \
             patch("sync_worker.history.resolve_chat_id", AsyncMock(return_value=-100456)) as mock_resolve, \
             patch("sync_worker.history.process_json_sync", AsyncMock()) as mock_process_json:
            await history.process_master_sync("json", "bot", "", "@target", 1, 0, 0, "fake.json", False, "")

        mock_process_json.assert_awaited_once()
        self.assertEqual(mock_resolve.await_count, 1)
