"""presentation tool capabilities."""

from __future__ import annotations

import re
from typing import Any
import pandas as pd
from sheetmate.engine.tables.sql import frame_rows, sanitize_table_name
from sheetmate.engine.tables.workspace import TableInfo, Workspace
from sheetmate.agent.tools.contracts import AgentContext
from sheetmate.core.security.payload import canonical, sanitize_tool_payload


MAX_TOOL_TEXT = 6000


PREVIEW_ROWS = 50
OBSERVATION_MAX_CHARS = 8_000
OBSERVATION_MAX_ROWS = 100


RUN_STATUS_LABELS = {"success": "成功", "warning": "完成（有提示）", "failed": "失败", "escalated": "需要人工处理", "running": "运行中"}


def _status_label(status: str) -> str:
    return RUN_STATUS_LABELS.get(status, status)


def table_payload(ws: Workspace, name: str) -> dict[str, Any]:
    info = ws.require(name)
    page = ws.page(name, 0, PREVIEW_ROWS)
    return {
        "name": info.name, "label": info.label, "rows": info.rows, "cols": len(info.columns),
        "columns": info.column_names, "preview": page["rows"], "source": info.source, "role": info.role,
        "file_id": info.file_id, "filename": info.filename, "sheet": info.sheet,
        "description": info.description,
    }


def ephemeral_payload(label: str, df: pd.DataFrame, total: int | None = None) -> dict[str, Any]:
    return {
        "name": None, "label": label, "rows": int(total if total is not None else len(df)), "cols": len(df.columns),
        "columns": [str(c) for c in df.columns], "preview": frame_rows(df, PREVIEW_ROWS), "source": "query",
        "role": "query", "ephemeral": True,
    }


def render_table_observation(ws: Workspace, table: str, *, offset: int = 0,
                             limit: int = OBSERVATION_MAX_ROWS, expected_revision: str | None = None,
                             text_offset: int = 0, max_chars: int = OBSERVATION_MAX_CHARS) -> str:
    """Render exact, bounded observations, with version-pinned continuations.

    Normal pages contain whole rows and intact cells. A single oversized row
    (or column header) is paged as JSON text instead of silently clipping cells.
    ``complete`` describes this observation, never source-table read quality.
    """
    for name, value in (("offset", offset), ("text_offset", text_offset)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} 必须为非负整数。")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= OBSERVATION_MAX_ROWS:
        raise ValueError(f"limit 必须为 1～{OBSERVATION_MAX_ROWS} 的整数。")
    if text_offset and not expected_revision:
        raise ValueError("续读文本片段必须提供上次返回的 expected_revision，不能拼接不同表版本。")
    if max_chars < 1_000:
        raise ValueError("表格观察字符预算至少为 1000。")
    with ws.locked():
        info = ws.require(table)
        if expected_revision is not None and expected_revision != info.revision:
            raise ValueError(
                "表版本已变化，不能继续拼接旧分页。请重新读取并舍弃旧分页："
                + canonical({"tool": "preview_table", "args": {"table": table, "offset": 0, "limit": limit,
                                                               "expected_revision": info.revision}})
            )
        page = ws.page(table, offset, limit)
    safe, removed = sanitize_tool_payload({"columns": [column["name"] for column in page["columns"]],
                                          "rows": page["rows"]})
    rows = safe["rows"]
    total = page["total"]
    base = {"table": table, "revision": info.revision, "offset": offset, "limit": limit,
            "total_rows": total, "complete": False, "completeness_scope": "entire_table_in_this_observation",
            "sanitized_fields": removed}

    def continuation(row_offset: int, fragment_offset: int = 0) -> dict[str, Any]:
        args = {"table": table, "offset": row_offset, "limit": limit, "expected_revision": info.revision}
        if fragment_offset:
            args["text_offset"] = fragment_offset
        return {"tool": "preview_table", "args": args}

    if not text_offset:
        # Read at most the page limit; budget the actual JSON envelope, including
        # escaped newlines, column names, receipts and continuation instructions.
        for count in range(len(rows), -1, -1):
            if not count and rows:
                break
            next_offset = offset + count
            payload = {**base, "encoding": "rows", "columns": safe["columns"], "rows": rows[:count],
                       "returned_rows": count, "page_complete": count == len(rows),
                       "complete": offset == 0 and count == total,
                       "next_call": continuation(next_offset) if next_offset < total else None}
            encoded = canonical(payload)
            if len(encoded) <= max_chars:
                return encoded

    # Fragment only one row so continuation work is bounded independently of
    # table height. Sanitize before splitting, preventing a secret spanning two
    # fragments from bypassing the existing payload boundary.
    fragment_rows = min(1, len(rows))
    source = canonical({"columns": safe["columns"], "rows": rows[:fragment_rows]})
    if text_offset >= len(source):
        raise ValueError("text_offset 超出当前行片段；请按上次返回的 next_call 续读，或从 text_offset=0 重新读取。")

    def fragment(count: int) -> dict[str, Any]:
        end = text_offset + count
        finished = end == len(source)
        next_call = (continuation(offset, end) if not finished
                     else continuation(offset + fragment_rows) if offset + fragment_rows < total else None)
        return {**base, "encoding": "json_fragment", "returned_rows": 0,
                "fragment_rows": fragment_rows, "fragment_complete": finished,
                "text_offset": text_offset, "total_chars": len(source),
                "content": source[text_offset:end], "next_call": next_call,
                "page_complete": finished and fragment_rows == len(rows),
                "fragment_note": "按 text_offset 拼接同一版本、同一行的 content 后再解析 JSON；本条没有完整 rows。"}

    low, high = 0, len(source) - text_offset
    while low < high:
        count = (low + high + 1) // 2
        if len(canonical(fragment(count))) <= max_chars:
            low = count
        else:
            high = count - 1
    if not low:
        raise ValueError("表格观察元数据超出字符预算，请缩短表名或增加预算。")
    return canonical(fragment(low))


