import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kjfwd_bot.config import load_config
from kjfwd_bot.service import process_group_names


class ConfigTests(unittest.TestCase):
    def test_managed_instance_overrides_bowxt_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps({
                "groups": [{"name": "答疑群", "bot_nickname": "kirotta"}],
                "llm": {"base_url": "https://example.com/v1", "model": "test"},
                "search": {"enabled": False},
                "bowxt": {"base_url": "http://wrong:1", "consumer": "shared"},
            }), encoding="utf-8")
            with patch.dict("os.environ", {
                "API_KEY": "key",
                "BOWXT_BASE_URL": "http://127.0.0.1:8787",
                "BOWXT_CONSUMER": "kjfwd-prod",
            }, clear=True):
                config = load_config(config_path)
            self.assertEqual(config.bowxt.base_url, "http://127.0.0.1:8787")
            self.assertEqual(config.bowxt.consumer, "kjfwd-prod")

    def test_group_listen_modes_and_reply_groups_are_configurable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "name": "答疑群",
                                "bot_nickname": "柯基服务队",
                                "listen_mode": "question_only",
                                "always_reply_to_mentions": True,
                                "reply_groups": ["机器人参考群"],
                            },
                            {
                                "name": "全量群",
                                "bot_nickname": "柯基服务队",
                                "listen_mode": "all_messages",
                                "reply_groups": ["参考一", "参考二"],
                            },
                        ],
                        "llm": {"base_url": "https://example.com/v1", "model": "test"},
                        "search": {"enabled": False},
                        "message_reminder": {
                            "enabled": True,
                            "group": "值班提醒群",
                            "source_groups": ["答疑群"],
                            "delay_seconds": 120,
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(
                config_path,
                environ={"API_KEY": "key"},
            )
            self.assertEqual(("答疑群", "全量群"), config.group_names)
            self.assertEqual("question_only", config.listen_modes["答疑群"])
            self.assertEqual("all_messages", config.listen_modes["全量群"])
            self.assertTrue(config.always_reply_to_mentions["答疑群"])
            self.assertFalse(config.always_reply_to_mentions["全量群"])
            self.assertEqual(("机器人参考群",), config.reply_groups["答疑群"])
            self.assertEqual(("参考一", "参考二"), config.reply_groups["全量群"])
            self.assertEqual(0.0, config.reply_debounce.delay_seconds)
            self.assertTrue(config.message_reminder.enabled)
            self.assertEqual("值班提醒群", config.message_reminder.group)
            self.assertEqual(("答疑群",), config.message_reminder.source_groups)
            self.assertEqual(120, config.message_reminder.delay_seconds)
            self.assertEqual(
                ("答疑群", "全量群", "机器人参考群", "参考一", "参考二"),
                process_group_names(config),
            )

    def test_reply_debounce_delay_is_configurable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "groups": [{"name": "答疑群", "bot_nickname": "柯基服务队"}],
                        "llm": {"base_url": "https://example.com/v1", "model": "test"},
                        "search": {"enabled": False},
                        "reply_debounce": {"delay_seconds": 2.5},
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(config_path, environ={"API_KEY": "key"})
            self.assertEqual(2.5, config.reply_debounce.delay_seconds)

    def test_document_library_path_is_owned_by_plugin_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "groups": [{"name": "答疑群", "bot_nickname": "kirotta"}],
                        "llm": {"base_url": "https://example.com/v1", "model": "test"},
                        "search": {"enabled": False},
                        "documents": {
                            "root_path": "knowledge",
                            "max_document_bytes": 12345,
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = load_config(config_path, environ={"API_KEY": "key"})
            self.assertEqual(config.documents.root_path, root / "knowledge")
            self.assertEqual(config.documents.max_document_bytes, 12345)

    def test_invalid_listen_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "groups": [
                            {
                                "name": "答疑群",
                                "bot_nickname": "柯基服务队",
                                "listen_mode": "unknown",
                            }
                        ],
                        "llm": {"base_url": "https://example.com/v1", "model": "test"},
                        "search": {"enabled": False},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(config_path, environ={"API_KEY": "key"})

    def test_enabled_message_reminder_requires_group(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "groups": [{"name": "答疑群", "bot_nickname": "柯基服务队"}],
                        "llm": {"base_url": "https://example.com/v1", "model": "test"},
                        "search": {"enabled": False},
                        "message_reminder": {"enabled": True, "delay_seconds": 60},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(config_path, environ={"API_KEY": "key"})

    def test_message_reminder_rejects_unlistened_source_group(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "groups": [{"name": "答疑群", "bot_nickname": "柯基服务队"}],
                        "llm": {"base_url": "https://example.com/v1", "model": "test"},
                        "search": {"enabled": False},
                        "message_reminder": {
                            "enabled": True,
                            "group": "值班提醒群",
                            "source_groups": ["未监听群"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(config_path, environ={"API_KEY": "key"})


if __name__ == "__main__":
    unittest.main()
