import unittest
from unittest.mock import AsyncMock, patch

from sync_worker.senders.targets import resolve_upload_target, should_fallback_to_user


class SenderTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_upload_target_uses_user_when_sender_is_user(self):
        fake_user = object()
        target = await resolve_upload_target("user", fake_user, [1, 2, 3])
        self.assertEqual(target["sender"], "user")
        self.assertIs(target["client"], fake_user)

    async def test_resolve_upload_target_falls_back_to_user_when_bot_size_not_supported(self):
        fake_user = object()
        with patch("sync_worker.senders.targets.bot_engine.should_upload_via_bot", return_value=False):
            target = await resolve_upload_target("bot", fake_user, [1024], allow_user_fallback=True)
        self.assertEqual(target["sender"], "user")

    async def test_resolve_upload_target_uses_pool_bot_when_available(self):
        fake_user = object()
        fake_bot = object()
        with patch("sync_worker.senders.targets.bot_engine.should_upload_via_bot", return_value=True), \
             patch("sync_worker.senders.targets.bot_engine.acquire_upload_bot", AsyncMock(return_value={"client": fake_bot, "label": "Bot#1"})):
            target = await resolve_upload_target("bot", fake_user, [100])
        self.assertEqual(target["sender"], "bot")
        self.assertIs(target["client"], fake_bot)

    def test_should_fallback_to_user(self):
        self.assertTrue(should_fallback_to_user("bot", True))
        self.assertFalse(should_fallback_to_user("bot", False))
        self.assertFalse(should_fallback_to_user("user", True))
