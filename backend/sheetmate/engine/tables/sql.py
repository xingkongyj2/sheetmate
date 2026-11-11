"""SQL 与数据值的通用工具。

- 标识符 / 字面量转义（防注入的基础）
- 表名清洗
- 表达式与查询的安全检查
- DataFrame 值转成 JSON 安全值（给前端预览和 LLM 上下文用）
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
import math
import re
import threading
from collections.abc import Iterator
from typing import Any

import duckdb
import numpy as np
import pandas as pd

# 用户/AI 写的表达式里不允许出现的语句级关键字（整词匹配，字符串字面量里的不算）
_FORBIDDEN_KEYWORDS = re.compile(
    r"\b(ATTACH|DETACH|COPY|INSTALL|LOAD|PRAGMA|CREATE|DROP|ALTER|INSERT|UPDATE|DELETE|EXPORT|IMPORT|"
    r"CALL|SET|RESET|CHECKPOINT|VACUUM|USE|TRUNCATE)\b",
    re.IGNORECASE,
)
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
_QUOTED_IDENT = re.compile(r'"(?:[^"]|"")*"')


class UnsafeSQLError(ValueError):
    """表达式或查询未通过安全检查。"""


def quote_ident(name: str) -> str:
    """双引号标识符：列名、表名一律这样引用，中文/空格/特殊字符都安全。"""
    return '"' + str(name).replace('"', '""') + '"'


def quote_literal(value: Any) -> str:
    """把 Python 值转成 SQL 字面量。"""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)):
        if not math.isfinite(float(value)):
            return "NULL"
        return repr(float(value))
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, dt.datetime):
        return f"TIMESTAMP '{value.isoformat(sep=' ')}'"
    if isinstance(value, dt.date):
        return f"DATE '{value.isoformat()}'"
    return "'" + str(value).replace("'", "''") + "'"


def sanitize_table_name(name: str, fallback: str = "表") -> str:
    """把任意文本清洗成适合做表名的字符串（保留中文、字母、数字、下划线）。"""
    text = re.sub(r"\.(xlsx|xlsm|xls|csv)$", "", str(name or "").strip(), flags=re.IGNORECASE)
    text = re.sub(r"[^\w一-鿿]+", "_", text).strip("_")
    if not text or text.startswith("__sm"):
        text = fallback
    if text[0].isdigit():
        text = f"t_{text}"
    return text[:60]


def strip_literals(sql: str) -> str:
    """去掉字符串字面量和引号标识符，只留下 SQL 结构，用于关键字检查。"""
    return _QUOTED_IDENT.sub('""', _STRING_LITERAL.sub("''", sql))


def assert_safe_expression(expr: str) -> str:
    """校验一个 SQL 表达式片段（筛选条件、计算列）：不能含分号、不能含语句级关键字。"""
    text = str(expr or "").strip()
    if not text:
        raise UnsafeSQLError("表达式不能为空")
    if len(text) > 4000:
        raise UnsafeSQLError("表达式过长")
    structure = strip_literals(text)
    if ";" in structure:
        raise UnsafeSQLError("表达式里不能包含分号")
    match = _FORBIDDEN_KEYWORDS.search(structure)
    if match:
        raise UnsafeSQLError(f"表达式里不能包含关键字 {match.group(1).upper()}")
    return text


def assert_single_select(sql: str) -> str:
    """校验一条完整查询：只能是一条 SELECT（含 WITH）语句。返回去掉结尾分号的 SQL。"""
    text = str(sql or "").strip().rstrip(";").strip()
    if not text:
        raise UnsafeSQLError("SQL 不能为空")
    try:
        statements = duckdb.extract_statements(text)
    except duckdb.Error as exc:
        raise UnsafeSQLError(f"SQL 语法错误：{exc}") from exc
    if len(statements) != 1:
        raise UnsafeSQLError("只能写一条查询语句")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise UnsafeSQLError("只允许 SELECT 查询，不能修改或创建表")
    match = _FORBIDDEN_KEYWORDS.search(strip_literals(text))
    if match and match.group(1).upper() not in {"SET"}:
        raise UnsafeSQLError(f"查询里不能包含关键字 {match.group(1).upper()}")
    return text


_PARSER_LOCK = threading.Lock()
_PARSER: duckdb.DuckDBPyConnection | None = None


def sql_table_refs(sql: str) -> list[str]:
    """用 DuckDB 自带的解析器找出一条查询引用了哪些表（排除 CTE 名）。解析失败返回空列表。"""
    global _PARSER
    try:
        with _PARSER_LOCK:
            if _PARSER is None:
                _PARSER = duckdb.connect(":memory:")
                _PARSER.execute("SET enable_external_access = false").fetchall()
            raw = _PARSER.execute("SELECT json_serialize_sql(?)", [sql]).fetchall()[0][0]
        ast = json.loads(raw)
    except Exception:
        return []
    if ast.get("error"):
        return []
    return list(dict.fromkeys(node["table_name"] for node in sql_base_table_nodes(ast)))


def sql_base_table_nodes(ast: Any) -> Iterator[dict[str, Any]]:
    """遍历 DuckDB AST 的真实表引用，按查询作用域排除 CTE（保留可原位改写的节点）。"""
    def walk(node: Any, scope: frozenset[str]) -> Iterator[dict[str, Any]]:
        if isinstance(node, list):
            for child in node:
                yield from walk(child, scope)
            return
        if not isinstance(node, dict):
            return
        local = scope
        cte_map = node.get("cte_map")
        if isinstance(cte_map, dict):
            # 非递归 CTE 只看得见之前的 CTE；自己的定义中同名表仍是实际输入表。
            for item in cte_map.get("map", []):
                if isinstance(item, dict):
                    yield from walk(item.get("value"), local)
                    if item.get("key"):
                        local = local | {str(item["key"]).casefold()}
        if node.get("type") == "BASE_TABLE" and node.get("table_name"):
            path = (node.get("qualified_name") or {}).get("path") or []
            qualified = bool(node.get("schema_name") or node.get("catalog_name") or len(path) > 1)
            if qualified or str(node["table_name"]).casefold() not in local:
                yield node
        recursive = node.get("type") == "RECURSIVE_CTE_NODE" and node.get("cte_name")
        for key, child in node.items():
            if key == "cte_map":
                continue
            # 递归 CTE 的种子仍能读同名原表，递归分支才绑定 CTE 本身。
            child_scope = local | {str(node["cte_name"]).casefold()} if recursive and key == "right" else local
            yield from walk(child, child_scope)

    yield from walk(ast, frozenset())


# ---------------------------------------------------------------------------
# JSON 安全转换
# ---------------------------------------------------------------------------


def clean_float(value: float, digits: int = 12) -> float:
    """去掉二进制浮点误差：341.73999999999995 → 341.74，0.1 + 0.2 → 0.3。

    预览和给 AI 看的数据用 12 位有效数字（累加几千行的误差也能抹掉）；写 Excel 用 15 位（和 Excel 本身的精度一致）。
    只影响显示和导出，引擎里的计算不变。
    """
    if not math.isfinite(value):
        return value
    return float(f"{value:.{digits}g}")


def to_jsonable(value: Any) -> Any:
    """把 DataFrame 里的单元格值转成 JSON 能表示的值。"""
    if value is None:
        return None
    if isinstance(value, (float, np.floating)):
        v = float(value)
        return clean_float(v) if math.isfinite(v) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        value = value.to_pydatetime()
    if isinstance(value, dt.datetime):
        if value.hour == value.minute == value.second == 0 and value.microsecond == 0:
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (dt.time, dt.timedelta)):
        return str(value)
    if isinstance(value, (list, tuple, np.ndarray)):
        return [to_jsonable(v) for v in list(value)]
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def frame_rows(df: pd.DataFrame, limit: int | None = None) -> list[list[Any]]:
    """DataFrame → 二维列表（保持列顺序），用于前端表格预览。"""
    part = df if limit is None else df.head(limit)
    return [[to_jsonable(v) for v in row] for row in part.itertuples(index=False, name=None)]


def frame_to_text(df: pd.DataFrame, limit: int = 8, max_cell: int = 40) -> str:
    """DataFrame → 紧凑文本（给 LLM 看的数据样例）。"""
    if df.empty:
        return "(空表)"
    cols = [str(c) for c in df.columns]
    lines = [" | ".join(cols)]
    for row in frame_rows(df, limit):
        cells = []
        for v in row:
            text = "" if v is None else str(v)
            cells.append(text if len(text) <= max_cell else text[: max_cell - 1] + "…")
        lines.append(" | ".join(cells))
    if len(df) > limit:
        lines.append(f"…（共 {len(df)} 行，仅展示前 {limit} 行）")
    return "\n".join(lines)
