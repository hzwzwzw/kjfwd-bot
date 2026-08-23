import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace

from kjfwd_bot.history import HistoryStore
from kjfwd_bot.panels import ConversationPanelPublisher, conversation_panel_nodes


class _Client:
    def __init__(self):
        self.calls = []

    def publish_panel(self, panel_id, title, nodes, *, empty_text):
        self.calls.append((panel_id, title, nodes, empty_text))
        return {"id": panel_id}


class ConversationPanelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.history = HistoryStore(Path(self.temp.name) / "history.db")
        self.config = SimpleNamespace(
            group_names=("客户群", "空群"),
            conversation_pool=SimpleNamespace(active_ttl_seconds=1800, max_active=5),
        )

    def tearDown(self):
        self.history.close()
        self.temp.cleanup()

    def test_panel_shows_only_live_conversations_and_concrete_messages(self):
        live = self.history.create_conversation("客户群", title="打印机", now=2000)
        old = self.history.create_conversation("客户群", title="过期", now=100)
        message, _ = self.history.record_group_message(
            "客户群", "打印机脱机", 2001, "m1",
            sender="黄泽文", sender_organization="柯基服务队",
        )
        self.history.bind_message_to_conversation(message.id, live.id, trigger_at=2001)
        old_message, _ = self.history.record_group_message("客户群", "旧问题", 101, "m2")
        self.history.bind_message_to_conversation(old_message.id, old.id, trigger_at=101)

        nodes = conversation_panel_nodes(self.history, self.config, now=2100)

        self.assertEqual(nodes[0]["label"], "客户群")
        self.assertEqual([item["label"] for item in nodes[0]["children"]], [live.id])
        record = nodes[0]["children"][0]["children"][0]
        self.assertIn("黄泽文", record["label"])
        self.assertEqual(record["meta"], "柯基服务队")
        self.assertEqual(record["value"], "打印机脱机")
        self.assertEqual(nodes[1]["meta"], "0 个活跃会话")

    def test_publisher_skips_unchanged_document(self):
        client = _Client()
        publisher = ConversationPanelPublisher(
            client, self.history, self.config, threading.Event()
        )
        self.assertTrue(publisher.publish_once())
        self.assertFalse(publisher.publish_once())
        self.assertEqual(len(client.calls), 1)


if __name__ == "__main__":
    unittest.main()
