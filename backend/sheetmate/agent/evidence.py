"""Compact, versioned execution evidence independent of LLM prose."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from sheetmate.engine.sqlutil import sql_table_refs
from sheetmate.agent.tool_results import _preview_value, result_reference


def _source(info: Any) -> dict[str, Any]:
    quality = dict(getattr(info, "read_quality", None) or {})
    return {"table": info.name, "revision": info.revision, "rows": info.rows,
            "file_id": info.file_id, "file_version": info.file_version,
            "full_data": not any(quality.get(key) for key in ("truncated", "missing_formula_cache", "error_cells", "formula_error_cells")), "read_quality": quality}


def snapshot_inputs(ctx: Any, tool: str, args: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Capture input versions before a tool can replace any input table."""
    args = args or {}
    names: list[str] = []
    for key in ("table", "tables", "input_tables", "left", "right", "source_table", "lookup_table"):
        value = args.get(key)
        names.extend([value] if isinstance(value, str) else [name for name in value if isinstance(name, str)]
                     if isinstance(value, list) else [])
    if isinstance(args.get("sql"), str):
        names.extend(sql_table_refs(args["sql"]))
    if tool == "run_python_script" and args.get("input_tables") is None:
        names.extend(table.name for table in ctx.visible_tables())
    result = []
    for name in dict.fromkeys(names):
        if ctx.table_filter is not None and name not in ctx.table_filter:
            continue
        try:
            result.append(_source(ctx.ws.require(name)))
        except ValueError:
            continue  # Tool validation reports missing or inaccessible inputs.
    return result


def tool_evidence(ctx: Any, tool: str, args: dict[str, Any] | None, result: Any, *,
                  call_id: str, sources: list[dict[str, Any]], executed: bool, reused: bool = False) -> dict[str, Any]:
    canonical = json.dumps(args or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    artifacts: list[dict[str, Any]] = []
    row_counts: list[dict[str, Any]] = []
    for table in result.tables:
        name = table.get("name")
        rows = table.get("rows")
        row_counts.append({"table": name or table.get("label"), "rows": rows if isinstance(rows, int) else None})
        if name:
            try:
                artifacts.append({"type": "table", **_source(ctx.ws.require(name))})
            except ValueError:
                pass
    for event in result.events:
        if event.get("type") == "file" and event.get("file_id"):
            artifacts.append({key: event[key] for key in ("type", "file_id", "filename") if key in event})
    output_id = result_reference(getattr(result, "text", ""))
    return {"id": "ev_" + uuid4().hex, "call_id": call_id, "tool": tool,
            "status": "success" if result.ok else "failed", "executed": executed, "reused": reused,
            "source_tables": sources, "full_data": all(source["full_data"] for source in sources) if sources else None,
            "query_hash": hashlib.sha256(canonical.encode()).hexdigest(), "row_counts": row_counts,
            "artifacts": artifacts, "branch_id": getattr(ctx, "branch_id", None),
            **({"tool_result_id": output_id} if output_id else {})}


def evidence_projection(value: dict[str, Any] | None) -> dict[str, Any]:
    """Bound audit/context size while retaining evidence identity and versions."""
    if not value:
        return {}
    return {**{key: value.get(key) for key in ("id", "call_id", "status", "executed", "reused", "reused_from", "full_data", "query_hash", "tool_result_id")},
            "source_tables": [_preview_value(item, chars=300, items=16) for item in (value.get("source_tables") or [])[:16]],
            "omitted_sources": max(0, len(value.get("source_tables") or []) - 16),
            "row_counts": [_preview_value(item, chars=300) for item in (value.get("row_counts") or [])[:16]],
            "artifacts": [_preview_value(item, chars=300) for item in (value.get("artifacts") or [])[:16]]}
