"""interaction / builtin tool capabilities."""

from __future__ import annotations

import json
import re
from typing import Any
from sheetmate.agent.tools.contracts import AgentContext, ToolResult

_RECOMMENDED_TAG_RE = re.compile(
    r"^(?:\s*[\[(【（]\s*(?:默认)?(?:推荐|recommended)\s*[\])】）]\s*)+"
    r"|(?:\s*[\[(【（]\s*(?:默认)?(?:推荐|recommended)\s*[\])】）]\s*)+$",
    re.IGNORECASE,
)


def _normalize_clarify_options(raw_options: Any, recommended_index: Any = None) -> tuple[list[str], int | None]:
    options = [str(o).strip() for o in (raw_options or []) if str(o).strip()][:4]
    if not options:
        return [], None
    rec_idx: int | None = None
    if isinstance(recommended_index, int) and not isinstance(recommended_index, bool) and 0 <= recommended_index < len(options):
        rec_idx = recommended_index
    else:
        for idx, text in enumerate(options):
            if _RECOMMENDED_TAG_RE.search(text):
                rec_idx = idx
                break
    if rec_idx is None:
        rec_idx = 0
    cleaned = [(_RECOMMENDED_TAG_RE.sub("", text).strip() or text) for text in options]
    if rec_idx > 0:
        cleaned.insert(0, cleaned.pop(rec_idx))
    return cleaned, 0


def _tool_ask_user(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    question = str(args.get("question") or "").strip()
    options, recommended = _normalize_clarify_options(args.get("options"), args.get("recommended_index"))
    event: dict[str, Any] = {"type": "clarify", "question": question, "options": options}
    if recommended is not None:
        event["recommended"] = recommended
    return ToolResult("已向用户提问，等待用户回答后再继续。", summary=question,
                      events=[event], end_turn=True)


def _tool_inspect_evidence(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    from sheetmate.core.security.payload import sanitize_tool_payload

    section = str(args.get("section") or "text")
    if section not in {"text", "args", "facts"}:
        raise ValueError("执行证据只支持 text、args 或 facts。")
    check_scope = getattr(ctx.host, "assert_live_context_scope", None)
    if callable(check_scope):
        check_scope()
    item = ctx.evidence_store.get(str(args["evidence_id"]))
    if item is None:
        resolver = getattr(ctx.host, "resolve_execution_evidence", None)
        if callable(resolver):
            item = resolver(str(args["evidence_id"]))
            if item is not None:
                ctx.evidence_store[str(args["evidence_id"])] = item
    if item is None or (ctx.branch_id and item.get("facts", {}).get("branch_id") != ctx.branch_id):
        raise ValueError("当前范围没有这条执行证据，请使用本任务实际返回的证据 ID。")
    safe, sanitized_fields = sanitize_tool_payload({
        "text": item.get("text"), "args": item.get("args", {}),
        "facts": item.get("facts"), "summary": item.get("summary"),
    })
    text = (str(safe.get("text") or "") if section == "text"
            else json.dumps(safe[section], ensure_ascii=False))
    offset = max(0, int(args.get("offset") or 0))
    limit = min(8000, max(100, int(args.get("limit") or 4000)))
    payload = {"evidence_id": args["evidence_id"], "section": section, "summary": safe.get("summary"),
               "text": text[offset:offset + limit], "total_chars": len(text), "offset": offset,
               "next_offset": offset + limit if offset + limit < len(text) else None}
    if section == "text":
        payload["facts"] = safe.get("facts")
    elif section == "args":
        payload["source_hint"] = "若参数以 script_id 引用代码，请用 section=text 取得 run_id，再调用 inspect_python_script 查看完整代码。"
    if sanitized_fields:
        payload["sanitized_fields"] = sanitized_fields
    return ToolResult(json.dumps(payload, ensure_ascii=False), summary="已回查原始执行证据")
