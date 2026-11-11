"""Source-backed task requirements, independent of the chat summary window.

The database stores message references, never model-authored replacements for
the user's requirements. Prompt projections may be short; omitted text remains
available through inspect_task_context and is explicitly identified.
"""

from __future__ import annotations

import json
from typing import Any

TASK_CONTEXT_CHARS = 10_000


def advance_task(state: dict[str, Any] | None, message_id: int, *, relation: str = "continue",
                 file_ids: list[str] | None = None) -> dict[str, Any]:
    previous = state or {}
    ids = [] if relation == "new" else list(previous.get("source_message_ids") or [])
    ids = list(dict.fromkeys([*ids, message_id]))
    return {
        "schema_version": 1,
        "task_id": f"task_{ids[0]}",
        "source_message_ids": ids,
        "current_message_id": message_id,
        "file_ids": list(dict.fromkeys([*([] if relation == "new" else previous.get("file_ids") or []), *(file_ids or [])])),
        "status": "running",
        "evidence_ids": [] if relation == "new" else list(previous.get("evidence_ids") or []),
        "unresolved": [] if relation == "new" else list(previous.get("unresolved") or []),
    }


def task_projection(state: dict[str, Any] | None, *, max_chars: int = TASK_CONTEXT_CHARS) -> str:
    """Project the original request and recent amendments, without silent loss."""
    if not state:
        return ""
    requests = list(state.get("requirements") or [])
    if not requests:
        return ""
    max_chars = max(600, max_chars)
    header = (
        "当前任务的用户要求（来源消息原文，后续明确修改优先；本次例外不自动写入长期规则）：\n"
        f"task_id={state.get('task_id', '')}；状态={state.get('status', 'running')}\n"
    )
    selected: dict[int, str] = {}
    used = len(header)
    # The initiating requirement remains an anchor even in a long conversation.
    ordered = [requests[0], *reversed(requests[1:])]
    omitted: list[int] = []
    for item in ordered:
        source_id = int(item["message_id"])
        content = str(item.get("content") or "")
        prefix = f"[用户消息 {source_id}] "
        available = max(0, max_chars - used - 400)
        if available < len(prefix) + 100:
            omitted.append(source_id)
            continue
        if len(prefix) + len(content) > available:
            # Only the first anchor can consume a partial slot. Other complete
            # requirements are referenced for retrieval instead of paraphrased.
            if item is requests[0]:
                room = available - len(prefix) - 80
                head = room // 2
                text = content[:head] + "\n[原文中段未展示，须按消息 ID 查看]\n" + content[-max(1, room - head):]
                selected[source_id] = prefix + text
                used += len(selected[source_id])
                omitted.append(source_id)
            else:
                omitted.append(source_id)
        else:
            selected[source_id] = prefix + content
            used += len(selected[source_id])
    lines = [header, *(selected[int(item["message_id"])] for item in requests if int(item["message_id"]) in selected)]
    if omitted:
        omitted = list(dict.fromkeys(omitted))
        lines.append("未完整展示的要求消息 ID：" + json.dumps(omitted[:30])
                     + (f"（另有 {len(omitted) - 30} 条，请检索）" if len(omitted) > 30 else "")
                     + "。执行涉及这些要求时，先调用 inspect_task_context 按消息 ID 或关键词读取原文；不能把未展示视为没有约束。")
    if state.get("unresolved"):
        lines.append("上次未完成事项：" + json.dumps(state["unresolved"], ensure_ascii=False)[:500])
    return "\n\n".join(lines)


def inspect_task(state: dict[str, Any] | None, *, message_id: int | None = None, query: str = "",
                 offset: int = 0, limit: int = 4000) -> dict[str, Any]:
    """Read exact user text; bounded pages advertise remaining text explicitly."""
    requests = list((state or {}).get("requirements") or [])
    if message_id is not None:
        requests = [item for item in requests if int(item["message_id"]) == message_id]
    query = query.strip().lower()
    if query:
        requests = [item for item in requests if query in str(item.get("content") or "").lower()]
    offset, limit = max(0, offset), max(100, min(limit, 8000))
    if message_id is None:
        page = requests[offset:offset + 12]
        next_offset = offset + len(page)
        return {"task_id": (state or {}).get("task_id"), "matches": [
            {"message_id": item["message_id"], "characters": len(str(item.get("content") or "")),
             "excerpt": str(item.get("content") or "")[:min(limit, 500)]}
            for item in page], "offset": offset, "total_matches": len(requests),
            "next_offset": next_offset if next_offset < len(requests) else None,
            "omitted_matches": max(0, len(requests) - next_offset),
            "instruction": "未指定 message_id 时 offset 按匹配消息条数翻页；指定 message_id 后 offset 按原文字符翻页。excerpt 仅是检索摘要，按 message_id 读取原文。"}
    if not requests:
        return {"message_id": message_id, "found": False, "matches": []}
    content = str(requests[0].get("content") or "")
    return {"message_id": message_id, "found": True, "content": content[offset:offset + limit],
            "offset": offset, "total_characters": len(content),
            "next_offset": offset + limit if offset + limit < len(content) else None,
            "complete": offset == 0 and len(content) <= limit}