def render_query_observation(df: pd.DataFrame, *, sql: str, result_name: str,
                             row_limit: int = 500, max_chars: int = OBSERVATION_MAX_CHARS) -> str:
    """Bound an unnamed query without inventing a total or a stable cursor.

    The caller fetches one lookahead row. Truncated unnamed queries require an
    explicit named snapshot instead of creating unbounded implicit tables.
    """
    known_total = len(df) <= row_limit
    safe, removed = sanitize_tool_payload({"columns": [str(column) for column in df.columns],
                                          "rows": frame_rows(df, row_limit)})
    base = {"table": None, "revision": None, "offset": 0, "limit": row_limit,
            "total_rows": len(df) if known_total else None, "observed_rows": len(df),
            "total_rows_known": known_total, "completeness_scope": "entire_query_in_this_observation",
            "sanitized_fields": removed}
    next_call = {"tool": "run_sql", "args": {"sql": sql, "result_name": result_name}}
    action = "未保存查询不能稳定续读。请用相同 SQL 并提供 result_name 重新执行，创建有版本的结果表；这会重新计算，不能与本次片段拼接。"
    for count in range(len(safe["rows"]), -1, -1):
        complete = known_total and count == len(df)
        payload = {**base, "encoding": "rows", "columns": safe["columns"], "rows": safe["rows"][:count],
                   "returned_rows": count, "complete": complete,
                   "next_call": None if complete else next_call,
                   "next_action": None if complete else action}
        if len(canonical(payload)) <= max_chars:
            return canonical(payload)
    # An arbitrary SQL statement or header can exceed the envelope by itself.
    # Keep a valid, explicit receipt instead of clipping its JSON or its values.
    payload = {**base, "encoding": "rows", "columns": [], "rows": [], "returned_rows": 0,
               "complete": False, "omitted_columns": len(safe["columns"]), "next_call": None,
               "next_action": action}
    return canonical(payload)


def _result_name(ctx: AgentContext, requested: str | None, default: str) -> str:
    """保留输入和历次中间表，避免同名计算覆盖依赖链，方便重新执行。"""
    base = sanitize_table_name(requested or default)
    return ctx.ws.unique_name(base)


def _clip(text: str) -> str:
    return text if len(text) <= MAX_TOOL_TEXT else text[:MAX_TOOL_TEXT] + "\n…（内容过长已截断）"


def _assert_table_scope(ctx: AgentContext, names: list[str]) -> None:
    """Enforce the per-agent table view at the tool boundary.

    ``visible_tables`` only controls prompt construction. Read/query tools
    must repeat the check because a model can still name an arbitrary table in
    SQL or in a tool argument. A branch adds only its own derived tables to
    this allowlist after successful execution.
    """
    if ctx.table_filter is None:
        return
    for raw_name in dict.fromkeys(str(name).strip() for name in names if str(name).strip()):
        ctx.ws.require(raw_name)
        if raw_name not in ctx.table_filter:
            allowed = "、".join(sorted(ctx.table_filter)) or "当前分支表"
            raise ValueError(f"当前并行分析分支只能读取「{allowed}」，不能访问「{raw_name}」。")


def public_detail_text(value: Any, limit: int = 2400) -> str:
    """Allow business feedback, never raw code, diagnostic IDs or parameter dictionaries."""
    if not isinstance(value, (str, int, float, bool)) or value is None:
        return ""
    text = re.sub(r"```[\s\S]*?(?:```|$)", "", str(value))
    text = re.split(r'Traceback \(most recent call last\):|\n\s*File "', text)[0]
    text = re.sub(r"\b(?:recipe_id|run_id|file_id|tool_call_id|result_call_id)\s*[=:]\s*[A-Za-z0-9_-]+", "", text)
    text = re.sub(r"\b(?:rcp|run|scr|script|call)_[A-Za-z0-9_-]+\b|\b(?:f|cv)_[a-f0-9]{8,}\b", "", text)
    parts = re.split(r"(?<=[。；\n])", text)
    text = "".join(part for part in parts if not re.search(
        r"\b(?:SELECT\b[\s\S]*\bFROM|INSERT\s+INTO|import\s+\w+|def\s+\w+\(|pd\.|np\.|openpyxl\.)|[\{\[]\s*[\"'][^\"']+[\"']\s*:", part, re.I))
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text if len(text) <= limit else text[:limit] + "…"


def public_detail_section(label: str, values: list[Any]) -> dict[str, Any]:
    return {"label": label, "items": [text for value in values if (text := public_detail_text(value))]}


def _file_detail(item: dict[str, Any]) -> str:
    kind = item.get("kind_label") or {"input": "原始文件", "expected": "结果样例", "output": "结果文件"}.get(item.get("kind"), "文件")
    sheets = [str(sheet.get("sheet") or sheet.get("name")) for sheet in item.get("sheets", []) if isinstance(sheet, dict) and (sheet.get("sheet") or sheet.get("name"))]
    tables = [str(table) for table in item.get("tables", []) if isinstance(table, str)]
    return f"「{item['filename']}」 · {kind}" + (f" · 工作表：{'、'.join(sheets)}" if sheets else "") + (f" · 已加载为：{'、'.join(tables)}" if tables else " · 尚未加载到分析工作区")


def _table_detail(info: TableInfo) -> str:
    source = f"「{info.filename}」" + (f" / 「{info.sheet}」" if info.sheet else "") if info.filename else ""
    return (source + " → " if source else "") + f"「{info.name}」：{info.rows} 行 × {len(info.columns)} 列"
