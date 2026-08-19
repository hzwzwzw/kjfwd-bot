from __future__ import annotations

import hashlib
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .agent import ToolCallingAgent
from .capabilities import CapabilityRegistry
from .classifier import LLMQuestionClassifier
from .config import BotConfig, load_config
from .handler import KJFWDHandler
from .history import HistoryStore
from .llm import OpenAIChatClient
from .prompt import PromptBuilder
from .router import ConversationRouter
from .search import BraveSearchClient, WebSearchTool
from .transport import ForwardAction, MessageEvent, ReplyAction

logger = logging.getLogger(__name__)


def process_group_names(config: BotConfig) -> tuple[str, ...]:
    names = list(config.group_names)
    seen = set(names)
    for targets in config.reply_groups.values():
        for group_name in targets:
            if group_name not in seen:
                names.append(group_name)
                seen.add(group_name)
    return tuple(names)


def build_handler(config: BotConfig, history: HistoryStore) -> KJFWDHandler:
    capabilities = CapabilityRegistry.from_skill_directory(config.skills_path)
    logger.info("已加载 skills: %s", ", ".join(capabilities.names) or "无")
    llm_client = OpenAIChatClient(config.llm)
    tools = []
    if config.search.enabled:
        tools.append(WebSearchTool(BraveSearchClient(config.search)))
    agent = ToolCallingAgent(
        llm_client,
        tools,
        max_tool_rounds=config.search.max_tool_rounds,
    )
    return KJFWDHandler(
        groups=config.group_names,
        bot_nicknames=config.group_nicknames,
        listen_modes=config.listen_modes,
        reply_groups=config.reply_groups,
        history=history,
        model=agent,
        router=ConversationRouter(llm_client),
        classifier=LLMQuestionClassifier(llm_client),
        prompt_builder=PromptBuilder(
            config.system_prompt_path,
            capabilities,
            image_context=config.image_context,
        ),
        max_messages=config.history.max_messages,
        max_characters=config.history.max_characters,
        trigger_dedupe_seconds=config.history.trigger_dedupe_seconds,
        queue_size_per_group=config.queue_size_per_group,
        conversation_pool=config.conversation_pool,
        reply_debounce=config.reply_debounce,
        message_reminder=config.message_reminder,
        image_context=config.image_context,
        show_conversation_id=config.debug.conversation_id_in_reply,
    )


class BowxtTransport:
    """Map transport-neutral actions to bowxt's durable HTTP Agent API."""

    def __init__(self, client: Any, config: BotConfig):
        self.client = client
        self.config = config
        self.chats: dict[str, Any] = {}

    def prepare(self) -> tuple[int, ...]:
        from bowxt import ChatType

        for name in process_group_names(self.config):
            self.chats[name] = self.client.ensure_chat(name, ChatType.GROUP)
        return tuple(self.chats[name].id for name in self.config.group_names)

    def emit(self, action: ReplyAction | ForwardAction) -> None:
        if isinstance(action, ReplyAction):
            target = action.group
        elif isinstance(action, ForwardAction):
            target = action.target_name
        else:
            raise TypeError(f"unsupported action: {type(action).__name__}")
        chat = self.chats.get(target)
        if chat is None:
            from bowxt import ChatType

            chat = self.client.ensure_chat(target, ChatType.GROUP)
            self.chats[target] = chat
        queued = self.client.send_text(
            chat,
            action.content,
            client_id=action.client_id,
        )
        delivered = self.client.wait_delivery(
            queued, timeout=self.config.bowxt.send_timeout_seconds
        )
        if delivered.delivery_status == "failed":
            raise RuntimeError(delivered.delivery_error or "bowxt send failed")
        self._log(
            "info",
            "微信回复发送完成",
            event="reply_delivered",
            context={
                "outgoing_seq": delivered.seq,
                "chat": delivered.chat,
                "status": delivered.delivery_status,
            },
        )

    def event_from_delivery(self, delivery: Any) -> MessageEvent:
        message = delivery.message
        image_path = self._cache_image(delivery)
        nickname = self.config.group_nicknames.get(message.chat, "")
        return MessageEvent(
            group=message.chat,
            content=message.content,
            timestamp=_timestamp(message.timestamp or message.observed_at),
            group_nickname=nickname,
            is_at_me=message.is_at_me,
            sender=message.sender,
            message_type=message.message_type,
            image_path=str(image_path) if image_path else None,
            image_mime_type=message.image_mime_type,
            image_sha256=message.image_sha256,
            source_key=f"bowxt:{message.seq}",
        )

    def _cache_image(self, delivery: Any) -> Path | None:
        message = delivery.message
        if message.message_type != "image" or not self.config.image_context.enabled:
            return None
        if not message.image_url:
            if delivery.attempt <= 6:
                raise RuntimeError("图片仍在 bowxt 可见界面抓取队列中")
            self._log(
                "warning",
                "图片抓取超时，按文本占位继续处理",
                event="image_capture_timeout",
                context={"message_seq": message.seq, "attempt": delivery.attempt},
            )
            return None
        data = self.client.download_image(message)
        if len(data) > self.config.image_context.max_image_bytes:
            raise ValueError(
                f"图片超过 image_context.max_image_bytes: {len(data)}"
            )
        digest = hashlib.sha256(data).hexdigest()
        if message.image_sha256 and digest != message.image_sha256:
            raise ValueError("bowxt 图片 SHA-256 校验失败")
        cache = self.config.image_context.cache_path
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"{digest}.png"
        if not path.exists():
            path.write_bytes(data)
        return path

    def _log(self, level: str, message: str, **kwargs: Any) -> None:
        try:
            self.client.log(level, message, **kwargs)
        except Exception:
            logger.debug("写入 bowxt Agent 日志失败", exc_info=True)


