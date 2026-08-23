import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kjfwd_bot.documents import MarkdownDocumentLibrary
from kjfwd_bot.tool_context import current_tool_group
from kjfwd_bot.tools import (
    GetHistoryTool,
    ListDocumentsTool,
    ReadDocumentTool,
    parse_duration_seconds,
)


class FakeBowxtClient:
    def __init__(self):
        self.calls = []

    def get_history(self, chat, *, duration_seconds):
        self.calls.append((chat, duration_seconds))
        return [
            SimpleNamespace(
                seq=10,
                timestamp="2026-08-23T10:00:00+00:00",
                observed_at="2026-08-23T10:00:01+00:00",
                direction="incoming",
                sender="黄泽文",
                sender_organization="柯基服务队",
                message_type="text",
                content="请继续排查",
                is_at_me=True,
            )
        ]


class ToolTests(unittest.TestCase):
    def test_duration_parser_accepts_supported_units_and_rejects_large_ranges(self):
        self.assertEqual(parse_duration_seconds("30m"), 1800)
        self.assertEqual(parse_duration_seconds("1h"), 3600)
        self.assertEqual(parse_duration_seconds("1d"), 86400)
        with self.assertRaises(ValueError):
            parse_duration_seconds("32d")

    def test_get_history_is_scoped_to_current_group_and_preserves_sender_metadata(self):
        client = FakeBowxtClient()
        tool = GetHistoryTool(client)
        with current_tool_group("科技服务队服务②群"):
            payload = json.loads(tool.execute({"duration": "1h"}))

        self.assertEqual(client.calls, [("科技服务队服务②群", 3600)])
        self.assertTrue(payload["complete"])
        self.assertEqual(payload["message_count"], 1)
        self.assertEqual(payload["messages"][0]["sender"], "黄泽文")
        self.assertEqual(
            payload["messages"][0]["sender_organization"], "柯基服务队"
        )
        with self.assertRaises(RuntimeError):
            tool.execute({"duration": "1h"})

    def test_kjfwd_document_tree_and_tools_are_local_and_path_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "documents"
            path = root / "调试" / "文档.md"
            path.parent.mkdir(parents=True)
            path.write_text("# 工具自检\n\nKJFWD_LOCAL_DOC", encoding="utf-8")
            library = MarkdownDocumentLibrary(root)

            listing = json.loads(ListDocumentsTool(library).execute({}))
            self.assertEqual(listing["tree"][0]["type"], "directory")
            self.assertEqual(listing["documents"][0]["path"], "调试/文档.md")
            document = json.loads(
                ReadDocumentTool(library).execute({"path": "调试/文档.md"})
            )
            self.assertIn("KJFWD_LOCAL_DOC", document["content"])
            with self.assertRaises(ValueError):
                library.read("../secret.md")


if __name__ == "__main__":
    unittest.main()
