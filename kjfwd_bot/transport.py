"""Transport-neutral message types used by the migrated Agent.

The old implementation imported these shapes from wx4py.  Keeping the small
action/event surface lets the business handler stay largely unchanged while
bowxt owns all WeChat UI access in a separate process.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class MessageEvent:
    group: str
    content: str
    timestamp: float
    group_nickname: str = ""
    is_at_me: bool = False
    raw: Any = None
    sender: Optional[str] = None
    message_type: str = "text"
    image_path: Optional[str] = None
    image_mime_type: Optional[str] = None
    image_sha256: Optional[str] = None
    source_key: Optional[str] = None
    sender_organization: Optional[str] = None


@dataclass(frozen=True)
class ReplyAction:
    group: str
    content: str
    client_id: Optional[str] = None


@dataclass(frozen=True)
class ForwardAction:
    target_name: str
    target_type: str
    content: str
    source_group: str = ""
    client_id: Optional[str] = None


class MessageHandler:
    """Minimal compatibility protocol for the original handler."""

    def set_action_emitter(self, emit_action) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def handle(self, event: MessageEvent):  # pragma: no cover - interface
        raise NotImplementedError
