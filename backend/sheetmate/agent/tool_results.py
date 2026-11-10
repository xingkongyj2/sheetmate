"""Two-level tool-result budgets with recoverable durable overflow references."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sheetmate.services.tool_results import canonical, sanitize_tool_payload
from sheetmate.services.errors import Forbidden, NotFound, Unauthorized

SELF_BOUNDED_TOOLS = frozenset({"read_tool_result", "inspect_conversation_history"})
_REFERENCE = re.compile(r"\[tool_result_id=(tr_[a-f0-9]{64})\]")
MAX_PROJECTED_CHARS = 14_000


@dataclass
class ProjectionBudget:
    max_result_chars: int = 50_000
    max_turn_chars: int = 200_000
    preview_chars: int = 2000
    used_chars: int = 0
    min_spill_chars: int = 5000
    failures: list[str] = field(default_factory=list)


def result_reference(text: str) -> str | None:
    match = _REFERENCE.search(text or "")
    return match.group(1) if match else None


def plan_tool_result_spills(payloads: list[dict[str, Any]], *, budget: ProjectionBudget,
                            tool_names: list[str] | None = None) -> set[int]:
    """Choose largest results first when a complete tool batch is available."""
    names = tool_names or [""] * len(payloads)
    sizes = [len(canonical(payload)) for payload in payloads]
    selected = {i for i, size in enumerate(sizes)
                if size > budget.max_result_chars and names[i] not in SELF_BOUNDED_TOOLS}
    # A bounded envelope is an estimate; the turn budget remains a projection
    # target, not permission to discard a small result or fail a running task.
    total = budget.used_chars + sum(min(size, budget.preview_chars + 2500) if i in selected else size
                                    for i, size in enumerate(sizes))
    for index in sorted(range(len(payloads)), key=sizes.__getitem__, reverse=True):
        if total <= budget.max_turn_chars:
            break
        if index in selected or sizes[index] < budget.min_spill_chars or names[index] in SELF_BOUNDED_TOOLS:
            continue
        selected.add(index)
        total -= max(0, sizes[index] - (budget.preview_chars + 2500))
    return selected


def _preview_value(value: Any, *, chars: int = 160, items: int = 8, depth: int = 0) -> Any:
    if isinstance(value, str):
        return value if len(value) <= chars else value[:chars] + f" [另有{len(value) - chars}字符，见完整引用]"
    if depth >= 4 and isinstance(value, (dict, list)):
        return "[结构详情见完整引用]"
    if isinstance(value, list):
        result = [_preview_value(child, chars=chars, items=items, depth=depth + 1) for child in value[:items]]
        if len(value) > items:
            result.append({"omitted_items": len(value) - items})
        return result
    if isinstance(value, dict):
        result = {(key if len(key) <= chars else key[:chars] + f" [字段{i}省略尾部]"):
                  _preview_value(child, chars=chars, items=items, depth=depth + 1)
                  for i, (key, child) in enumerate(list(value.items())[:items])}
        if len(value) > items:
            result["omitted_fields"] = len(value) - items
        return result
    return value


def _project_tables(value: Any) -> list[dict[str, Any]]:
    """Keep table-preview types valid while explicitly bounding rows/columns."""
    projected = []
    for table in (value or [])[:8]:
        if not isinstance(table, dict):
            continue
        item = _preview_value(table)
        columns = table.get("columns")
        if isinstance(columns, list):
            pairs = [(column, column[:160]) for column in columns[:8] if isinstance(column, str)]
            item["columns"] = [short for _original, short in pairs]
            item["omitted_columns"] = max(0, len(columns) - len(pairs))
            preview = []
            for row in (table.get("preview") or [])[:5]:
                if isinstance(row, dict):
                    preview.append({short: _preview_value(row.get(original), items=2)
                                    for original, short in pairs})
                elif isinstance(row, list):
                    preview.append([_preview_value(cell, items=2) for cell in row[:len(pairs)]])
            item["preview"] = preview
            item["omitted_preview_rows"] = max(0, len(table.get("preview") or []) - len(preview))
        projected.append(item)
    return projected


def _project(payload: dict[str, Any], reference: dict[str, Any], preview_chars: int) -> dict[str, Any]:
    text = str(payload.get("text") or "")
    result_id = reference["result_id"]
    notice = (f"[tool_result_id={result_id}]\n"
              f"完整安全结果已保存（{reference['total_chars']}字符，sha256={reference['sha256']}）。"
              f"以下是预览，不能推断省略的数据；使用 read_tool_result(result_id='{result_id}', "
              "section='text', offset=0, limit=4000) 分页读取。tables/events/payload 分区按 JSON 文本分页。")
    if reference.get("sanitized_fields"):
        notice += f"已明确移除{reference['sanitized_fields']}处凭据、原始推理或内联二进制内容。"
    allowed = {"text", "ok", "summary", "tables", "events", "end_turn", "public_text", "public_summary",
               "public_details", "error_kind", "error_details"}
    projected = {key: value for key, value in payload.items() if key in allowed}
    projected["text"] = notice + "\n" + text[:max(0, preview_chars)]
    for key in ("summary", "public_text", "public_summary"):
        value = projected.get(key)
        if isinstance(value, str):
            projected[key] = _preview_value(value, chars=min(1000, max(0, preview_chars)))
    if isinstance(projected.get("error_kind"), str):
        projected["error_kind"] = _preview_value(projected["error_kind"], chars=160)
    projected["tables"] = _project_tables(payload.get("tables"))
    for key in ("public_details", "error_details"):
        value = projected.get(key)
        if value is not None:
            projected[key] = _preview_value(value)
    # Keep every delivery/control event and its identity. Rich previews/flows
    # may be inspected from storage rather than copied into every checkpoint.
    projected["events"] = [
        {key: (value if key in {"file_id", "id", "run_id", "recipe_id", "type", "delivery_status"}
               else _preview_value(value, chars=500, items=4)) for key, value in event.items()}
        for event in (payload.get("events") or []) if isinstance(event, dict)
    ]
    if len(canonical(projected)) > MAX_PROJECTED_CHARS:
        projected["tables"] = [
            {key: _preview_value(value) for key, value in table.items() if key in {"name", "label", "rows", "file_id"}}
            for table in (payload.get("tables") or [])[:16] if isinstance(table, dict)
        ]
        for key in ("public_details", "error_details"):
            if projected.get(key) is not None:
                projected[key] = [{"notice": "完整结构已外溢，请分页读取工具结果引用。"}]
        projected["events"] = [
            {key: value for key, value in event.items()
             if key in {"type", "id", "file_id", "run_id", "recipe_id", "filename", "delivery_status"}}
            for event in projected["events"]
        ]
    if len(canonical(projected)) > MAX_PROJECTED_CHARS:
        # Some tools can produce thousands of delivery cards or an arbitrarily
        # long field name. The last boundary must be a total budget, not just
        # a collection of per-field caps. The complete events remain readable
        # from the durable output; callers emit operational events separately.
        projected = {
            "text": notice + "\n" + text[:min(2000, max(0, preview_chars))],
            "ok": payload.get("ok", True) is not False,
            "end_turn": bool(payload.get("end_turn", False)),
            "summary": str(payload.get("summary") or "")[:500],
            "public_text": (str(payload["public_text"])[:500] if payload.get("public_text") is not None else None),
            "public_summary": (str(payload["public_summary"])[:500] if payload.get("public_summary") is not None else None),
            "tables": [], "events": [],
            "public_details": [{"notice": "仅展示有界预览；完整表格和事件请通过工具结果引用分页读取。"}],
            "error_kind": (str(payload["error_kind"])[:100] if payload.get("error_kind") is not None else None),
            "error_details": ([{"notice": "完整错误详情请通过工具结果引用分页读取。"}]
                              if payload.get("error_details") is not None else None),
        }
        tables = [{key: _preview_value(value, chars=120, items=2)
                   for key, value in table.items() if key in {"name", "label", "rows", "file_id"}}
                  for table in (payload.get("tables") or [])[:8] if isinstance(table, dict)]
        event_keys = {"type", "id", "file_id", "run_id", "recipe_id", "filename", "delivery_status", "explicit",
                      "question", "options", "customer_id", "conversation_id", "download_url"}
        events = [event for event in (payload.get("events") or []) if isinstance(event, dict)]
        # Clarifications carry task control; preserve them before presentation
        # events when the number of events itself exceeds the context budget.
        ordered = [event for event in events if event.get("type") == "clarify"][:4]
        ordered.extend(event for event in events if event.get("type") != "clarify")
        for event in ordered[:24]:
            candidate = {key: _preview_value(value, chars=300, items=4)
                         for key, value in event.items() if key in event_keys}
            trial = {**projected, "events": [*projected["events"], candidate]}
            if len(canonical(trial)) <= MAX_PROJECTED_CHARS - 1000:
                projected["events"].append(candidate)
        for table in tables:
            trial = {**projected, "tables": [*projected["tables"], table]}
            if len(canonical(trial)) <= MAX_PROJECTED_CHARS - 1000:
                projected["tables"].append(table)
        omitted_events = len(events) - len(projected["events"])
        projected["public_details"][0]["omitted_events"] = omitted_events
        projected["public_details"][0]["omitted_tables"] = len(payload.get("tables") or []) - len(projected["tables"])
    if len(canonical(projected)) > MAX_PROJECTED_CHARS:
        # JSON escaping can multiply a short string's representation sixfold.
        # Preserve the locator even when preview text consists of control bytes.
        projected["text"] = notice
        for key in ("summary", "public_text", "public_summary"):
            if isinstance(projected.get(key), str):
                projected[key] = projected[key][:100]
    return projected


def project_tool_result(ctx: Any, tool_name: str, call_id: str, payload: dict[str, Any], *,
                        budget: ProjectionBudget, execution_id: str | None = None,
                        force_spill: bool = False) -> dict[str, Any]:
    """Return a ToolResult-compatible payload; never invent a failed-save ref.

    The durable payload is the source of truth. Receipts, evidence and model
    messages should all use this returned projection to avoid reinjecting the
    large source on checkpoint recovery.
    """
    safe, _ = sanitize_tool_payload(payload)
    size = len(canonical(safe))
    already_projected = size <= MAX_PROJECTED_CHARS and result_reference(str(safe.get("text") or "")) is not None
    spill = (tool_name not in SELF_BOUNDED_TOOLS and not already_projected and
             (force_spill or size > budget.max_result_chars or
              (size >= budget.min_spill_chars and budget.used_chars + size > budget.max_turn_chars)))
    result = safe
    if spill:
        try:
            reference = ctx.host.persist_tool_result(
                tool_name=tool_name, call_id=call_id, payload=payload,
                execution_id=execution_id, branch_id=getattr(ctx, "branch_id", None),
            )
            if (not isinstance(reference, dict) or not re.fullmatch(r"tr_[a-f0-9]{64}", str(reference.get("result_id")))
                    or not isinstance(reference.get("total_chars"), int)
                    or not re.fullmatch(r"[a-f0-9]{64}", str(reference.get("sha256")))):
                raise ValueError("invalid durable result reference")
        except (Forbidden, Unauthorized, NotFound):
            # A revoked scope is an authorization failure, not a soft storage
            # outage. Do not pass the now-unauthorized source back to a model.
            raise
        except Exception as exc:
            # A soft context budget never authorizes destroying evidence. Keep
            # the complete safe payload when persistence is unavailable.
            budget.failures.append(type(exc).__name__)
        else:
            result = _project(safe, reference, budget.preview_chars)
    budget.used_chars += len(canonical(result))
    return result
