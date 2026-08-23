"""Cross-project smoke test: bowxt simulation -> kjfwd consumer -> reply."""

from __future__ import annotations

import base64
import json
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from bowxt import ChatType
from bowxt.models import MessageImage
from bowxt.service import BowxtService
from bowxt.store import SQLiteStore
from bowxt.web import BowxtHTTPServer

from kjfwd_bot.service import run


ROOT = Path(__file__).resolve().parents[1]
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class FakeOpenAIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.server.payloads.append(payload)  # type: ignore[attr-defined]
        system = str(payload.get("messages", [{}])[0].get("content", ""))
        if "只负责给群聊机器人做会话路由" in system:
            content = json.dumps(
                {"action": "create_new", "title": "CI 测试", "reason": "deterministic"},
                ensure_ascii=False,
            )
        else:
            content = "CI_REPLY_OK"
        body = json.dumps(
            {"choices": [{"message": {"role": "assistant", "content": content}}]},
            ensure_ascii=False,
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def wait_until(predicate, *, timeout: float, label: str) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {label}")


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        store = SQLiteStore(root / "bowxt.db")
        bowxt_service = BowxtService(store, client_factory=lambda: None, poll_gap=1.5)
        chat = bowxt_service.add_simulated_chat("CI 模拟群", ChatType.GROUP)
        bowxt_server = BowxtHTTPServer(("127.0.0.1", 0), bowxt_service)
        bowxt_thread = threading.Thread(target=bowxt_server.serve_forever, daemon=True)

        llm_server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOpenAIHandler)
        llm_server.payloads = []  # type: ignore[attr-defined]
        llm_thread = threading.Thread(target=llm_server.serve_forever, daemon=True)
        bowxt_thread.start()
        llm_thread.start()

        config = {
            "groups": [
                {
                    "name": "CI 模拟群",
                    "bot_nickname": "kirotta",
                    "listen_mode": "mention_only",
                    "always_reply_to_mentions": True,
                }
            ],
            "llm": {
                "base_url": f"http://127.0.0.1:{llm_server.server_port}/v1",
                "model": "ci-model",
                "temperature": 0,
                "max_tokens": 700,
                "timeout_seconds": 3,
                "retries": 0,
            },
            "bowxt": {
                "base_url": f"http://127.0.0.1:{bowxt_server.server_port}",
                "consumer": "kjfwd-ci",
                "claim_timeout_seconds": 0.2,
                "lease_seconds": 30,
                "batch_size": 8,
                "require_sender": True,
                "replay_existing": False,
                "send_timeout_seconds": 3,
            },
            "search": {"enabled": False, "max_tool_rounds": 1},
            "documents": {"root_path": str(ROOT / "documents")},
            "history": {
                "database_path": str(root / "kjfwd.db"),
                "idle_timeout_seconds": 1800,
                "max_messages": 100,
                "max_characters": 16000,
                "retention_days": 30,
                "trigger_dedupe_seconds": 1,
            },
            "conversation_pool": {
                "active_ttl_seconds": 1800,
                "max_active": 5,
                "global_fallback_seconds": 3600,
                "global_fallback_max_messages": 80,
                "low_information_recent_reply_seconds": 180,
            },
            "debug": {"conversation_id_in_reply": False},
            "reply_debounce": {"delay_seconds": 0},
            "message_reminder": {"enabled": False, "delay_seconds": 300},
            "image_context": {
                "enabled": True,
                "cache_path": str(root / "images"),
                "max_images": 4,
                "max_image_bytes": 1048576,
                "detail": "auto",
                "trigger_images": False,
                "lookback_seconds": 180,
                "require_viewer_clipboard": False,
            },
            "system_prompt_path": str(ROOT / "prompts" / "system.md"),
            "skills_path": str(ROOT / "skills"),
            "queue_size_per_group": 5,
        }
        config_path = root / "config.json"
        config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
        env_path = root / ".env"
        env_path.write_text("API_KEY=ci-key\n", encoding="utf-8")
        stopping = threading.Event()
        errors: list[BaseException] = []

        def run_agent() -> None:
            try:
                run(config_path, env_path, stop_event=stopping)
            except BaseException as exc:  # make thread failures visible to CI
                errors.append(exc)

        agent_thread = threading.Thread(target=run_agent, name="kjfwd-ci-agent")
        agent_thread.start()
        try:
            wait_until(
                lambda: store.get_agent_consumer_activity("kjfwd-ci")["last_claim_at"] is not None,
                timeout=8,
                label="consumer baseline claim",
            )
            bowxt_service.inject_simulated_message(
                chat.id,
                sender="黄泽文",
                sender_organization="柯基服务队",
                image=MessageImage(PNG_1X1, width=1, height=1, source="simulation_upload"),
            )
            wait_until(
                lambda: any(
                    log.event == "message_accepted"
                    for log in store.get_agent_logs(agent="kjfwd-ci", limit=20)
                ),
                timeout=8,
                label="image acceptance",
            )
            bowxt_service.inject_simulated_message(
                chat.id,
                text="@kirotta 请结合刚才的图片回答 CI 问题",
                sender="黄泽文",
                sender_organization="柯基服务队",
                is_at_me=True,
            )

            def replied() -> bool:
                return any(
                    item.direction == "outgoing" and item.content.startswith("CI_REPLY_OK")
                    for item in store.latest_messages(limit=30)
                )

            try:
                wait_until(replied, timeout=12, label="Agent reply")
            except AssertionError:
                print(
                    "Agent logs:",
                    json.dumps(
                        [item.as_dict() for item in store.get_agent_logs(agent="kjfwd-ci", limit=50)],
                        ensure_ascii=False,
                    ),
                )
                print(
                    "bowxt messages:",
                    json.dumps(
                        [item.as_dict() for item in store.latest_messages(limit=50)],
                        ensure_ascii=False,
                    ),
                )
                print("LLM payload count:", len(llm_server.payloads))  # type: ignore[attr-defined]
                raise
            assert not errors, errors
            payloads = llm_server.payloads  # type: ignore[attr-defined]
            answer_payloads = [
                item
                for item in payloads
                if "只负责给群聊机器人做会话路由"
                not in str(item.get("messages", [{}])[0].get("content", ""))
            ]
            assert answer_payloads, payloads
            user_content = answer_payloads[-1]["messages"][1]["content"]
            assert isinstance(user_content, list), user_content
            serialized = json.dumps(user_content, ensure_ascii=False)
            assert "黄泽文" in serialized
            assert "柯基服务队" in serialized
            assert '"type": "image_url"' in serialized
            assert "data:image/png;base64," in serialized
            assert any(log.event == "message_accepted" for log in store.get_agent_logs(agent="kjfwd-ci", limit=20))
        finally:
            stopping.set()
            agent_thread.join(8)
            bowxt_server.shutdown()
            bowxt_server.server_close()
            llm_server.shutdown()
            llm_server.server_close()
            bowxt_thread.join(3)
            llm_thread.join(3)
        assert not agent_thread.is_alive()
        assert not errors, errors
    print("kjfwd-bot cross-project simulation chain passed")


if __name__ == "__main__":
    main()
