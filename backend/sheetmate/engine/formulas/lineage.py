"""将可信内置公式的源行标识与数据放进同一次 SELECT。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from sheetmate.engine.formulas import BUILTIN_FORMULAS, ResolvedFormula
from sheetmate.engine.formulas.base import FormulaPlan
from sheetmate.engine.tables.sql import quote_ident
from sheetmate.engine.tables.workspace import Workspace, WorkspaceError, validated_row_lineage

_ROW_FORMULAS = frozenset({
    "select_columns", "drop_columns", "rename_columns", "filter_rows", "sort_rows", "fill_null",
    "trim_text", "cast_columns", "compute_column", "map_values", "split_column", "concat_columns",
    "add_serial", "drop_duplicates", "top_n",
})


# 结果行与左表行一一对应、顺序不变的"保左表行"公式：每个结果行只来自左表的一行。
# lookup 把匹配表折叠成每个键一行，结果行数恒等于左表；join_tables 仅在 left/inner 时才可能保持左表行
# （right/full 会出现没有左表来源的行），右表键重复造成的一对多由建表时的唯一性检查拦下，不会误传身份。
_LEFT_ROW_FORMULAS = {"lookup": "table", "join_tables": "left"}
_LEFT_JOIN_KINDS = frozenset({"left", "inner"})
_LOOKUP_SELECT = "SELECT l.*, "


@dataclass(frozen=True)
class LineagePlan:
    sql: str
    source: str
    column: str
    optional: bool = False  # 来自用户 SQL 的改写：执行不了（如含聚合）时回退为普通建表，不能让查询本身失败


def _single_expression(ws: Workspace, expression: str) -> bool:
    """仅解析，不执行。表达式不能逃出括号变成 UNION / ORDER BY / 其他查询。"""
    try:
        parsed = json.loads(ws.fetch("SELECT json_serialize_sql(?)", [f"SELECT ({expression})"])[0][0])
        if parsed.get("error") or len(parsed.get("statements", [])) != 1:
            return False
        node = parsed["statements"][0]["node"]
        selected = node.get("select_list", [])
        return (
            node.get("type") == "SELECT_NODE"
            and node.get("from_table", {}).get("type") == "EMPTY"
            and len(selected) == 1 and not selected[0].get("alias")
            and not node.get("modifiers") and not node.get("cte_map", {}).get("map")
            and not any(node.get(key) for key in (
                "where_clause", "group_expressions", "group_sets", "having", "sample", "qualify",
            ))
            and node.get("aggregate_handling") == "STANDARD_HANDLING"
        )
    except (WorkspaceError, ValueError, TypeError, KeyError, IndexError):
        return False


_SIMPLE_SELECT_MODIFIERS = frozenset({"ORDER_MODIFIER", "LIMIT_MODIFIER"})


def compile_sql_row_lineage(ws: Workspace, sql: str, source: str) -> LineagePlan | None:
    """单表 SELECT（可带 WHERE / ORDER BY / LIMIT / 计算列 / 窗口列）时，在结果里带上输入表的物理 rowid。

    只做语法树判定与改写，不执行。JOIN、集合运算、GROUP BY、DISTINCT、子查询来源、抽样一律返回 None；
    无 GROUP BY 的聚合（如 SELECT sum(a) FROM t）改写后无法执行，由调用方回退为普通建表。
    """
    try:
        source_info = ws.require(source)
        if validated_row_lineage(source_info) is None:
            return None
        parsed = json.loads(ws.fetch("SELECT json_serialize_sql(?)", [sql])[0][0])
        if parsed.get("error") or len(parsed.get("statements", [])) != 1:
            return None
        node = parsed["statements"][0]["node"]
        table = node.get("from_table", {})
        if (
            node.get("type") != "SELECT_NODE" or table.get("type") != "BASE_TABLE"
            or str(table.get("table_name", "")).casefold() != source.casefold()
            or table.get("schema_name") not in ("", "main", None) or table.get("catalog_name") not in ("", None)
            or node.get("cte_map", {}).get("map")
            or any(node.get(key) for key in ("group_expressions", "group_sets", "having", "sample", "qualify"))
            or node.get("aggregate_handling") != "STANDARD_HANDLING"
            or any(modifier.get("type") not in _SIMPLE_SELECT_MODIFIERS for modifier in node.get("modifiers", []))
        ):
            return None
        column_names = {name.casefold() for name in source_info.column_names}
        base = f"__sm_row_lineage_{uuid4().hex}"
        column, suffix = base, 0
        while column.casefold() in column_names or column.casefold() in sql.casefold():
            suffix += 1
            column = f"{base}_{suffix}"
        node["select_list"].insert(0, {"class": "COLUMN_REF", "type": "COLUMN_REF", "alias": column,
                                       "query_location": 0, "column_names": ["rowid"]})
        rewritten = ws.fetch("SELECT json_deserialize_sql(?::JSON)", [json.dumps(parsed)])[0][0]
        if not isinstance(rewritten, str) or not rewritten.upper().startswith("SELECT "):
            return None
        return LineagePlan(rewritten, source, column, optional=True)
    except (WorkspaceError, ValueError, TypeError, KeyError, IndexError, AttributeError):
        return None


def _compile_left_row_lineage(fid: str, params: dict[str, Any], plan: FormulaPlan, ws: Workspace) -> LineagePlan | None:
    """lookup / join_tables：在最外层 SELECT 里带上左表（别名 l）的物理 rowid。"""
    source = params.get(_LEFT_ROW_FORMULAS[fid])
    if not isinstance(source, str):
        return None
    if fid == "join_tables" and str(params.get("how", "left")).lower() not in _LEFT_JOIN_KINDS:
        return None
    try:
        source_info = ws.require(source)
    except WorkspaceError:
        return None
    if validated_row_lineage(source_info) is None:
        return None
    names = {name.casefold() for name in source_info.column_names}
    for table in dict.fromkeys(plan.inputs):
        try:
            names.update(name.casefold() for name in ws.require(table).column_names)
        except WorkspaceError:
            return None
    if "rowid" in names:
        return None  # 业务列叫 rowid 会遮住伪列，不能声称行身份可信
    base = f"__sm_row_lineage_{uuid4().hex}"
    column, suffix = base, 0
    while column.casefold() in names or column.casefold() in plan.sql.casefold():
        suffix += 1
        column = f"{base}_{suffix}"
    marker = f"l.rowid AS {quote_ident(column)}, "
    if fid == "lookup":
        # lookup 的 CTE 里没有同样的写法，最外层 SELECT 是唯一一处 "SELECT l.*, "。
        if plan.sql.count(_LOOKUP_SELECT) != 1:
            return None
        sql = plan.sql.replace(_LOOKUP_SELECT, f"SELECT {marker}l.*, ", 1)
    else:
        if not plan.sql.startswith("SELECT "):
            return None
        sql = f"SELECT {marker}{plan.sql[len('SELECT '):]}"
    return LineagePlan(sql, source, column)


def compile_row_lineage(
    resolved: ResolvedFormula, params: dict[str, Any], plan: FormulaPlan, ws: Workspace,
) -> LineagePlan | None:
    """只有真实内置实现且来源可验证时才增加内部行号，不以结果行数猜测身份。"""
    fid = resolved.spec.id
    if fid in _LEFT_ROW_FORMULAS and resolved.builtin is BUILTIN_FORMULAS.get(fid):
        return _compile_left_row_lineage(fid, params, plan, ws)
    if fid == "custom_sql" and resolved.builtin is BUILTIN_FORMULAS.get(fid):
        inputs = list(dict.fromkeys(plan.inputs))
        return compile_sql_row_lineage(ws, plan.sql, inputs[0]) if len(inputs) == 1 else None
    if fid not in _ROW_FORMULAS or resolved.builtin is not BUILTIN_FORMULAS.get(fid):
        return None
    source = params.get("table")
    if not isinstance(source, str) or list(dict.fromkeys(plan.inputs)) != [source]:
        return None
    source_info = ws.require(source)
    if validated_row_lineage(source_info) is None or not plan.sql.startswith("SELECT "):
        return None
    for key, spec in resolved.spec.params.items():
        if spec.type == "expr" and params.get(key) is not None and not _single_expression(ws, str(params[key])):
            return None
    column_names = {column.casefold() for column in source_info.column_names}
    nested = fid == "drop_duplicates" or (fid == "top_n" and params.get("group_by"))
    if nested and column_names.intersection({"__rid", "__rn", "__grp"}):
        # 这些旧内置子查询已有保留别名，遇到业务同名列不能声称行身份可信。
        return None
    base = f"__sm_row_lineage_{uuid4().hex}"
    column, suffix = base, 0
    while column.casefold() in column_names or column.casefold() in plan.sql.casefold():
        suffix += 1
        column = f"{base}_{suffix}"
    row_id = "__rid" if nested else "rowid"
    sql = f"SELECT {row_id} AS {quote_ident(column)}, " + plan.sql[len("SELECT "):]
    return LineagePlan(sql, source, column)


def create_table_with_optional_lineage(
    ws: Workspace, name: str, sql: str, lineage: LineagePlan | None, **meta: Any,
):
    """按需带行身份建表。可信内置公式的失败照常抛出；用户 SQL 的行身份改写失败则回退为普通建表
    （事务已回滚），原 SQL 自身的错误仍会在回退路径里按原样报告。"""
    if lineage is None:
        return ws.create_table_as(name, sql, **meta)
    if not lineage.optional:
        return ws.create_table_as_with_lineage(
            name, lineage.sql, lineage_source=lineage.source, lineage_column=lineage.column, **meta)
    try:
        return ws.create_table_as_with_lineage(
            name, lineage.sql, lineage_source=lineage.source, lineage_column=lineage.column, **meta)
    except Exception:  # 行身份只是附加能力：无论怎样失败（事务已回滚）都回退，原 SQL 自身的错误由回退路径如实报告
        return ws.create_table_as(name, sql, **meta)
