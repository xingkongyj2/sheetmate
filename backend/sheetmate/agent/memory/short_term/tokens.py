"""Pure token estimates and bounded text projections for short-term memory."""
from __future__ import annotations

from typing import Any

def estimate_tokens(text: str) -> int:
    """Conservative local estimate for mixed Chinese/ASCII; no provider tokenizer required."""
    value = str(text or "")
    non_ascii = sum(ord(char) > 127 for char in value)
    return non_ascii + (len(value) - non_ascii + 3) // 4


def history_tokens(history: list[dict[str, Any]]) -> int:
    return sum(estimate_tokens(str(item.get("content") or "")) + 8 for item in history)


def fit_text(text: str, tokens: int) -> str:
    """Bound a temporary projection, explicitly retaining both ends of long messages."""
    value = str(text or "")
    if estimate_tokens(value) <= tokens:
        return value
    marker = "\n（本条中间内容因上下文预算省略，原文仍保留；勿推断省略部分。）\n"
    available = max(0, tokens - estimate_tokens(marker))
    low, high = 0, len(value)
    while low < high:
        size = (low + high + 1) // 2
        head = (size + 1) // 2
        candidate = value[:head] + (value[-(size - head):] if size > head else "")
        if estimate_tokens(candidate) <= available:
            low = size
        else:
            high = size - 1
    head = (low + 1) // 2
    return value[:head] + marker + (value[-(low - head):] if low > head else "")

