import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sync_worker.json_import import process as json_sync


class FakeSentMessage:
    def __init__(self, message_id):
        self.message_id = message_id


class JsonSyncTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_retry_after_seconds(self):
        self.assertEqual(json_sync._parse_retry_after_seconds(Exception("retry after 21")), 21)
        self.assertIsNone(json_sync._parse_retry_after_seconds(Exception("other error")))

    def test_is_request_entity_too_large(self):
        self.assertTrue(json_sync._is_request_entity_too_large(Exception("HTTP Client says - Request Entity Too Large")))
        self.assertFalse(json_sync._is_request_entity_too_large(Exception("Too Many Requests")))

    async def test_send_json_single_via_user_raises_fatal_when_user_not_logged_in(self):
        with patch("sync_worker.json_import.process.bot_engine.pyro_user_app") as mock_app:
            mock_app.is_initialized = False
            with self.assertRaises(json_sync.JsonSyncFatalError):
                await json_sync._send_json_single_via_user(
                    -100456,
                    "document",
                    "fake.txt",
                    "caption",
                    None,
                    json_sync.SharedUploadProgressTracker("上传中", 1),
                    "上传文件: fake.txt",
                )

    def test_group_json_messages_groups_documents_without_mixing_visual_media(self):
        messages = [
            {
                "id": 97,
                "type": "message",
                "date_unixtime": "1776427654",
                "file": "files/testfile.txt",
                "text": "文件组中1介绍",
            },
            {
                "id": 98,
                "type": "message",
                "date_unixtime": "1776427654",
                "file": "files/testfile (1).txt",
                "text": "文件组中2介绍",
            },
            {
                "id": 99,
                "type": "message",
                "date_unixtime": "1776427655",
                "file": "files/testfile (2).txt",
                "text": "文件组中3介绍",
            },
            {
                "id": 100,
                "type": "message",
                "date_unixtime": "1776427656",
                "file": "files/testfile (3).txt",
                "text": "",
            },
            {
                "id": 101,
                "type": "message",
                "date_unixtime": "1776427656",
                "photo": "photos/pic.jpg",
                "text": "图片说明",
            },
        ]

        grouped = json_sync.group_json_messages(messages, 3)

        self.assertEqual([[item["id"] for item in group] for group in grouped], [[97, 98, 99, 100], [101]])

    async def test_send_json_media_group_keeps_document_captions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            for name in ["a.txt", "b.txt"]:
                (Path(temp_dir) / name).write_text(name, encoding="utf-8")

            group = [
                {
                    "id": 1,
                    "type": "message",
                    "date_unixtime": "1",
                    "file": "a.txt",
                    "text": "第一条说明",
                },
                {
                    "id": 2,
                    "type": "message",
                    "date_unixtime": "2",
                    "file": "b.txt",
                    "text": "第二条说明",
                },
            ]

            mock_send = AsyncMock(return_value=[FakeSentMessage(1001), FakeSentMessage(1002)])
            with patch("sync_worker.json_import.process.update_state_and_check_skip", AsyncMock(return_value=False)), \
                 patch("sync_worker.json_import.process.resolve_reply_target", AsyncMock(return_value=None)), \
                 patch("sync_worker.json_import.process.rewrite_message_links", AsyncMock(side_effect=lambda text, *_: (text, 0))), \
                 patch("sync_worker.json_import.process.record_success", AsyncMock()), \
                 patch("sync_worker.json_import.process.db.add_msg_log", AsyncMock()), \
                 patch("sync_worker.json_import.process.bot_engine.aiogram_bot") as mock_bot:
                mock_bot.send_media_group = mock_send

                await json_sync.send_json_media_group(group, -100456, temp_dir, 0, False, {}, "bot", True)

            media = mock_send.await_args.args[1]
            self.assertEqual([item.caption for item in media], ["第一条说明", "第二条说明"])

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

            with patch("sync_worker.json_import.process.resolve_chat_id", AsyncMock(return_value=-100456)), \
                 patch("sync_worker.json_import.process.build_link_rewrite_context", AsyncMock(return_value={})), \
                 patch("sync_worker.json_import.process.db.get_all_settings", AsyncMock(return_value={"sync_photo": "0"})), \
                 patch("sync_worker.json_import.process.db.add_msg_log", AsyncMock()) as mock_add_msg_log, \
                 patch("sync_worker.json_import.process.db.apply_message_filters", AsyncMock(return_value=(False, "caption"))), \
                 patch("sync_worker.json_import.process.update_state_and_check_skip", AsyncMock(return_value=False)), \
                 patch("sync_worker.json_import.process.record_success", AsyncMock()), \
                 patch("sync_worker.json_import.process.bot_engine.aiogram_bot") as mock_bot:
                mock_bot.send_photo = AsyncMock(return_value=FakeSentMessage(1001))

                await json_sync.process_json_sync("bot", "@target", str(json_path), 0.5, False)

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

            with patch("sync_worker.json_import.process.resolve_chat_id", AsyncMock(return_value=-100456)), \
                 patch("sync_worker.json_import.process.build_link_rewrite_context", AsyncMock(return_value={})), \
                 patch("sync_worker.json_import.process.db.get_all_settings", AsyncMock(return_value={"sync_text": "1"})), \
                 patch("sync_worker.json_import.process.db.add_msg_log", AsyncMock()) as mock_add_msg_log, \
                 patch("sync_worker.json_import.process.db.apply_message_filters", AsyncMock(return_value=(True, "blocked content"))), \
                 patch("sync_worker.json_import.process.update_state_and_check_skip", AsyncMock(return_value=False)), \
                 patch("sync_worker.json_import.process.record_success", AsyncMock()), \
                 patch("sync_worker.json_import.process.bot_engine.aiogram_bot") as mock_bot:
                mock_bot.send_message = AsyncMock(return_value=FakeSentMessage(1002))

                await json_sync.process_json_sync("bot", "@target", str(json_path), 0.5, False)

    async def test_process_json_sync_can_send_text_via_user(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            json_path = Path(temp_dir) / "result.json"
            json_path.write_text(
                json.dumps(
                    {
                        "messages": [
                            {
                                "id": 3,
                                "type": "message",
                                "text": "hello",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            fake_user = type("FakeUser", (), {"is_initialized": True, "send_message": AsyncMock(return_value=FakeSentMessage(1003))})()
            with patch("sync_worker.json_import.process.resolve_chat_id", AsyncMock(return_value=-100456)), \
                 patch("sync_worker.json_import.process.build_link_rewrite_context", AsyncMock(return_value={})), \
                 patch("sync_worker.json_import.process.db.get_all_settings", AsyncMock(return_value={"sync_text": "1"})), \
                 patch("sync_worker.json_import.process.db.add_msg_log", AsyncMock()), \
                 patch("sync_worker.json_import.process.db.apply_message_filters", AsyncMock(return_value=(False, "hello"))), \
                 patch("sync_worker.json_import.process.update_state_and_check_skip", AsyncMock(return_value=False)), \
                 patch("sync_worker.json_import.process.record_success", AsyncMock()), \
                 patch("sync_worker.json_import.process.bot_engine.pyro_user_app", fake_user), \
                 patch("sync_worker.json_import.process.bot_engine.aiogram_bot"):
                await json_sync.process_json_sync("user", "@target", str(json_path), 0.5, False)

            fake_user.send_message.assert_awaited_once()

    async def test_prepare_json_media_path_preserves_original_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            original = Path(temp_dir) / "photo.jpg"
            original.write_bytes(b"abc123")
            with patch("sync_worker.json_import.process.TEMP_DIR", temp_dir), \
                 patch("sync_worker.json_import.process.db.add_msg_log", AsyncMock()):
                prepared_path, created_temp = await json_sync._prepare_json_media_path(str(original), "photo", 11, True)

            self.assertTrue(created_temp)
            self.assertNotEqual(prepared_path, str(original))
            self.assertEqual(original.read_bytes(), b"abc123")
            self.assertTrue(Path(prepared_path).exists())
            self.assertGreater(Path(prepared_path).stat().st_size, original.stat().st_size)
