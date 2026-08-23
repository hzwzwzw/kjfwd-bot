from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator


_CURRENT_GROUP: ContextVar[str | None] = ContextVar("kjfwd_current_group", default=None)


@contextmanager
def current_tool_group(group_name: str) -> Iterator[None]:
    token = _CURRENT_GROUP.set(str(group_name))
    try:
        yield
    finally:
        _CURRENT_GROUP.reset(token)


def require_current_group() -> str:
    group_name = _CURRENT_GROUP.get()
    if not group_name:
        raise RuntimeError("get_history 只能在处理群聊消息时调用")
    return group_name
