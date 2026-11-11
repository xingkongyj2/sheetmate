"""Fail-closed source resolution before executing model-generated SQL."""

from __future__ import annotations

import json
from typing import Any

from sheetmate.engine.tables.sql import sql_base_table_nodes


def checked_sql_tables(ctx: Any, sql: str) -> list[str]:
    try:
        raw = ctx.ws.fetch_df("SELECT json_serialize_sql(?) AS ast", [sql]).iloc[0, 0]
        ast = json.loads(raw)
    except Exception as exc:
        raise ValueError("无法可靠解析查询输入范围，请改写为直接引用工作区表的 SELECT。") from exc
    if ast.get("error"):
        raise ValueError("无法可靠解析查询输入范围，请改写为直接引用工作区表的 SELECT。")

    def inspect(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                inspect(child)
        elif isinstance(node, dict):
            if node.get("type") == "TABLE_FUNCTION":
                function = (node.get("function") or {}).get("function_name", "")
                if ctx.table_filter is not None or str(function).lower() not in {"range", "generate_series", "unnest"}:
                    raise ValueError("查询输入范围不明确：不能通过动态查询或表函数读取数据，请直接引用已加载的工作区表。")
            for child in node.values():
                inspect(child)

    inspect(ast)
    references = list(sql_base_table_nodes(ast))
    if any(node.get("schema_name") or node.get("catalog_name") or
           len((node.get("qualified_name") or {}).get("path") or []) > 1 for node in references):
        raise ValueError("查询不能通过 schema/catalog 限定名绕过工作区表范围，请直接使用表名。")
    return list(dict.fromkeys(node["table_name"] for node in references))
