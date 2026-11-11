"""纯数据净化规则，供 Agent 投影与数据库存储共同使用。"""
from __future__ import annotations

import json
import math
import re
from typing import Any

from sheetmate.core.errors import ServiceError
from sheetmate.core.security.privacy import _BEARER, _IMAGE_DATA, _LABELED_SECRET, _TOKEN

_FORBIDDEN_KEYS = frozenset({
    "apikey", "accesstoken", "refreshtoken", "authorization", "password", "passwordhash",
    "secret", "clientsecret", "privatekey", "reasoning", "reasoningcontent", "reasoningtext", "chainofthought", "thinking",
    "imagedata", "imagebytes", "inlineimage", "b64json", "base64",
})

_FORBIDDEN_SUFFIXES = ("apikey", "accesstoken", "refreshtoken", "clientsecret", "password", "passwordhash",
                       "privatekey", "reasoningcontent", "reasoningtext", "chainofthought", "imagebytes",
                       "imagedata", "base64")


_RAW_REASONING = re.compile(r"<(think|thinking|analysis|reasoning)\b[^>]*>[\s\S]*?(?:</\1\s*>|$)", re.I)


_PRIVATE_KEY_DATA = re.compile(
    r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----[\s\S]*?(?:-----END (?:[A-Z]+ )?PRIVATE KEY-----|$)"
)


_SENSITIVE_ASSIGNMENT = re.compile(
    r"[\"']?(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|"
    r"authorization|password|reasoning(?:[_ -]?(?:content|text))?|chain[_ -]?of[_ -]?thought)"
    r"[\"']?\s*[:=：]\s*([\"'])(?:(?!\1)[^\r\n])*?\1", re.I,
)


_UNQUOTED_PRIVATE = re.compile(
    r"\b(?:password|reasoning(?:[_ -]?(?:content|text))?|chain[_ -]?of[_ -]?thought)"
    r"\s*[:=：]\s*[^\r\n]+", re.I,
)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def sanitize_tool_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """Copy a tool payload with explicit safe replacements, retaining ordinary data."""
    count = 0

    def omitted() -> str:
        nonlocal count
        count += 1
        return "[sensitive tool content removed]"

    def walk(value: Any, depth: int = 0, encoded_depth: int = 0) -> Any:
        if depth > 80:
            raise ServiceError("工具结果嵌套过深，无法保存。")
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if not isinstance(key, str):
                    raise ServiceError("工具结果字段名必须是字符串。")
                normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                if normalized in _FORBIDDEN_KEYS or normalized.endswith(_FORBIDDEN_SUFFIXES):
                    omitted()
                    continue
                result[key] = walk(child, depth + 1, encoded_depth)
            return result
        if isinstance(value, (list, tuple)):
            return [walk(child, depth + 1, encoded_depth) for child in value]
        if isinstance(value, (bytes, bytearray, memoryview)):
            return omitted()
        if isinstance(value, str):
            stripped = value.strip()
            # JSON inside ToolResult.text is common. Sanitize its structure too,
            # without rewriting normal JSON formatting when no removal occurred.
            if encoded_depth < 3 and stripped[:1] in {"{", "[", '"'}:
                try:
                    decoded = json.loads(stripped)
                except (ValueError, RecursionError):
                    pass
                else:
                    previous = count
                    safe = walk(decoded, depth + 1, encoded_depth + 1)
                    if count != previous:
                        value = canonical(safe)
            for pattern in (_PRIVATE_KEY_DATA, _IMAGE_DATA, _RAW_REASONING, _SENSITIVE_ASSIGNMENT, _UNQUOTED_PRIVATE,
                            _LABELED_SECRET, _BEARER, _TOKEN):
                value = pattern.sub(lambda _match: omitted(), value)
            return value
        if value is None or isinstance(value, (bool, int)):
            return value
        if isinstance(value, float) and math.isfinite(value):
            return value
        raise ServiceError("工具结果必须是有效的 JSON。")

    safe = walk(payload)
    if not isinstance(safe, dict):
        raise ServiceError("工具结果必须是 JSON 对象。")
    return safe, count

