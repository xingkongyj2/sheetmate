"""Build multimodal model content without putting image bytes in text history."""

from __future__ import annotations

from typing import Any


def user_content(text: str, image_parts: list[dict[str, Any]] | None = None) -> str | list[dict[str, Any]]:
    if not image_parts:
        return text
    return [{"type": "text", "text": text}, *image_parts]


def model_history(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"role": item["role"], "content": user_content(item["content"], item.get("image_parts"))}
        for item in history
    ]
