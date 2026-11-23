"""Compact, versioned execution evidence independent of LLM prose."""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4

from sheetmate.engine.tables.sql import sql_table_refs
from sheetmate.agent.tools.results import _preview_value, result_reference


_WORKFLOW_TOOLS = frozenset({"build_recipe", "revise_recipe", "run_recipe"})


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
    if tool in _WORKFLOW_TOOLS:
        mapping = args.get("inputs") or args.get("input_mapping")
        if isinstance(mapping, dict):
            names.extend(value for value in mapping.values() if isinstance(value, str))
        if not names:
            # 流程类工具不写 input_tables 时默认处理所有上传的输入表；证据里也要记下它们的版本和完整性，
            # 否则这类成功的修改永远是“完整性未确认”，完成核对会反复拒绝一个其实已经完成的任务。
            names.extend(table.name for table in ctx.visible_tables()
                         if getattr(table, "source", "") == "uploaded" and getattr(table, "role", "") == "input")
    result = []
    for name in dict.fromkeys(names):
        if ctx.table_filter is not None and name not in ctx.table_filter:
            continue
        try:
            result.append(_source(ctx.ws.require(name)))
        except ValueError:
            continue  # Tool validation reports missing or inaccessible inputs.
    # 纯文件脚本或流程（如 run_python_script(input_tables=[], input_files={...})）直接读取完整原文件，
    # 必须把输入文件记入证据，避免 full_data=None 导致完成核对误杀已完成的结果。
    input_files = args.get("input_files")
    if isinstance(input_files, dict) and input_files:
        seen_file_ids = {item.get("file_id") for item in result if item.get("file_id")}
        for alias, file_id in input_files.items():
            if not isinstance(file_id, str) or not file_id or file_id in seen_file_ids:
                continue
            seen_file_ids.add(file_id)
            result.append({"table": str(alias), "revision": file_id, "rows": None,
                           "file_id": file_id, "file_version": None, "full_data": True,
                           "read_quality": {"source_kind": "input_file"}})
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