def run(config_path: Path, env_path: Path, *, stop_event: threading.Event | None = None) -> None:
    try:
        from bowxt import AgentClient
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            "未安装 bowxt 0.4+；请先执行 python -m pip install -e /path/to/bowxt"
        ) from exc

    config = load_config(config_path, env_path=env_path)
    history = HistoryStore(config.history.database_path, config.history.idle_timeout_seconds)
    history.prune(config.history.retention_days)
    handler = build_handler(config, history)
    client = AgentClient(config.bowxt.consumer, base_url=config.bowxt.base_url)
    transport = BowxtTransport(client, config)
    chat_ids = transport.prepare()
    handler.set_action_emitter(transport.emit)
    stopping = stop_event or threading.Event()
    logger.info(
        "kjfwd-bot 已连接 bowxt：consumer=%s groups=%s image_context=%s",
        config.bowxt.consumer,
        ",".join(config.group_names),
        config.image_context.enabled,
    )
    transport._log(
        "info",
        "kjfwd-bot 启动",
        event="agent_started",
        context={
            "groups": list(config.group_names),
            "image_context": config.image_context.enabled,
        },
    )
    try:
        while not stopping.is_set():
            try:
                deliveries = client.claim(
                    chat_ids=chat_ids,
                    limit=config.bowxt.batch_size,
                    lease_seconds=config.bowxt.lease_seconds,
                    timeout=config.bowxt.claim_timeout_seconds,
                    require_sender=config.bowxt.require_sender,
                    replay_existing=config.bowxt.replay_existing,
                )
            except Exception as exc:
                logger.warning("领取 bowxt 消息失败：%s", exc)
                if stopping.wait(1.0):
                    break
                continue
            for delivery in deliveries:
                if stopping.is_set():
                    client.nack(delivery, "agent is stopping", retry_delay=1)
                    break
                try:
                    event = transport.event_from_delivery(delivery)
                    handler.handle(event)
                except Exception as exc:
                    client.nack(delivery, exc, retry_delay=5)
                    transport._log(
                        "error",
                        str(exc),
                        event="message_failed",
                        context={
                            "message_seq": delivery.message.seq,
                            "attempt": delivery.attempt,
                        },
                    )
                else:
                    client.ack(delivery)
                    transport._log(
                        "info",
                        "消息已进入 Agent 处理链路",
                        event="message_accepted",
                        context={
                            "message_seq": delivery.message.seq,
                            "chat": delivery.message.chat,
                            "sender": delivery.message.sender,
                            "message_type": delivery.message.message_type,
                        },
                    )
    finally:
        handler.stop()
        history.close()


def _timestamp(value: str) -> float:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
