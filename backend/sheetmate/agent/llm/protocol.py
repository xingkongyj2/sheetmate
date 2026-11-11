"""Agent 所需的模型接口；兼容客户端、私有模型和测试替身。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol


class LanguageModel(Protocol):
    """只约定消息与结构化输出能力，不绑定模型提供商或 SDK。"""

    async def chat(self, messages: list[dict[str, Any]],
                   tools: list[dict[str, Any]] | None = None, **extra: Any) -> Any: ...

    def stream_chat(self, messages: list[dict[str, Any]],
                    tools: list[dict[str, Any]] | None = None, **extra: Any) -> AsyncIterator[Any]: ...

    async def complete_json(self, prompt: str, system: str | None = None,
                            retries: int | None = None, **extra: Any) -> Any: ...
