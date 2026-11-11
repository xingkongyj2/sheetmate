"""Build multimodal model content without putting image bytes in text history."""

from __future__ import annotations

from typing import Any


def user_content(text: str, image_parts: list[dict[str, Any]] | None = None) -> str | list[dict[str, Any]]:
    if not image_parts:
        return text
    return [{"type": "text", "text": text}, *image_parts]


def history_window(history: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    """保留压缩摘要，再取最近消息；摘要不参与滑动窗口淘汰。"""
    if limit is None:
        # 由服务层统一压缩；执行层不能提前丢弃尚未纳入摘要的消息。
        return list(history)
    summaries = [item for item in history if item.get("context_summary")]
    messages = [item for item in history if not item.get("context_summary")]
    return [*summaries, *(messages[-limit:] if limit > 0 else [])]


def model_history(history: list[dict[str, Any]], limit: int | None = None) -> list[dict[str, Any]]:
    messages = []
    for item in history_window(history, limit):
        content = str(item.get("content") or "")
        message_id = item.get("message_id", item.get("id"))
        if message_id is not None:
            source = "conversation_summary" if item.get("context_summary") else "conversation_message"
            content = f"[history source={source} message_id={message_id}]\n{content}"
        messages.append({"role": item["role"], "content": user_content(content, item.get("image_parts"))})
    return messages
