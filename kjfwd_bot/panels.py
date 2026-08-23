from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any

from .config import BotConfig
from .history import HistoryStore
from .models import StoredMessage

logger = logging.getLogger(__name__)

CONVERSATION_PANEL_ID = "active-conversations"
CONVERSATION_PANEL_TITLE = "会话信息"


def conversation_panel_nodes(
    history: HistoryStore,
    config: BotConfig,
    *,
    now: float | None = None,
    messages_per_conversation: int = 8,
) -> list[dict[str, Any]]:
    """Build group -> active conversation -> message nodes for WebIM."""

    current = time.time() if now is None else float(now)
    groups: list[dict[str, Any]] = []
    for group_name in config.group_names:
        conversations = history.list_active_conversations(
            group_name,
            now=current,
            ttl_seconds=config.conversation_pool.active_ttl_seconds,
            limit=config.conversation_pool.max_active,
        )
        conversation_nodes: list[dict[str, Any]] = []
        for conversation in conversations:
            messages = history.conversation_recent_messages(
                conversation.id, limit=messages_per_conversation
            )
            message_nodes = [_message_node(item) for item in messages]
            omitted = max(conversation.message_count - len(messages), 0)
            if omitted:
                message_nodes.insert(
                    0,
                    {
                        "id": f"omitted:{conversation.id}",
                        "label": f"更早的 {omitted} 条记录未在面板中展开",
                        "meta": "历史仍保存在 kjfwd 数据库中",
                        "tone": "info",
                    },
                )
            conversation_nodes.append(
                {
                    "id": f"conversation:{conversation.id}",
                    "label": conversation.id,
                    "meta": (
                        f"{conversation.title} · {conversation.message_count} 条 · "
                        f"更新于 {_format_time(conversation.updated_at)}"
                    ),
                    "expanded": False,
                    "children": message_nodes,
                }
            )
        groups.append(
            {
                "id": f"group:{group_name}",
                "label": group_name,
                "meta": f"{len(conversations)} 个活跃会话",
                "tone": "success" if conversations else "neutral",
                "expanded": bool(conversations),
                "children": conversation_nodes,
            }
        )
    return groups


class ConversationPanelPublisher:
    """Publish panel changes without coupling them to message claim/ack latency."""

    def __init__(
        self,
        client: Any,
        history: HistoryStore,
        config: BotConfig,
        stop_event: threading.Event,
        *,
        interval_seconds: float = 2.0,
    ):
        self.client = client
        self.history = history
        self.config = config
        self.stop_event = stop_event
        self.interval_seconds = max(float(interval_seconds), 0.2)
        self._last_digest = ""
        self._thread = threading.Thread(
            target=self._run,
            name="kjfwd-conversation-panel",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def publish_once(self) -> bool:
        nodes = conversation_panel_nodes(self.history, self.config)
        encoded = json.dumps(nodes, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        if digest == self._last_digest:
            return False
        self.client.publish_panel(
            CONVERSATION_PANEL_ID,
            CONVERSATION_PANEL_TITLE,
            nodes,
            empty_text="当前没有活跃会话",
        )
        self._last_digest = digest
        return True

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.publish_once()
            except Exception as exc:
                logger.warning("发布 bowxt 会话信息面板失败：%s", exc)
            if self.stop_event.wait(self.interval_seconds):
                return


def _message_node(message: StoredMessage) -> dict[str, Any]:
    if message.role == "assistant":
        sender = "kirotta"
        meta = "Agent 回复"
        tone = "info"
    else:
        sender = message.sender or "未知发送者"
        meta = message.sender_organization or "群成员"
        tone = "neutral"
    content = message.content
    if message.message_type == "image" and not content.strip():
        content = "[图片]"
    if len(content) > 240:
        content = content[:237] + "…"
    return {
        "id": f"message:{message.id}",
        "label": f"{_format_time(message.observed_at)} · {sender}",
        "meta": meta,
        "value": content,
        "tone": tone,
    }


def _format_time(value: float) -> str:
    return datetime.fromtimestamp(float(value)).strftime("%m-%d %H:%M:%S")
