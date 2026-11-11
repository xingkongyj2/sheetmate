"""presentation tool capabilities."""

from __future__ import annotations

import re
from typing import Any
import pandas as pd
from sheetmate.engine.tables.sql import frame_rows, sanitize_table_name
from sheetmate.engine.tables.workspace import TableInfo, Workspace
from sheetmate.agent.tools.contracts import AgentContext


MAX_TOOL_TEXT = 6000


PREVIEW_ROWS = 50


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
