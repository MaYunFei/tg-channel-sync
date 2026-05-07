import json
import tempfile
import unittest
from pathlib import Path

import app_config


class AppConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        self.original_config_file = app_config.config_file
        self.original_ensure_dirs = app_config.ensure_runtime_dirs
        app_config.config_file = lambda: self.config_path
        app_config.ensure_runtime_dirs = lambda: self.config_path.parent.mkdir(parents=True, exist_ok=True)
        app_config.clear_config_cache()

    def tearDown(self):
        app_config.config_file = self.original_config_file
        app_config.ensure_runtime_dirs = self.original_ensure_dirs
        app_config.clear_config_cache()
        self.temp_dir.cleanup()

    def test_load_config_does_not_rewrite_existing_file(self):
        raw = {"telegram": {"bot_token": "abc"}, "server": {"port": "9000"}}
        self.config_path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        original_content = self.config_path.read_text(encoding="utf-8")

        loaded = app_config.load_config()

        self.assertEqual(loaded["telegram"]["bot_token"], "abc")
        self.assertEqual(loaded["server"]["port"], 9000)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), original_content)

    def test_get_config_uses_cache_until_save(self):
        first = app_config.get_config()
        second = app_config.get_config()

        self.assertEqual(first, second)
        self.assertTrue(self.config_path.exists())

        updated = app_config.save_config({"telegram": {"bot_token": "next"}})
        cached = app_config.get_config()

        self.assertEqual(updated["telegram"]["bot_token"], "next")
        self.assertEqual(cached["telegram"]["bot_token"], "next")

    def test_log_retention_defaults_are_present(self):
        config = app_config.get_config()

        self.assertEqual(config["sync"]["system_log_retention_limit"], 1000)
        self.assertEqual(config["sync"]["message_log_retention_limit"], 5000)

    def test_log_retention_invalid_values_fall_back_to_defaults(self):
        config = app_config.save_config(
            {
                "sync": {
                    "system_log_retention_limit": "abc",
                    "message_log_retention_limit": None,
                }
            }
        )

        self.assertEqual(config["sync"]["system_log_retention_limit"], 1000)
        self.assertEqual(config["sync"]["message_log_retention_limit"], 5000)

    def test_log_retention_values_are_clamped_to_minimum(self):
        config = app_config.save_config(
            {
                "sync": {
                    "system_log_retention_limit": 1,
                    "message_log_retention_limit": "99",
                }
            }
        )

        self.assertEqual(config["sync"]["system_log_retention_limit"], 100)
        self.assertEqual(config["sync"]["message_log_retention_limit"], 100)
