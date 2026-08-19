import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from kjfwd_bot.capabilities import CapabilityRegistry
from kjfwd_bot.config import ImageContextConfig
from kjfwd_bot.history import HistoryStore
from kjfwd_bot.handler import KJFWDHandler, action_client_id
from kjfwd_bot.models import ContextSnapshot, StoredMessage
from kjfwd_bot.prompt import PromptBuilder
from kjfwd_bot.router import ConversationRouter
from kjfwd_bot.service import BowxtTransport
from kjfwd_bot.transport import MessageEvent


class BowxtMigrationTests(unittest.TestCase):
    def test_action_client_id_is_stable_ascii_and_bowxt_compatible(self):
        value = action_client_id("reply", 7, "博特泰斯特")
        self.assertEqual(value, action_client_id("reply", 7, "博特泰斯特"))
        self.assertRegex(value, r"^[A-Za-z0-9_-]{1,80}$")

    def test_history_persists_sender_and_image_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store = HistoryStore(Path(directory) / "history.db")
            try:
                message, inserted = store.record_group_message(
                    "群",
                    "[图片]",
                    1000,
                    "bowxt:42",
                    sender="张三",
                    message_type="image",
                    image_path="/tmp/example.png",
                    image_mime_type="image/png",
                    image_sha256="abc",
                )
                self.assertTrue(inserted)
                self.assertEqual("张三", message.sender)
                self.assertEqual("image", message.message_type)
                self.assertEqual("/tmp/example.png", message.image_path)
                duplicate, inserted = store.record_group_message(
                    "群", "[图片]", 1001, "bowxt:42", sender="其他人"
                )
                self.assertFalse(inserted)
                self.assertEqual(message.id, duplicate.id)
                self.assertEqual("张三", duplicate.sender)
            finally:
                store.close()

    def test_prompt_contains_sender_and_openai_image_part_when_enabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system = root / "system.md"
            system.write_text("SYSTEM", encoding="utf-8")
            image = root / "image.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nexample")
            builder = PromptBuilder(
                system,
                CapabilityRegistry([]),
                image_context=ImageContextConfig(
                    enabled=True,
                    cache_path=root,
                    max_images=2,
                    max_image_bytes=1024,
                ),
            )
            message = StoredMessage(
                1,
                "群",
                "group",
                "[图片]",
                1000,
                "session",
                sender="张三",
                message_type="image",
                image_path=str(image),
                image_mime_type="image/png",
            )
            snapshot = ContextSnapshot("群", "session", 1, (message,))
            _system, content = builder.build(snapshot, "这张图怎么处理？", ())
            self.assertIsInstance(content, list)
            text = "\n".join(part.get("text", "") for part in content if part["type"] == "text")
            self.assertIn("群成员（张三）", text)
            image_parts = [part for part in content if part["type"] == "image_url"]
            self.assertEqual(1, len(image_parts))
            self.assertTrue(image_parts[0]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_router_prompt_exposes_current_and_historical_senders(self):
        conversation = SimpleNamespace(id="c1", title="打印机", updated_at=1000)
        messages = {
            "c1": (
                StoredMessage(1, "群", "group", "打印机脱机", 1000, "s", sender="张三"),
            )
        }
        prompt = ConversationRouter._build_user_prompt(
            "群", "还是不行", "张三", (conversation,), messages
        )
        self.assertIn("当前发送人：张三", prompt)
        self.assertIn("- 张三: 打印机脱机", prompt)

    def test_low_information_followup_prefers_same_sender_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system = root / "system.md"
            system.write_text("SYSTEM", encoding="utf-8")
            store = HistoryStore(root / "history.db")
            try:
                alice = store.create_conversation("群", title="打印机", now=1000)
                alice_message, _ = store.record_group_message(
                    "群", "打印机脱机", 1001, "bowxt:1", sender="张三"
                )
                store.bind_message_to_conversation(
                    alice_message.id, alice.id, trigger_at=1001
                )
                bob = store.create_conversation("群", title="蓝屏", now=1002)
                bob_message, _ = store.record_group_message(
                    "群", "电脑蓝屏", 1003, "bowxt:2", sender="李四"
                )
                store.bind_message_to_conversation(
                    bob_message.id, bob.id, trigger_at=1003
                )
                followup, _ = store.record_group_message(
                    "群", "还是不行", 1004, "bowxt:3", sender="张三"
                )
                handler = KJFWDHandler(
                    groups=("群",),
                    bot_nicknames={"群": "bot"},
                    history=store,
                    model=SimpleNamespace(),
                    prompt_builder=PromptBuilder(system, CapabilityRegistry([])),
                )
                _message, route = handler._route_message(
                    "群", followup, "还是不行", 1004, sender="张三"
                )
                self.assertEqual("use_existing", route.action)
                self.assertEqual(alice.id, route.conversation_id)
                self.assertEqual("low_information_same_sender", route.reason)
            finally:
                store.close()

    def test_image_can_continue_one_recent_conversation_when_configured(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            system = root / "system.md"
            system.write_text("SYSTEM", encoding="utf-8")
            store = HistoryStore(root / "history.db")
            try:
                conversation = store.create_conversation("群", title="蓝屏", now=1000)
                question, _ = store.record_group_message(
                    "群", "电脑蓝屏怎么办", 1001, "bowxt:1", sender="张三"
                )
                store.bind_message_to_conversation(
                    question.id, conversation.id, trigger_at=1001
                )
                image, _ = store.record_group_message(
                    "群",
                    "[图片]",
                    1002,
                    "bowxt:2",
                    sender="张三",
                    message_type="image",
                )
                image_config = ImageContextConfig(
                    enabled=True, trigger_images=True, lookback_seconds=180
                )
                handler = KJFWDHandler(
                    groups=("群",),
                    bot_nicknames={"群": "bot"},
                    history=store,
                    model=SimpleNamespace(),
                    prompt_builder=PromptBuilder(system, CapabilityRegistry([])),
                    image_context=image_config,
                )
                event = MessageEvent(
                    "群", "[图片]", 1002, "bot", False, sender="张三", message_type="image"
                )
                self.assertTrue(handler._image_should_trigger(event, image))
                _message, route = handler._route_message(
                    "群", image, "请结合图片继续分析。", 1002, sender="张三"
                )
                self.assertEqual(conversation.id, route.conversation_id)
                self.assertEqual("image_followup_same_sender", route.reason)
            finally:
                store.close()

    def test_bowxt_delivery_maps_stable_seq_sender_and_at_state(self):
        config = SimpleNamespace(
            group_nicknames={"博特泰斯特": "kirotta"},
            image_context=ImageContextConfig(enabled=False),
        )
        transport = BowxtTransport(SimpleNamespace(), config)
        message = SimpleNamespace(
            seq=123,
            chat="博特泰斯特",
            content="@kirotta 电脑蓝屏怎么办",
            timestamp="2026-08-20T01:00:00+08:00",
            observed_at="2026-08-19T17:00:00+00:00",
            is_at_me=True,
            sender="张三",
            message_type="text",
            image_mime_type=None,
            image_sha256=None,
        )
        event = transport.event_from_delivery(SimpleNamespace(message=message, attempt=1))
        self.assertEqual("bowxt:123", event.source_key)
        self.assertEqual("张三", event.sender)
        self.assertTrue(event.is_at_me)
        self.assertEqual("kirotta", event.group_nickname)


if __name__ == "__main__":
    unittest.main()
