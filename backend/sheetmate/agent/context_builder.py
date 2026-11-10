"""Bound per-call projections while retaining source-backed requirements.

Only old tool results with a durable evidence reference can be projected here.
User/system instructions, tool arguments and call/result pairing remain intact.
If the remaining request is still too large the gateway refuses it explicitly.
"""
from __future__ import annotations

import re
from typing import Any

from sheetmate.agent.llm_budget import request_size

_EVIDENCE = re.compile(r"^\[evidence_id=(ev_[a-zA-Z0-9_]+)\]\n")


def prepare_context(kwargs: dict[str, Any], max_chars: int) -> dict[str, Any]:
    """Return an independent temporary request; never mutate the evidence log."""
    if not max_chars or request_size(kwargs)[0] <= max_chars * 0.8:
        return kwargs
    messages = kwargs.get("messages") or []
    projected = [dict(item) for item in messages]
    candidates = [index for index, item in enumerate(projected)
                  if item.get("role") == "tool" and isinstance(item.get("content"), str)]
    changed = False
    for index in candidates[:-4]:
        content = projected[index]["content"]
        reference = _EVIDENCE.match(content)
        if not reference or len(content) < 1200:
            continue
        evidence_id = reference.group(1)
        head = content[reference.end():reference.end() + 700]
        projected[index]["content"] = (
            f"[evidence_id={evidence_id}]\n{head}\n"
            "[旧工具结果的详细内容未重复注入；上面仅为摘要，不能据此推断省略数据。"
            f"涉及具体数值或验收时，调用 inspect_execution_evidence(evidence_id='{evidence_id}') 分页查看完整依据。]"
        )
        changed = True
        candidate = {**kwargs, "messages": projected}
        if request_size(candidate)[0] <= max_chars * 0.8:
            break
    return {**kwargs, "messages": projected} if changed else kwargs
