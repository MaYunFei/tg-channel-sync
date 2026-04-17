import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sync_worker import json_sync


class FakeSentMessage:
    def __init__(self, message_id):
        self.message_id = message_id


class JsonSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_json_sync_respects_type_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "result.json"
            json_path.write_text(
                json.dumps(
                    {
                        "messages": [
                            {
                                "id": 1,
                                "type": "message",
                                "photo": "photos/pic.jpg",
                                "text": "caption",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch("sync_worker.json_sync.resolve_chat_id", AsyncMock(return_value=-100456)), \
                 patch("sync_worker.json_sync.build_link_rewrite_context", AsyncMock(return_value={})), \
                 patch("sync_worker.json_sync.db.get_all_settings", AsyncMock(return_value={"sync_photo": "0"})), \
                 patch("sync_worker.json_sync.db.add_msg_log", AsyncMock()) as mock_add_msg_log, \
                 patch("sync_worker.json_sync.db.apply_message_filters", AsyncMock(return_value=(False, "caption"))), \
                 patch("sync_worker.json_sync.update_state_and_check_skip", AsyncMock(return_value=False)), \
                 patch("sync_worker.json_sync.record_success", AsyncMock()), \
                 patch("sync_worker.json_sync.bot_engine.aiogram_bot") as mock_bot:
                mock_bot.send_photo = AsyncMock(return_value=FakeSentMessage(1001))

                await json_sync.process_json_sync("@target", str(json_path), 0.5, False)

            mock_bot.send_photo.assert_not_awaited()
            mock_add_msg_log.assert_any_await("JSON_DROP_TYPE", "消息ID:1 | 类型:photo | 已被类型过滤拦截")

    async def test_process_json_sync_respects_regex_filter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "result.json"
            json_path.write_text(
                json.dumps(
                    {
                        "messages": [
                            {
                                "id": 2,
                                "type": "message",
                                "text": "blocked content",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch("sync_worker.json_sync.resolve_chat_id", AsyncMock(return_value=-100456)), \
                 patch("sync_worker.json_sync.build_link_rewrite_context", AsyncMock(return_value={})), \
                 patch("sync_worker.json_sync.db.get_all_settings", AsyncMock(return_value={"sync_text": "1"})), \
                 patch("sync_worker.json_sync.db.add_msg_log", AsyncMock()) as mock_add_msg_log, \
                 patch("sync_worker.json_sync.db.apply_message_filters", AsyncMock(return_value=(True, "blocked content"))), \
                 patch("sync_worker.json_sync.update_state_and_check_skip", AsyncMock(return_value=False)), \
                 patch("sync_worker.json_sync.record_success", AsyncMock()), \
                 patch("sync_worker.json_sync.bot_engine.aiogram_bot") as mock_bot:
                mock_bot.send_message = AsyncMock(return_value=FakeSentMessage(1002))

                await json_sync.process_json_sync("@target", str(json_path), 0.5, False)

            mock_bot.send_message.assert_not_awaited()
            mock_add_msg_log.assert_any_await("JSON_DROP_REGEX", "消息ID:2 | 已被正则过滤拦截")
