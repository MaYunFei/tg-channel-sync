import sqlite3
import tempfile
import unittest
from pathlib import Path

import database


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "data.db"
        self.original_db_file = database.DB_FILE
        self.original_ensure_dirs = database.ensure_runtime_dirs
        database.DB_FILE = str(self.db_path)
        database.ensure_runtime_dirs = lambda: self.db_path.parent.mkdir(parents=True, exist_ok=True)
        await database.close_db()

    async def asyncTearDown(self):
        await database.close_db()
        database.DB_FILE = self.original_db_file
        database.ensure_runtime_dirs = self.original_ensure_dirs
        self.temp_dir.cleanup()

    async def test_init_db_migrates_old_tables(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE channel_mappings (id INTEGER PRIMARY KEY AUTOINCREMENT, source_id INTEGER NOT NULL UNIQUE, target_id INTEGER NOT NULL)")
        conn.execute("INSERT INTO channel_mappings (source_id, target_id) VALUES (1001, 2001)")
        conn.execute("CREATE TABLE message_mappings (id INTEGER PRIMARY KEY AUTOINCREMENT, source_channel_id INTEGER NOT NULL, source_msg_id INTEGER NOT NULL, target_msg_id INTEGER NOT NULL)")
        conn.execute("INSERT INTO message_mappings (source_channel_id, source_msg_id, target_msg_id) VALUES (1001, 11, 99)")
        conn.commit()
        conn.close()

        await database.init_db()

        self.assertEqual(await database.get_target_channels(1001), [2001])
        [mapping] = await database.get_target_channel_mappings(1001)
        self.assertEqual(mapping["target_id"], 2001)
        self.assertEqual(mapping["realtime_sender"], "bot")
        self.assertTrue(mapping["realtime_fallback_to_user"])
        self.assertFalse(mapping["realtime_hash_perturb"])
        self.assertEqual(await database.get_target_msg_id(1001, 11, 2001), 99)

    async def test_log_retention_keeps_latest_100(self):
        await database.init_db()
        for index in range(105):
            await database.add_sys_log("INFO", f"log-{index}")

        rows = await database.get_recent_sys_logs()
        self.assertEqual(len(rows), 100)
        self.assertEqual(rows[0][3], "log-104")
        self.assertEqual(rows[-1][3], "log-5")

    async def test_multi_target_message_mapping_isolated(self):
        await database.init_db()
        await database.save_msg_mapping(1, 10, 100, 500)
        await database.save_msg_mapping(1, 10, 200, 600)

        self.assertEqual(await database.get_target_msg_id(1, 10, 100), 500)
        self.assertEqual(await database.get_target_msg_id(1, 10, 200), 600)

    async def test_channel_mapping_realtime_options_are_per_mapping(self):
        await database.init_db()
        await database.add_channel_mapping(
            1,
            100,
            realtime_sender="user",
            realtime_fallback_to_user=False,
            realtime_hash_perturb=True,
        )
        await database.add_channel_mapping(1, 200)

        mappings = await database.get_target_channel_mappings(1)

        self.assertEqual(
            mappings,
            [
                {
                    "target_id": 100,
                    "realtime_sender": "user",
                    "realtime_fallback_to_user": False,
                    "realtime_hash_perturb": True,
                },
                {
                    "target_id": 200,
                    "realtime_sender": "bot",
                    "realtime_fallback_to_user": True,
                    "realtime_hash_perturb": False,
                },
            ],
        )

    async def test_channel_mapping_duplicate_and_cycle_detection(self):
        await database.init_db()
        await database.add_channel_mapping(1, 2)
        await database.add_channel_mapping(2, 3)

        self.assertTrue(await database.has_channel_mapping(1, 2))
        self.assertTrue(await database.would_create_channel_mapping_cycle(1, 1))
        self.assertTrue(await database.would_create_channel_mapping_cycle(3, 1))
        self.assertFalse(await database.would_create_channel_mapping_cycle(3, 4))
