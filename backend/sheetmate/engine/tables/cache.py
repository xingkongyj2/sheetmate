"""Conservative cache eligibility for deterministic local SELECTs.

Unknown functions and table functions bypass the cache. This is deliberately a
small allowlist rather than trying to enumerate all sources of nondeterminism.
"""
from __future__ import annotations

from typing import Any

from sheetmate.engine.tables.sql import sql_base_table_nodes

_PURE_FUNCTIONS = frozenset({
    "sum", "avg", "min", "max", "count", "count_star", "count_if", "countif",
    "abs", "round", "floor", "ceil", "ceiling", "trunc", "greatest", "least",
    "lower", "upper", "trim", "ltrim", "rtrim", "length", "char_length",
    "replace", "substr", "substring", "concat", "concat_ws", "left", "right",
    "coalesce", "ifnull", "nullif", "if", "date_part", "date_trunc", "strftime",
    "strptime", "try_strptime", "year", "month", "day", "extract",
    "+", "-", "*", "/", "//", "%", "||", "~~", "!~~", "~~*", "!~~*",
})
_CLOCK_NAMES = frozenset({"current_date", "current_time", "current_timestamp", "localtime", "localtimestamp"})


def cacheable_tables(ast: dict[str, Any]) -> list[str] | None:
    if ast.get("error"):
        return None

    def pure(node: Any) -> bool:
        if isinstance(node, list):
            return all(pure(item) for item in node)
        if not isinstance(node, dict):
            return True
        if (node.get("sample") or node.get("type") == "TABLE_FUNCTION"
                or node.get("class") in {"WINDOW", "LAMBDA", "PARAMETER"}):
            return False
        if node.get("class") == "FUNCTION":
            if node.get("schema") or node.get("catalog") or str(node.get("function_name", "")).lower() not in _PURE_FUNCTIONS:
                return False
        if node.get("class") == "COLUMN_REF" and any(str(name).lower() in _CLOCK_NAMES for name in node.get("column_names", [])):
            return False
        literal = node.get("literal")
        if isinstance(literal, dict) and str(literal.get("text", "")).lower() in {"now", "today", "tomorrow", "yesterday"}:
            return False
        return all(pure(child) for child in node.values())

    if not pure(ast):
        return None
    refs = list(sql_base_table_nodes(ast))
    if any(node.get("schema_name") or node.get("catalog_name") or node.get("at_clause") for node in refs):
        return None
    return list(dict.fromkeys(node["table_name"] for node in refs)) or None
