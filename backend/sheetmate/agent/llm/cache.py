"""Deterministic prepared requests and provider-reported prompt cache usage.

These fingerprints describe the application's request, not a provider's opaque
cache key. No response cache, provider breakpoint or monetary saving is inferred.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _ordered(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _ordered(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_ordered(item) for item in value]
    return value


def _serialized(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def stable_request(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Copy and order JSON keys and the unordered tool catalog, preserving data.

    Message order, content, tool argument strings and all schema arrays retain
    their exact values. The returned tree owns its lists/dicts so later provider
    preparation cannot modify a durable history or a shared tool definition.
    """
    result = _ordered(kwargs)
    if isinstance(result.get("tools"), list):
        result["tools"].sort(key=lambda item: (
            str(item.get("type", "")), str((item.get("function") or {}).get("name", "")),
            _serialized(item),
        ))
    return result


def request_prefix_profile(
    kwargs: dict[str, Any], previous: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Hash the actual prepared request without retaining any prompt text."""
    def fingerprint(value: Any) -> str:
        digest = hashlib.sha256()
        encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), default=str)
        for fragment in encoder.iterencode(value):
            digest.update(fragment.encode("utf-8"))
        return digest.hexdigest()

    messages = kwargs.get("messages") or []
    leading_system = []
    for message in messages:
        if message.get("role") not in {"system", "developer"}:
            break
        leading_system.append(message)
    controls = {key: value for key, value in kwargs.items() if key not in {"messages", "tools"}}
    snapshot = {
        "request_sha256": fingerprint(kwargs),
        "system_sha256": fingerprint(leading_system),
        "tools_sha256": fingerprint(kwargs.get("tools")),
        "controls_sha256": fingerprint(controls),
        "message_sha256": [fingerprint(item) for item in messages],
    }
    common = 0
    changed = []
    relation = "initial"
    first_changed = None
    if previous is not None:
        old, new = previous["message_sha256"], snapshot["message_sha256"]
        for before, after in zip(old, new):
            if before != after:
                break
            common += 1
        changed = [name for name in ("system", "tools", "controls")
                   if previous[f"{name}_sha256"] != snapshot[f"{name}_sha256"]]
        if old != new:
            changed.append("messages")
            if common < len(old):
                first_changed = common
        relation = ("identical" if snapshot["request_sha256"] == previous["request_sha256"] else
                    "append_only" if common == len(old) and len(new) > len(old)
                    and not any(name in changed for name in ("tools", "controls")) else "rewritten")
    profile = {key: value for key, value in snapshot.items() if key != "message_sha256"}
    profile.update({
        "representation": "prepared_request_json_utf8; provider cache key is not observable",
        "message_count": len(messages), "relation": relation,
        "common_message_prefix_count": common, "first_changed_message_index": first_changed,
        "changed_sections": changed,
    })
    return profile, snapshot


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _tokens(value: Any) -> int | None:
    # Missing and malformed fields are not evidence of a zero-token cache hit.
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def reported_cache_usage(usage: Any) -> dict[str, Any]:
    """Use one reported cache-read field, without double counting aliases."""
    cached = _tokens(_field(_field(usage, "prompt_tokens_details"), "cached_tokens"))
    cached_source = "prompt_tokens_details.cached_tokens" if cached is not None else None
    if cached is None:
        cached = _tokens(_field(usage, "prompt_cache_hit_tokens"))
        cached_source = "prompt_cache_hit_tokens" if cached is not None else None
    miss = _tokens(_field(usage, "prompt_cache_miss_tokens"))
    miss_source = "prompt_cache_miss_tokens" if miss is not None else None
    prompt = _tokens(_field(usage, "prompt_tokens"))
    if miss is None and cached is not None and prompt is not None and cached <= prompt:
        miss = prompt - cached
        miss_source = "prompt_tokens-cached_tokens"
    return {
        "cached_tokens": cached or 0, "cache_miss_tokens": miss or 0,
        "cached_tokens_available": cached is not None,
        "cache_miss_tokens_available": miss is not None,
        "cached_tokens_source": cached_source, "cache_miss_tokens_source": miss_source,
    }
