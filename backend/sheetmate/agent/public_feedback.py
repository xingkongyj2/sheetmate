"""Sanitize declared public operation feedback without importing execution tools."""

from __future__ import annotations

import re
from typing import Any


def public_detail(value: Any, limit: int = 1200) -> str:
    """Only the declared business explanation belongs in progress feedback."""
    if not isinstance(value, str):
        return ""
    text = re.sub(r"```[\s\S]*?(?:```|$)", "", value)
    text = re.split(r'Traceback \(most recent call last\):|\n\s*File "', text)[0]
    text = re.sub(r"\b(?:recipe_id|run_id|file_id|tool_call_id|result_call_id)\s*[=:]\s*[A-Za-z0-9_-]+", "", text)
    text = re.sub(r"\b(?:rcp|run|scr|script|call)_[A-Za-z0-9_-]+\b", "", text)
    text = re.sub(r"\b(?:cv|f)_[a-f0-9]{8,}\b", "", text, flags=re.I)
    # Code and raw parameter dictionaries remain in diagnostics, not the public explanation.
    sentences = re.split(r"(?<=[。；\n])", text)
    readable = [part for part in sentences if not re.search(
        r"\b(?:SELECT\b[\s\S]*\bFROM|INSERT\s+INTO|import\s+\w+|def\s+\w+\(|pd\.|np\.|openpyxl\.)|[\{\[]\s*[\"'][^\"']+[\"']\s*:",
        part, flags=re.I,
    )]
    text = re.sub(r"[ \t]+", " ", "".join(readable)).strip()
    return text if len(text) <= limit else text[:limit] + "…"
