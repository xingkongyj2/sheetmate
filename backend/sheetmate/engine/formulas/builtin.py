"""内置公式库（scope=global，只读）。

每个内置公式 = 参数声明 + SQL 生成函数（build）+ 人话描述（describe）。
SQL 生成规则：
- 列名、表名一律双引号转义；值一律转成 SQL 字面量；表达式参数经过安全检查
- 输入都是工作区里的物理表，用 rowid 保持原始行顺序（分组结果按首次出现的顺序）
"""

from __future__ import annotations

from typing import Any

from sheetmate.engine.formulas.base import (
    BuiltinFormula,
    Formula,
    FormulaError,
    FormulaPlan,
    ParamSpec,
    check_columns,
)
from sheetmate.engine.sqlutil import (
    assert_single_select,
    quote_ident as q,
    quote_literal as lit,
    sql_table_refs,
)
from sheetmate.engine.workspace import Workspace

_MAX_PIVOT_VALUES = 200

# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------


def _cols(columns: list[str], alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    return ", ".join(f"{prefix}{q(c)}" for c in columns)


def _types(ws: Workspace, table: str) -> dict[str, str]:
    return {c["name"]: c["type"] for c in ws.require(table).columns}


def _num(expr: str, col_type: str) -> str:
    """把一列当数字用：文本列先去掉千分位再转成数字。"""
    if col_type in {"integer", "float"}:
        return expr
    return f"TRY_CAST(replace(CAST({expr} AS VARCHAR), ',', '') AS DOUBLE)"


def _key_expr(expr: str, normalize: bool) -> str:
    """匹配键标准化：转文本、去首尾及中间空白、转大写、去掉数字末尾的 .0。"""
    if not normalize:
        return f"CAST({expr} AS VARCHAR)"
    return (
        "NULLIF(regexp_replace(regexp_replace(upper(trim(CAST("
        + expr
        + r" AS VARCHAR))), '\s+', '', 'g'), '\.0+$', ''), '')"
    )


def _names(items: list[str], limit: int = 6) -> str:
    shown = "、".join(f"「{i}」" for i in items[:limit])
    return shown + (f" 等 {len(items)} 个" if len(items) > limit else "")


def _ensure_new_names(ws: Workspace, table: str, names: list[str], what: str) -> None:
    existing = set(ws.require(table).column_names)
    clash = [n for n in names if n in existing]
    if clash:
        raise FormulaError(f"{what}{_names(clash)} 已经存在于表「{table}」，请换个名字")


def _spec(
    fid: str,
    name: str,
    category: str,
    intent: str,
    params: dict[str, dict[str, Any]],
    triggers: list[str] | None = None,
    example: dict[str, Any] | None = None,
) -> Formula:
    return Formula(
        id=fid,
        name=name,
        category=category,
        intent=intent,
        type="builtin",
        scope="global",
        params={k: ParamSpec(**v) for k, v in params.items()},
        triggers=triggers or [],
        example=example,
        status="active",
        created_by="human",
    )


_T = {"type": "table", "required": True, "desc": "输入表"}

# ---------------------------------------------------------------------------
# 清洗整理
# ---------------------------------------------------------------------------


def _build_select(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    return FormulaPlan(f"SELECT {_cols(p['columns'])} FROM {q(p['table'])} ORDER BY rowid", [p["table"]])


def _build_drop_columns(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    remaining = [c for c in ws.require(p["table"]).column_names if c not in p["columns"]]
    if not remaining:
        raise FormulaError("不能删除全部列")
    return FormulaPlan(f"SELECT * EXCLUDE ({_cols(p['columns'])}) FROM {q(p['table'])} ORDER BY rowid", [p["table"]])


def _build_rename(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    mapping = {str(k): str(v).strip() for k, v in p["mapping"].items() if str(v).strip() and str(k) != str(v)}
    if not mapping:
        raise FormulaError("mapping 里没有需要改名的列")
    check_columns(ws, p["table"], list(mapping))
    remaining = [c for c in ws.require(p["table"]).column_names if c not in mapping]
    clash = [v for v in mapping.values() if v in remaining or list(mapping.values()).count(v) > 1]
    if clash:
        raise FormulaError(f"新列名{_names(clash)}和已有列重名")
    pairs = ", ".join(f"{q(k)} AS {q(v)}" for k, v in mapping.items())
    return FormulaPlan(f"SELECT * RENAME ({pairs}) FROM {q(p['table'])} ORDER BY rowid", [p["table"]])


def _build_filter(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    return FormulaPlan(f"SELECT * FROM {q(p['table'])} WHERE ({p['condition']}) ORDER BY rowid", [p["table"]])


def _build_dedup(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    cols = p["columns"] or ws.require(p["table"]).column_names
    order = "DESC" if p["keep"] == "last" else "ASC"
    # 注意：DuckDB 2.0 的 QUALIFY 与 ORDER BY rowid 同时使用会触发内部错误，统一改成子查询写法
    sql = (
        "SELECT * EXCLUDE (__rn, __rid) FROM ("
        f"SELECT *, rowid AS __rid, ROW_NUMBER() OVER (PARTITION BY {_cols(cols)} ORDER BY rowid {order}) AS __rn "
        f"FROM {q(p['table'])}) WHERE __rn = 1 ORDER BY __rid"
    )
    return FormulaPlan(sql, [p["table"]])


def _parse_sort_items(raw: Any) -> list[tuple[str, str]]:
    items = raw if isinstance(raw, list) else [raw]
    result: list[tuple[str, str]] = []
    for item in items:
        if isinstance(item, dict):
            col = item.get("column") or item.get("col") or item.get("name")
            order = str(item.get("order") or item.get("direction") or "asc").lower()
        else:
            text = str(item).strip()
            col, order = text, "asc"
            for suffix, value in ((" desc", "desc"), (" asc", "asc"), (" 降序", "desc"), (" 升序", "asc")):
                if text.lower().endswith(suffix):
                    col, order = text[: -len(suffix)].strip(), value
        if not col:
            raise FormulaError("排序字段不能为空")
        result.append((str(col), "desc" if order.startswith(("desc", "降")) else "asc"))
    return result


def _build_sort(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    items = _parse_sort_items(p["by"])
    check_columns(ws, p["table"], [c for c, _ in items])
    order = ", ".join(f"{q(c)} {o.upper()} NULLS LAST" for c, o in items)
    return FormulaPlan(f"SELECT * FROM {q(p['table'])} ORDER BY {order}, rowid", [p["table"]])


def _build_fill_null(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    types = _types(ws, p["table"])
    cols = p["columns"] or list(types)
    replaces = []
    if p["method"] == "forward":
        for c in cols:
            replaces.append(
                f"LAST_VALUE({q(c)} IGNORE NULLS) OVER (ORDER BY rowid ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS {q(c)}"
            )
    else:
        if p["value"] is None:
            raise FormulaError("method=value 时必须提供 value（要填入的值）")
        value = p["value"]
        for c in cols:
            if types[c] in {"integer", "float"}:
                try:
                    number = float(value)
                except (TypeError, ValueError) as exc:
                    raise FormulaError(f"列「{c}」是数字列，填充值「{value}」不是数字") from exc
                replaces.append(f"COALESCE({q(c)}, {lit(number)}) AS {q(c)}")
            elif types[c] == "text":
                replaces.append(f"COALESCE({q(c)}, {lit(str(value))}) AS {q(c)}")
            else:
                replaces.append(f"COALESCE({q(c)}, TRY_CAST({lit(str(value))} AS {_duck_type(types[c])})) AS {q(c)}")
    return FormulaPlan(f"SELECT * REPLACE ({', '.join(replaces)}) FROM {q(p['table'])} ORDER BY rowid", [p["table"]])


def _duck_type(label: str) -> str:
    return {"integer": "BIGINT", "float": "DOUBLE", "datetime": "TIMESTAMP", "boolean": "BOOLEAN"}.get(label, "VARCHAR")


def _build_trim(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    types = _types(ws, p["table"])
    cols = [c for c in (p["columns"] or list(types)) if types.get(c) == "text"]
    if not cols:
        return FormulaPlan(f"SELECT * FROM {q(p['table'])} ORDER BY rowid", [p["table"]], ["没有需要清理的文本列"])
    replaces = ", ".join(
        f"NULLIF(trim(regexp_replace(replace({q(c)}, '　', ' '), '\\s+', ' ', 'g')), '') AS {q(c)}" for c in cols
    )
    return FormulaPlan(f"SELECT * REPLACE ({replaces}) FROM {q(p['table'])} ORDER BY rowid", [p["table"]])


_CAST_TARGETS = {
    "integer": "BIGINT",
    "int": "BIGINT",
    "整数": "BIGINT",
    "float": "DOUBLE",
    "number": "DOUBLE",
    "数字": "DOUBLE",
    "小数": "DOUBLE",
    "text": "VARCHAR",
    "str": "VARCHAR",
    "文本": "VARCHAR",
    "date": "DATE",
    "日期": "DATE",
    "datetime": "TIMESTAMP",
    "时间": "TIMESTAMP",
}


def _build_cast(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    types = _types(ws, p["table"])
    check_columns(ws, p["table"], list(p["types"]))
    replaces = []
    for col, target in p["types"].items():
        duck = _CAST_TARGETS.get(str(target).lower())
        if not duck:
            raise FormulaError(f"不支持转换成「{target}」，可选：integer / float / text / date / datetime")
        src = q(col)
        if duck == "VARCHAR" and types[col] == "float":
            expr = f"CASE WHEN {src} = round({src}) THEN CAST(CAST({src} AS BIGINT) AS VARCHAR) ELSE CAST({src} AS VARCHAR) END"
        elif duck in {"BIGINT", "DOUBLE"} and types[col] == "text":
            expr = f"TRY_CAST(replace({src}, ',', '') AS {duck})"
            if duck == "BIGINT":
                expr = f"CAST(round(TRY_CAST(replace({src}, ',', '') AS DOUBLE)) AS BIGINT)"
        else:
            expr = f"TRY_CAST({src} AS {duck})"
        replaces.append(f"{expr} AS {src}")
    plan = FormulaPlan(f"SELECT * REPLACE ({', '.join(replaces)}) FROM {q(p['table'])} ORDER BY rowid", [p["table"]])

    def post(ws_: Workspace, output: str) -> dict[str, Any]:
        lost = {}
        for col in p["types"]:
            before = ws_.fetch(f"SELECT count({q(col)}) FROM {q(p['table'])}")[0][0]
            after = ws_.fetch(f"SELECT count({q(col)}) FROM {q(output)}")[0][0]
            if after < before:
                lost[col] = int(before - after)
        if lost:
            return {"warnings": [f"列「{c}」有 {n} 个值无法转换，已变为空" for c, n in lost.items()]}
        return {}

    plan.post = post
    return plan


# ---------------------------------------------------------------------------
# 计算
# ---------------------------------------------------------------------------


def _build_compute(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    table, col, expr = p["table"], p["column"], p["expression"]
    cols = ws.require(table).column_names
    if col in cols:
        sql = f"SELECT * REPLACE (({expr}) AS {q(col)}) FROM {q(table)} ORDER BY rowid"
        return FormulaPlan(sql, [table])
    after = p.get("after")
    if after:
        check_columns(ws, table, [after])
        parts = []
        for c in cols:
            parts.append(q(c))
            if c == after:
                parts.append(f"({expr}) AS {q(col)}")
        sql = f"SELECT {', '.join(parts)} FROM {q(table)} ORDER BY rowid"
    else:
        sql = f"SELECT *, ({expr}) AS {q(col)} FROM {q(table)} ORDER BY rowid"
    return FormulaPlan(sql, [table])


def _build_map_values(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    table, col = p["table"], p["column"]
    target = p.get("target") or col
    if not p["mapping"]:
        raise FormulaError("mapping 不能为空")
    whens = " ".join(f"WHEN {lit(str(k))} THEN {lit(v)}" for k, v in p["mapping"].items())
    default = lit(p["default"]) if p.get("default") is not None else f"CAST({q(col)} AS VARCHAR)"
    expr = f"CASE CAST({q(col)} AS VARCHAR) {whens} ELSE {default} END"
    if target in ws.require(table).column_names:
        sql = f"SELECT * REPLACE ({expr} AS {q(target)}) FROM {q(table)} ORDER BY rowid"
    else:
        sql = f"SELECT *, {expr} AS {q(target)} FROM {q(table)} ORDER BY rowid"
    plan = FormulaPlan(sql, [table])

    def post(ws_: Workspace, output: str) -> dict[str, Any]:
        keys = [str(k) for k in p["mapping"]]
        rows = ws_.fetch(
            f"SELECT DISTINCT CAST({q(col)} AS VARCHAR) FROM {q(table)} "
            f"WHERE {q(col)} IS NOT NULL AND CAST({q(col)} AS VARCHAR) NOT IN ({', '.join(lit(k) for k in keys)}) LIMIT 8"
        )
        if rows:
            return {"warnings": [f"这些值不在映射里，{'用默认值' if p.get('default') is not None else '保持原值'}：{[r[0] for r in rows]}"]}
        return {}

    plan.post = post
    return plan


def _build_split(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    table, col, sep, into = p["table"], p["column"], p["separator"], p["into"]
    if not into:
        raise FormulaError("into 至少要有一个新列名")
    keep = p["keep_original"]
    existing = [c for c in ws.require(table).column_names if keep or c != col]
    clash = [n for n in into if n in existing]
    if clash:
        raise FormulaError(f"新列名{_names(clash)}已存在")
    parts = ", ".join(
        f"NULLIF(trim(split_part(CAST({q(col)} AS VARCHAR), {lit(sep)}, {i + 1})), '') AS {q(n)}" for i, n in enumerate(into)
    )
    base = "*" if keep else f"* EXCLUDE ({q(col)})"
    return FormulaPlan(f"SELECT {base}, {parts} FROM {q(table)} ORDER BY rowid", [table])


def _build_concat_columns(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    table = p["table"]
    _ensure_new_names(ws, table, [p["column"]], "新列")
    pieces = ", ".join(f"CAST({q(c)} AS VARCHAR)" for c in p["columns"])
    sql = f"SELECT *, concat_ws({lit(p['separator'] or '')}, {pieces}) AS {q(p['column'])} FROM {q(table)} ORDER BY rowid"
    return FormulaPlan(sql, [table])


def _build_serial(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    table, col = p["table"], p["column"]
    serial = f"ROW_NUMBER() OVER (ORDER BY rowid) AS {q(col)}"
    if col in ws.require(table).column_names:
        return FormulaPlan(f"SELECT * REPLACE ({serial}) FROM {q(table)} ORDER BY rowid", [table])
    return FormulaPlan(f"SELECT {serial}, * FROM {q(table)} ORDER BY rowid", [table])


# ---------------------------------------------------------------------------
# 匹配合并
# ---------------------------------------------------------------------------


def _build_lookup(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    """匹配加列（类似 VLOOKUP）：键标准化 → 重复键检测（拦截多对多）→ 精确匹配 → 可选模糊兜底 → 匹配率报告。"""
    left, right = p["table"], p["lookup_table"]
    key, rkey = p["key"], p.get("lookup_key") or p["key"]
    check_columns(ws, right, [rkey])
    bring = [c for c in p["columns"] if c != rkey]
    if not bring:
        raise FormulaError("columns 里要写要从匹配表带出来的列（不包括匹配键本身）")
    norm = p["normalize_key"]
    lk = _key_expr(f"l.{q(key)}", norm)
    rk = _key_expr(f"r0.{q(rkey)}", norm)
    left_cols = set(ws.require(left).column_names)
    rtypes = _types(ws, right)
    warnings: list[str] = []

    out_names: dict[str, str] = {}
    for c in bring:
        out = c
        if c in left_cols:
            out = f"{c}_匹配"
            warnings.append(f"「{c}」在原表已存在，带出的列命名为「{out}」")
        out_names[c] = out

    # 重复键：多对多会把行数放大、金额重复，默认取第一条，也可以设为报错
    dup = ws.fetch(
        f"SELECT k, n FROM (SELECT {rk} AS k, count(*) AS n FROM {q(right)} AS r0 "
        f"WHERE {rk} IS NOT NULL GROUP BY 1 HAVING count(*) > 1) ORDER BY n DESC LIMIT 5"
    )
    if dup:
        dup_total = ws.fetch(
            f"SELECT count(*) FROM (SELECT {rk} AS k FROM {q(right)} AS r0 WHERE {rk} IS NOT NULL GROUP BY 1 HAVING count(*) > 1)"
        )[0][0]
        examples = "、".join(f"{k}（{n} 次）" for k, n in dup)
        if p["duplicates"] == "error":
            raise FormulaError(f"匹配表「{right}」的键「{rkey}」有 {dup_total} 个重复值（{examples}），已拦截以免一对多重复计算")
        warnings.append(f"匹配表「{right}」有 {dup_total} 个重复键（{examples}），已取第一条")

    values = ", ".join(f"r0.{q(c)} AS {q(f'__v{i}')}" for i, c in enumerate(bring))
    r_cte = (
        f"r AS (SELECT * EXCLUDE (__rn) FROM (SELECT {rk} AS __k, {values}, "
        f"ROW_NUMBER() OVER (PARTITION BY {rk} ORDER BY r0.rowid) AS __rn "
        f"FROM {q(right)} AS r0 WHERE {rk} IS NOT NULL) WHERE __rn = 1)"
    )
    fills = {str(k): v for k, v in (p.get("missing_fill") or {}).items()}

    def value_expr(i: int, c: str, matched: str) -> str:
        fill = fills.get(c, fills.get(out_names[c]))
        if fill is None:
            return f"r.{q(f'__v{i}')} AS {q(out_names[c])}"
        src = f"r.{q(f'__v{i}')}"
        if isinstance(fill, str) and rtypes.get(c) != "text":
            src = f"CAST({src} AS VARCHAR)"
        return f"CASE WHEN {matched} IS NULL THEN {lit(fill)} ELSE {src} END AS {q(out_names[c])}"

    threshold = p.get("fuzzy_threshold")
    if threshold:
        if not 0 < float(threshold) <= 1:
            raise FormulaError("fuzzy_threshold 取值 0~1，例如 0.9")
        score_col = p.get("score_column") or "匹配得分"
        ctes = (
            f"WITH {r_cte}, "
            f"lk AS (SELECT DISTINCT {lk} AS __lk FROM {q(left)} AS l WHERE {lk} IS NOT NULL), "
            "fz AS (SELECT __lk, __rk, __score FROM (SELECT lk.__lk, r.__k AS __rk, "
            "jaro_winkler_similarity(lk.__lk, r.__k) AS __score, "
            "ROW_NUMBER() OVER (PARTITION BY lk.__lk ORDER BY jaro_winkler_similarity(lk.__lk, r.__k) DESC, r.__k) AS __rn "
            "FROM lk CROSS JOIN r WHERE lk.__lk NOT IN (SELECT __k FROM r)) WHERE __rn = 1), "
            "km AS (SELECT __lk, __lk AS __rk, 1.0 AS __score FROM lk WHERE __lk IN (SELECT __k FROM r) "
            f"UNION ALL SELECT __lk, __rk, __score FROM fz WHERE __score >= {float(threshold)}) "
        )
        joins = f"FROM {q(left)} AS l LEFT JOIN km ON {lk} = km.__lk LEFT JOIN r ON km.__rk = r.__k"
        select = ", ".join(value_expr(i, c, "r.__k") for i, c in enumerate(bring))
        sql = f"{ctes}SELECT l.*, {select}, round(km.__score, 3) AS {q(score_col)} {joins} ORDER BY l.rowid"
        stats_sql = f"{ctes}SELECT count(*), count(r.__k), count(*) FILTER (WHERE km.__score < 1) {joins}"
        miss_sql = f"{ctes}SELECT DISTINCT CAST(l.{q(key)} AS VARCHAR) {joins} WHERE r.__k IS NULL AND l.{q(key)} IS NOT NULL LIMIT 5"
    else:
        ctes = f"WITH {r_cte} "
        joins = f"FROM {q(left)} AS l LEFT JOIN r ON {lk} = r.__k"
        select = ", ".join(value_expr(i, c, "r.__k") for i, c in enumerate(bring))
        sql = f"{ctes}SELECT l.*, {select} {joins} ORDER BY l.rowid"
        stats_sql = f"{ctes}SELECT count(*), count(r.__k), 0 {joins}"
        miss_sql = f"{ctes}SELECT DISTINCT CAST(l.{q(key)} AS VARCHAR) {joins} WHERE r.__k IS NULL AND l.{q(key)} IS NOT NULL LIMIT 5"

    total, matched, fuzzy = ws.fetch(stats_sql)[0]
    missing = [r[0] for r in ws.fetch(miss_sql)]
    rate = round(matched / total, 4) if total else 1.0
    metrics = {
        "total_rows": int(total),
        "matched_rows": int(matched),
        "unmatched_rows": int(total - matched),
        "fuzzy_rows": int(fuzzy or 0),
        "match_rate": rate,
        "unmatched_examples": missing,
        "duplicate_keys": len(dup),
    }
    if total - matched:
        filled = "，已按设置填充" if fills else ""
        warnings.append(f"有 {total - matched} 行没匹配上（匹配率 {rate:.1%}），示例：{missing}{filled}")
    return FormulaPlan(sql, [left, right], warnings, {"metrics": metrics})


def _build_join(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    left, right = p["left"], p["right"]
    left_on = p["left_on"]
    right_on = p.get("right_on") or left_on
    if len(left_on) != len(right_on):
        raise FormulaError("left_on 和 right_on 的列数必须一样")
    check_columns(ws, right, right_on)
    ltypes, rtypes = _types(ws, left), _types(ws, right)
    how = {"inner": "INNER", "left": "LEFT", "right": "RIGHT", "full": "FULL OUTER", "outer": "FULL OUTER"}[p["how"]]
    same_keys = {r for l_, r in zip(left_on, right_on) if l_ == r}

    select: list[str] = []
    for c in ltypes:
        if c in same_keys and how in {"RIGHT", "FULL OUTER"}:
            select.append(f"COALESCE(l.{q(c)}, r.{q(c)}) AS {q(c)}")
        else:
            select.append(f"l.{q(c)}")
    suffix = p.get("suffix") or "_右"
    for c in rtypes:
        if c in same_keys:
            continue
        name = c if c not in ltypes else f"{c}{suffix}"
        select.append(f"r.{q(c)} AS {q(name)}")

    conds = []
    for lc, rc in zip(left_on, right_on):
        if ltypes[lc] == rtypes[rc]:
            conds.append(f"l.{q(lc)} = r.{q(rc)}")
        else:
            conds.append(f"CAST(l.{q(lc)} AS VARCHAR) = CAST(r.{q(rc)} AS VARCHAR)")
    sql = (
        f"SELECT {', '.join(select)} FROM {q(left)} AS l {how} JOIN {q(right)} AS r ON {' AND '.join(conds)} "
        "ORDER BY l.rowid NULLS LAST, r.rowid"
    )
    plan = FormulaPlan(sql, [left, right])

    def post(ws_: Workspace, output: str) -> dict[str, Any]:
        before, after = ws_.row_count(left), ws_.row_count(output)
        if how in {"LEFT", "INNER"} and after > before:
            return {"warnings": [f"合并后行数从 {before} 变成 {after}：右表有重复键，出现了一对多，请确认金额不会被重复计算"]}
        return {}

    plan.post = post
    return plan


def _build_concat_tables(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    tables = p["tables"]
    if len(tables) < 2:
        raise FormulaError("至少需要两张表才能纵向合并")
    src_col = p.get("source_column")
    parts = []
    for i, t in enumerate(tables):
        info = ws.require(t)
        extra = f", {lit(info.label or t)} AS {q(src_col)}" if src_col else ""
        parts.append(f"SELECT *{extra}, {i} AS __ord, rowid AS __rid FROM {q(t)}")
    sql = f"SELECT * EXCLUDE (__ord, __rid) FROM ({' UNION ALL BY NAME '.join(parts)}) ORDER BY __ord, __rid"
    warnings = []
    column_sets = [set(ws.require(t).column_names) for t in tables]
    union = set().union(*column_sets)
    partial = sorted(c for c in union if not all(c in s for s in column_sets))
    if partial:
        warnings.append(f"这些列不是每张表都有，缺的部分为空：{partial[:8]}")
    return FormulaPlan(sql, list(tables), warnings)


# ---------------------------------------------------------------------------
# 汇总统计
# ---------------------------------------------------------------------------

_AGG_ALIASES = {
    "sum": "sum", "合计": "sum", "求和": "sum", "total": "sum",
    "avg": "avg", "mean": "avg", "average": "avg", "平均": "avg", "平均值": "avg",
    "count": "count", "计数": "count", "条数": "count", "size": "count",
    "count_distinct": "count_distinct", "nunique": "count_distinct", "去重计数": "count_distinct", "distinct": "count_distinct",
    "min": "min", "最小": "min", "最小值": "min",
    "max": "max", "最大": "max", "最大值": "max",
    "first": "first", "第一个": "first", "last": "last", "最后一个": "last",
    "median": "median", "中位数": "median",
    "concat": "concat", "join": "concat", "合并": "concat", "string_agg": "concat",
}
_AGG_SUFFIX = {
    "sum": "合计", "avg": "平均", "count": "计数", "count_distinct": "去重数", "min": "最小",
    "max": "最大", "first": "首个", "last": "末个", "median": "中位数", "concat": "合并",
}


def _parse_aggregations(raw: Any) -> list[dict[str, str]]:
    """兼容多种写法：[{column, func, as}]、{"金额": "sum"}、["金额:sum"]。"""
    items: list[dict[str, str]] = []
    if isinstance(raw, dict):
        for col, func in raw.items():
            for f in func if isinstance(func, list) else [func]:
                items.append({"column": str(col), "func": str(f)})
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                items.append(
                    {
                        "column": str(item.get("column") or item.get("col") or item.get("field") or "*"),
                        "func": str(item.get("func") or item.get("agg") or item.get("function") or "sum"),
                        "as": str(item.get("as") or item.get("alias") or item.get("name") or ""),
                    }
                )
            elif isinstance(item, str) and ":" in item:
                col, func = item.split(":", 1)
                items.append({"column": col.strip(), "func": func.strip()})
            else:
                raise FormulaError(f"无法识别的汇总写法：{item!r}，请用 {{\"column\": \"金额\", \"func\": \"sum\"}}")
    else:
        raise FormulaError("aggregations 应该是列表，例如 [{\"column\": \"金额\", \"func\": \"sum\"}]")
    for item in items:
        func = _AGG_ALIASES.get(item["func"].lower().strip())
        if not func:
            raise FormulaError(f"不支持的汇总方式「{item['func']}」，可选：sum / avg / count / count_distinct / min / max / first / last / median / concat")
        item["func"] = func
    if not items:
        raise FormulaError("aggregations 不能为空")
    return items


def _agg_sql(func: str, col: str, col_type: str) -> str:
    if col == "*":
        if func != "count":
            raise FormulaError("只有 count 可以用 * 作为列")
        return "COUNT(*)"
    c = q(col)
    if func == "sum":
        if col_type == "integer":
            return f"CAST(SUM({c}) AS BIGINT)"
        return f"SUM({_num(c, col_type)})"
    if func == "avg":
        return f"AVG({_num(c, col_type)})"
    if func == "median":
        return f"MEDIAN({_num(c, col_type)})"
    if func == "count":
        return f"COUNT({c})"
    if func == "count_distinct":
        return f"COUNT(DISTINCT {c})"
    if func in {"min", "max"}:
        return f"{func.upper()}({c})"
    if func == "first":
        return f"arg_min({c}, rowid)"
    if func == "last":
        return f"arg_max({c}, rowid)"
    return f"string_agg(DISTINCT CAST({c} AS VARCHAR), '、')"


def _build_group(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    table = p["table"]
    types = _types(ws, table)
    group = p["group_by"] or []
    aggs = _parse_aggregations(p["aggregations"])
    check_columns(ws, table, [a["column"] for a in aggs if a["column"] != "*"])
    used = set(group)
    exprs = []
    for a in aggs:
        col, func = a["column"], a["func"]
        alias = a.get("as") or ("记录数" if col == "*" else (col if func in {"sum", "first", "last", "concat"} else f"{col}_{_AGG_SUFFIX[func]}"))
        if alias in used:
            alias = f"{col}_{_AGG_SUFFIX[func]}" if col != "*" else f"记录数_{len(used)}"
        if alias in used:
            raise FormulaError(f"汇总结果列名「{alias}」重复，请用 as 指定不同的名字")
        used.add(alias)
        exprs.append(f"{_agg_sql(func, col, types.get(col, 'text'))} AS {q(alias)}")
    if group:
        sql = f"SELECT {_cols(group)}, {', '.join(exprs)} FROM {q(table)} GROUP BY {_cols(group)} ORDER BY MIN(rowid)"
    else:
        sql = f"SELECT {', '.join(exprs)} FROM {q(table)}"
    return FormulaPlan(sql, [table])


def _build_pivot_wide(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    """长转宽：每个 index 一行，pivot_column 的每个取值变成一列（例：一单 3 行费用 → 一单 1 行 3 列）。"""
    table, index, pc, vc = p["table"], p["index"], p["pivot_column"], p["value_column"]
    types = _types(ws, table)
    carry = [c for c in (p.get("carry_columns") or []) if c not in index]
    check_columns(ws, table, carry)
    observed = [
        r[0]
        for r in ws.fetch(
            f"SELECT CAST({q(pc)} AS VARCHAR) AS v FROM {q(table)} WHERE {q(pc)} IS NOT NULL GROUP BY 1 ORDER BY MIN(rowid)"
        )
    ]
    values = [str(v) for v in (p.get("values") or observed)]
    warnings: list[str] = []
    if not values:
        raise FormulaError(f"「{pc}」没有任何取值，无法拆列")
    if len(values) > _MAX_PIVOT_VALUES:
        raise FormulaError(f"「{pc}」有 {len(values)} 个不同取值，太多了，不适合拆成列")

    func = p["aggfunc"]
    suffix = p.get("column_suffix") or ""
    reserved = set(index) | set(carry)
    fill = p.get("fill_value")
    value_exprs = []
    for v in values:
        cond = f"CAST({q(pc)} AS VARCHAR) = {lit(v)}"
        if func == "first":
            agg = f"arg_min({q(vc)}, rowid) FILTER (WHERE {cond})"
        elif func == "count":
            agg = f"COUNT({q(vc)}) FILTER (WHERE {cond})"
        elif func in {"max", "min"}:
            agg = f"{func.upper()}({q(vc)}) FILTER (WHERE {cond})"
        elif func == "avg":
            agg = f"AVG({_num(q(vc), types[vc])}) FILTER (WHERE {cond})"
        else:
            inner = f"SUM({_num(q(vc), types[vc])}) FILTER (WHERE {cond})"
            agg = f"CAST({inner} AS BIGINT)" if types[vc] == "integer" else inner
        if fill is not None:
            agg = f"COALESCE({agg}, {lit(fill)})"
        name = f"{v}{suffix}"
        if name in reserved:
            name = f"{v}_值"
        reserved.add(name)
        value_exprs.append(f"{agg} AS {q(name)}")
    carry_exprs = [f"arg_min({q(c)}, rowid) AS {q(c)}" for c in carry]
    select = ", ".join([_cols(index)] + carry_exprs + value_exprs)
    sql = f"SELECT {select} FROM {q(table)} GROUP BY {_cols(index)} ORDER BY MIN(rowid)"

    # 金额守恒：固定了要拆的取值时，没拆成列的取值对应的金额会丢掉 —— 算出来明确告诉用户丢了多少
    dropped = [v for v in observed if v not in values]
    lost_amount = None
    if dropped and func == "sum" and types[vc] in {"integer", "float"}:
        cond = " OR ".join(f"CAST({q(pc)} AS VARCHAR) = {lit(v)}" for v in dropped)
        lost = ws.fetch(f"SELECT sum({_num(q(vc), types[vc])}), count(*) FROM {q(table)} WHERE {cond}")[0]
        lost_amount = {"values": dropped, "amount": round(float(lost[0] or 0), 4), "rows": int(lost[1])}
        warnings.append(
            f"「{pc}」出现了新的取值 {dropped}（{lost_amount['rows']} 行，「{vc}」合计 {lost_amount['amount']:,.2f}），"
            "按设置没有拆成列，这部分金额不在结果里，请确认是否要把它加进流程"
        )

    for c in carry:
        varying = ws.fetch(
            f"SELECT count(*) FROM (SELECT 1 FROM {q(table)} GROUP BY {_cols(index)} HAVING count(DISTINCT {q(c)}) > 1)"
        )[0][0]
        if varying:
            warnings.append(f"跟随列「{c}」在 {varying} 个分组里取值不唯一，已取第一行的值")
    notes = {"pivot_values": values, "observed_values": observed}
    if lost_amount:
        notes["metrics"] = {"dropped_values": lost_amount["values"], "dropped_amount": lost_amount["amount"],
                            "dropped_rows": lost_amount["rows"]}
    return FormulaPlan(sql, [table], warnings, notes)


def _build_unpivot(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    """宽转长：把多列转成「项目 + 数值」两列。"""
    table, ids = p["table"], p["id_columns"]
    types = _types(ws, table)
    value_cols = p.get("value_columns") or [c for c in types if c not in ids]
    check_columns(ws, table, value_cols)
    if not value_cols:
        raise FormulaError("没有可以转换的列")
    name_col, val_col = p["name_column"], p["value_column"]
    if name_col in ids or val_col in ids:
        raise FormulaError("name_column / value_column 不能和 id_columns 重名")
    kinds = {types[c] for c in value_cols}
    duck = "BIGINT" if kinds == {"integer"} else "DOUBLE" if kinds <= {"integer", "float"} else "VARCHAR"
    id_sql = _cols(ids)
    parts = [
        f"SELECT {id_sql}, {lit(c)} AS {q(name_col)}, CAST({q(c)} AS {duck}) AS {q(val_col)}, rowid AS __rid, {i} AS __ci FROM {q(table)}"
        for i, c in enumerate(value_cols)
    ]
    where = f" WHERE {q(val_col)} IS NOT NULL" if p["drop_null"] else ""
    sql = f"SELECT * EXCLUDE (__rid, __ci) FROM ({' UNION ALL '.join(parts)}){where} ORDER BY __rid, __ci"
    return FormulaPlan(sql, [table])


def _build_top_n(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    table, by, n = p["table"], p["by"], p["n"]
    order = "DESC" if p["order"] == "desc" else "ASC"
    group = p.get("group_by") or []
    if group:
        sql = (
            "SELECT * EXCLUDE (__rn, __grp, __rid) FROM ("
            f"SELECT *, rowid AS __rid, ROW_NUMBER() OVER (PARTITION BY {_cols(group)} ORDER BY {q(by)} {order} NULLS LAST, rowid) AS __rn, "
            f"MIN(rowid) OVER (PARTITION BY {_cols(group)}) AS __grp FROM {q(table)}) "
            f"WHERE __rn <= {int(n)} ORDER BY __grp, __rn"
        )
    else:
        sql = f"SELECT * FROM {q(table)} ORDER BY {q(by)} {order} NULLS LAST, rowid LIMIT {int(n)}"
    return FormulaPlan(sql, [table])


def _build_append_total(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    table, label_col = p["table"], p["label_column"]
    types = _types(ws, table)
    sums = p.get("sum_columns") or [c for c, t in types.items() if t in {"integer", "float"} and c != label_col]
    check_columns(ws, table, sums)
    if not sums:
        raise FormulaError("没有可以合计的数字列，请用 sum_columns 指定")
    total_exprs = ", ".join(f"{_agg_sql('sum', c, types[c])} AS {q(c)}" for c in sums)
    label = lit(p["label"] or "合计")
    if types[label_col] != "text":
        label = f"CAST({label} AS VARCHAR)"
    sql = (
        "SELECT * EXCLUDE (__ord, __rid) FROM ("
        f"SELECT *, 0 AS __ord, rowid AS __rid FROM {q(table)} "
        "UNION ALL BY NAME "
        f"SELECT {label} AS {q(label_col)}, {total_exprs}, 1 AS __ord, 0 AS __rid FROM {q(table)}"
        ") ORDER BY __ord, __rid"
    )
    return FormulaPlan(sql, [table])


def referenced_tables(ws: Workspace | None, sql: str) -> list[str]:
    """查询引用了哪些表（保留 ws 参数是为了兼容调用方）。"""
    return sql_table_refs(sql)


def _build_custom_sql(p: dict[str, Any], ws: Workspace) -> FormulaPlan:
    sql = assert_single_select(p["sql"])
    inputs = p.get("inputs") or referenced_tables(ws, sql)
    for t in inputs:
        ws.require(t)
    return FormulaPlan(sql, list(inputs))


# ---------------------------------------------------------------------------
# 人话描述（流程图、Recipe 总结里展示）
# ---------------------------------------------------------------------------


class _Blank(dict):
    """format_map 用：缺少的参数显示为空，避免描述函数因为可选参数缺失而报错。"""

    def __missing__(self, key: str) -> str:
        return ""


def _d(text: str) -> Any:
    return lambda p: text.format_map(_Blank({k: _fmt(v) for k, v in p.items()}))


def _fmt(value: Any) -> str:
    if isinstance(value, list):
        return "、".join(_fmt(v) for v in value)
    if isinstance(value, dict):
        return "，".join(f"{k}→{_fmt(v)}" for k, v in value.items())
    return "" if value is None else str(value)


def _describe_group(p: dict[str, Any]) -> str:
    try:
        aggs = _parse_aggregations(p.get("aggregations"))
    except FormulaError:
        aggs = []
    parts = "、".join(
        ("条数" if a["column"] == "*" else f"「{a['column']}」") + _AGG_SUFFIX.get(a["func"], a["func"]) for a in aggs
    )
    by = f"按「{_fmt(p.get('group_by'))}」分组" if p.get("group_by") else "整体"
    return f"{by}汇总：{parts}"


def _describe_lookup(p: dict[str, Any]) -> str:
    text = f"用「{p.get('key')}」去「{p.get('lookup_table')}」匹配，带出「{_fmt(p.get('columns'))}」"
    if p.get("missing_fill"):
        text += f"；匹配不到时填：{_fmt(p.get('missing_fill'))}"
    if p.get("fuzzy_threshold"):
        text += f"；精确匹配不上的按相似度 ≥ {p.get('fuzzy_threshold')} 模糊匹配"
    return text


def _describe_pivot(p: dict[str, Any]) -> str:
    values = f"（{_fmt(p.get('values'))}）" if p.get("values") else ""
    return f"按「{_fmt(p.get('index'))}」把「{p.get('pivot_column')}」拆成列{values}，值取「{p.get('value_column')}」{_AGG_SUFFIX.get(str(p.get('aggfunc') or 'sum'), '')}"


def _describe_sort(p: dict[str, Any]) -> str:
    try:
        items = _parse_sort_items(p.get("by"))
    except FormulaError:
        return "排序"
    return "按 " + "、".join(f"「{c}」{'降序' if o == 'desc' else '升序'}" for c, o in items) + " 排序"


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


def _register() -> dict[str, BuiltinFormula]:
    items = [
        # ---- 清洗整理 ----
        BuiltinFormula(
            _spec("select_columns", "选择列", "清洗整理", "只保留需要的列，并按给定顺序排列",
                  {"table": _T, "columns": {"type": "columns", "required": True, "of": "table", "desc": "要保留的列（按输出顺序）"}},
                  ["保留列", "调整列顺序", "删除其他列"]),
            _build_select, _d("保留并排列列：{columns}"),
        ),
        BuiltinFormula(
            _spec("drop_columns", "删除列", "清洗整理", "删除不需要的列",
                  {"table": _T, "columns": {"type": "columns", "required": True, "of": "table", "desc": "要删除的列"}},
                  ["去掉列", "删除字段"]),
            _build_drop_columns, _d("删除列：{columns}"),
        ),
        BuiltinFormula(
            _spec("rename_columns", "重命名列", "清洗整理", "把列改成新名字",
                  {"table": _T, "mapping": {"type": "dict", "required": True, "desc": "{旧列名: 新列名}"}},
                  ["改列名", "字段改名", "表头改名"]),
            _build_rename, _d("重命名：{mapping}"),
        ),
        BuiltinFormula(
            _spec("filter_rows", "筛选行", "清洗整理", "只保留满足条件的行",
                  {"table": _T, "condition": {"type": "expr", "required": True,
                                              "desc": "DuckDB SQL 条件，列名用双引号，例如 \"状态\" <> '取消' AND \"金额\" > 0"}},
                  ["过滤", "删除行", "排除", "只要"]),
            _build_filter, _d("筛选：{condition}"),
        ),
        BuiltinFormula(
            _spec("drop_duplicates", "去重", "清洗整理", "删除重复行",
                  {"table": _T,
                   "columns": {"type": "columns", "of": "table", "desc": "按哪些列判断重复；不填表示整行完全相同才算重复"},
                   "keep": {"type": "enum", "values": ["first", "last"], "default": "first", "desc": "保留第一条还是最后一条"}},
                  ["重复", "唯一"]),
            _build_dedup, lambda p: f"按「{_fmt(p.get('columns')) or '整行'}」去重，保留{'最后' if p.get('keep') == 'last' else '第'}一条",
        ),
        BuiltinFormula(
            _spec("sort_rows", "排序", "清洗整理", "按一列或多列排序",
                  {"table": _T, "by": {"type": "any", "required": True,
                                       "desc": "排序字段，例如 [{\"column\": \"金额\", \"order\": \"desc\"}, {\"column\": \"日期\"}]",
                                       "schema": {"type": "array", "items": {"type": "object", "properties": {
                                           "column": {"type": "string"}, "order": {"type": "string", "enum": ["asc", "desc"]}},
                                           "required": ["column"]}}}},
                  ["排序", "升序", "降序"]),
            _build_sort, _describe_sort,
        ),
        BuiltinFormula(
            _spec("fill_null", "填充空值", "清洗整理", "把空值填成指定值，或用上一行的值向下填充（合并单元格常见）",
                  {"table": _T,
                   "columns": {"type": "columns", "of": "table", "desc": "要填充的列；不填表示所有列"},
                   "value": {"type": "any", "desc": "填入的值（method=value 时必填；数字也可以写成文本，如 \"0\"）",
                             "schema": {"type": "string"}},
                   "method": {"type": "enum", "values": ["value", "forward"], "default": "value", "desc": "value 固定值 / forward 向下填充"}},
                  ["空值", "补全", "向下填充"]),
            _build_fill_null,
            lambda p: f"「{_fmt(p.get('columns')) or '所有列'}」" + ("用上一行向下填充" if p.get("method") == "forward" else f"的空值填为「{_fmt(p.get('value'))}」"),
        ),
        BuiltinFormula(
            _spec("trim_text", "清理文本", "清洗整理", "去掉文本首尾空格、合并多余空白",
                  {"table": _T, "columns": {"type": "columns", "of": "table", "desc": "要清理的列；不填表示所有文本列"}},
                  ["空格", "清洗"]),
            _build_trim, lambda p: f"清理文本空格：{_fmt(p.get('columns')) or '所有文本列'}",
        ),
        BuiltinFormula(
            _spec("cast_columns", "转换类型", "清洗整理", "把列转换成数字 / 文本 / 日期",
                  {"table": _T, "types": {"type": "dict", "required": True, "desc": "{列名: integer|float|text|date|datetime}"}},
                  ["类型", "转数字", "转文本", "转日期"]),
            _build_cast, _d("转换类型：{types}"),
        ),
        # ---- 计算 ----
        BuiltinFormula(
            _spec("compute_column", "计算列", "计算", "用表达式新增一列或更新已有列",
                  {"table": _T,
                   "column": {"type": "str", "required": True, "desc": "列名（已存在则更新，不存在则新增）"},
                   "expression": {"type": "expr", "required": True,
                                  "desc": "DuckDB SQL 表达式，例如 \"重量\" * 2.5、round(\"金额\" * 1.06, 2)、CASE WHEN … END"},
                   "after": {"type": "str", "desc": "新增列放在哪一列后面；不填放最后"}},
                  ["新增列", "计算", "公式", "乘", "加"]),
            _build_compute, _d("计算「{column}」= {expression}"),
        ),
        BuiltinFormula(
            _spec("map_values", "值映射", "计算", "按对照表替换某列的值（例如简称 → 全称）",
                  {"table": _T,
                   "column": {"type": "column", "required": True, "of": "table", "desc": "要映射的列"},
                   "mapping": {"type": "dict", "required": True, "desc": "{原值: 新值}"},
                   "default": {"type": "any", "desc": "不在映射里的值用什么；不填保持原值", "schema": {"type": "string"}},
                   "target": {"type": "str", "desc": "结果写到哪一列；不填覆盖原列"}},
                  ["替换", "对照", "映射", "转换名称"]),
            _build_map_values, _d("把「{column}」按对照表替换：{mapping}"),
        ),
        BuiltinFormula(
            _spec("split_column", "拆分列", "计算", "按分隔符把一列拆成多列",
                  {"table": _T,
                   "column": {"type": "column", "required": True, "of": "table", "desc": "要拆分的列"},
                   "separator": {"type": "str", "required": True, "desc": "分隔符，例如 - 或 /"},
                   "into": {"type": "list", "required": True, "item_type": "str", "desc": "拆出来的新列名"},
                   "keep_original": {"type": "bool", "default": True, "desc": "是否保留原列"}},
                  ["拆分", "分割"]),
            _build_split, _d("把「{column}」按「{separator}」拆成：{into}"),
        ),
        BuiltinFormula(
            _spec("concat_columns", "合并列", "计算", "把多列文本拼成一列",
                  {"table": _T,
                   "columns": {"type": "columns", "required": True, "of": "table", "desc": "要拼接的列"},
                   "separator": {"type": "str", "default": "", "desc": "连接符"},
                   "column": {"type": "str", "required": True, "desc": "新列名"}},
                  ["拼接", "合并文本"]),
            _build_concat_columns, _d("把 {columns} 拼成「{column}」"),
        ),
        BuiltinFormula(
            _spec("add_serial", "添加序号", "计算", "在最前面加一列从 1 开始的序号",
                  {"table": _T, "column": {"type": "str", "default": "序号", "desc": "序号列名"}},
                  ["序号", "编号", "行号"]),
            _build_serial, _d("添加序号列「{column}」"),
        ),
        # ---- 匹配合并 ----
        BuiltinFormula(
            _spec("lookup", "匹配带出列", "匹配合并",
                  "用关键字段去另一张表匹配，带出需要的列（类似 VLOOKUP）；会检测重复键并报告匹配率",
                  {"table": _T,
                   "lookup_table": {"type": "table", "required": True, "desc": "去哪张表里查"},
                   "key": {"type": "column", "required": True, "of": "table", "desc": "本表的匹配键"},
                   "lookup_key": {"type": "str", "desc": "匹配表里的键列；不填表示和 key 同名"},
                   "columns": {"type": "columns", "required": True, "of": "lookup_table", "desc": "要带出来的列"},
                   "missing_fill": {"type": "dict", "desc": "匹配不到时填的值，例如 {\"归属\": \"新食研\"}"},
                   "duplicates": {"type": "enum", "values": ["first", "error"], "default": "first",
                                  "desc": "匹配表键重复时：first 取第一条 / error 报错拦截"},
                   "normalize_key": {"type": "bool", "default": True, "desc": "匹配前标准化键（去空格、统一大小写）"},
                   "fuzzy_threshold": {"type": "number", "desc": "可选：精确匹配不上的行按相似度模糊匹配，0~1，例如 0.92"},
                   "score_column": {"type": "str", "default": "匹配得分", "desc": "模糊匹配时得分列名"}},
                  ["匹配", "vlookup", "查找", "关联", "带出"],
                  {"table": "订单", "lookup_table": "店铺归属", "key": "店铺", "columns": ["归属"], "missing_fill": {"归属": "新食研"}}),
            _build_lookup, _describe_lookup,
        ),
        BuiltinFormula(
            _spec("join_tables", "合并两表", "匹配合并", "按键把两张表横向合并（inner / left / right / full）",
                  {"left": {"type": "table", "required": True, "desc": "左表"},
                   "right": {"type": "table", "required": True, "desc": "右表"},
                   "left_on": {"type": "columns", "required": True, "of": "left", "desc": "左表的键"},
                   "right_on": {"type": "columns", "of": "right", "desc": "右表的键；不填表示和左表同名"},
                   "how": {"type": "enum", "values": ["inner", "left", "right", "full"], "default": "left", "desc": "合并方式"},
                   "suffix": {"type": "str", "default": "_右", "desc": "重名列的后缀"}},
                  ["合并", "join", "关联"]),
            _build_join, _d("按 {left_on} 合并「{left}」和「{right}」（{how}）"),
        ),
        BuiltinFormula(
            _spec("concat_tables", "纵向合并", "匹配合并", "把多张结构相近的表上下拼接（按列名对齐）",
                  {"tables": {"type": "tables", "required": True, "desc": "要拼接的表（按顺序）"},
                   "source_column": {"type": "str", "desc": "可选：新增一列标记每行来自哪张表"}},
                  ["追加", "上下合并", "汇总多表", "union"]),
            _build_concat_tables, _d("纵向合并：{tables}"),
        ),
        # ---- 汇总统计 ----
        BuiltinFormula(
            _spec("group_aggregate", "分组汇总", "汇总统计", "按字段分组，对数值求和 / 计数 / 平均等（结果按首次出现的顺序）",
                  {"table": _T,
                   "group_by": {"type": "columns", "of": "table", "desc": "分组字段；不填表示整体汇总成一行"},
                   "aggregations": {"type": "any", "required": True,
                                    "desc": "[{\"column\": \"金额\", \"func\": \"sum|avg|count|count_distinct|min|max|first|last|median|concat\", \"as\": \"结果列名\"}]，计数可用 column=\"*\"",
                                    "schema": {"type": "array", "items": {"type": "object", "properties": {
                                        "column": {"type": "string"},
                                        "func": {"type": "string", "enum": ["sum", "avg", "count", "count_distinct", "min", "max", "first", "last", "median", "concat"]},
                                        "as": {"type": "string"}}, "required": ["column", "func"]}}}},
                  ["汇总", "统计", "合计", "分组", "求和", "计数"],
                  {"table": "账单", "group_by": ["客户"], "aggregations": [{"column": "金额", "func": "sum"}, {"column": "*", "func": "count", "as": "票数"}]}),
            _build_group, _describe_group,
        ),
        BuiltinFormula(
            _spec("top_n", "取前 N 名", "汇总统计", "按某列排序取前 N 行，可分组取",
                  {"table": _T,
                   "by": {"type": "column", "required": True, "of": "table", "desc": "排序依据"},
                   "n": {"type": "int", "required": True, "desc": "取几行"},
                   "order": {"type": "enum", "values": ["desc", "asc"], "default": "desc", "desc": "desc 从大到小"},
                   "group_by": {"type": "columns", "of": "table", "desc": "可选：每组各取前 N"}},
                  ["前几", "排名", "top"]),
            _build_top_n, _d("按「{by}」取前 {n} 行"),
        ),
        BuiltinFormula(
            _spec("append_total", "添加合计行", "汇总统计", "在表格最后加一行合计",
                  {"table": _T,
                   "label_column": {"type": "column", "required": True, "of": "table", "desc": "在哪一列写「合计」"},
                   "label": {"type": "str", "default": "合计", "desc": "合计行的标签"},
                   "sum_columns": {"type": "columns", "of": "table", "desc": "要合计的列；不填表示所有数字列"}},
                  ["合计行", "总计"]),
            _build_append_total, _d("在最后添加「{label}」行"),
        ),
        # ---- 结构重塑 ----
        BuiltinFormula(
            _spec("pivot_wide", "长转宽（拆成列）", "结构重塑",
                  "把一列里的类别拆成多列，例如每个运单多行费用 → 每个运单一行、每种费用一列",
                  {"table": _T,
                   "index": {"type": "columns", "required": True, "of": "table", "desc": "每行的主键，例如 [\"运单号\"]"},
                   "pivot_column": {"type": "column", "required": True, "of": "table", "desc": "要拆成列的类别字段，例如 费用类型"},
                   "value_column": {"type": "column", "required": True, "of": "table", "desc": "填进新列的值，例如 金额"},
                   "aggfunc": {"type": "enum", "values": ["sum", "first", "count", "max", "min", "avg"], "default": "sum", "desc": "同一格有多个值时怎么合并"},
                   "carry_columns": {"type": "columns", "of": "table", "desc": "跟随主键带上的列（每个主键取第一行的值）"},
                   "fill_value": {"type": "any", "desc": "没有值的格子填什么，例如 0", "schema": {"type": "number"}},
                   "values": {"type": "list", "desc": "固定要拆出的取值及顺序；不填表示数据里出现的全部取值。固定后重复执行更稳定"},
                   "column_suffix": {"type": "str", "desc": "可选：新列名后缀"}},
                  ["长转宽", "拆列", "透视", "pivot", "一单多行"],
                  {"table": "费用明细", "index": ["运单号"], "pivot_column": "费用类型", "value_column": "金额", "fill_value": 0}),
            _build_pivot_wide, _describe_pivot,
        ),
        BuiltinFormula(
            _spec("unpivot_long", "宽转长", "结构重塑", "把多列转换成「项目 + 数值」两列（逆透视）",
                  {"table": _T,
                   "id_columns": {"type": "columns", "required": True, "of": "table", "desc": "保持不动的标识列"},
                   "value_columns": {"type": "columns", "of": "table", "desc": "要转成行的列；不填表示其余所有列"},
                   "name_column": {"type": "str", "default": "项目", "desc": "存放原列名的新列"},
                   "value_column": {"type": "str", "default": "数值", "desc": "存放值的新列"},
                   "drop_null": {"type": "bool", "default": True, "desc": "是否丢掉空值"}},
                  ["宽转长", "逆透视", "unpivot", "melt"]),
            _build_unpivot, _d("宽转长：保留 {id_columns}，其余列转成「{name_column}」+「{value_column}」"),
        ),
        BuiltinFormula(
            _spec("custom_sql", "自定义处理（SQL）", "自定义",
                  "内置公式表达不了的客户独有逻辑，用一条 DuckDB SELECT 实现",
                  {"sql": {"type": "sql", "required": True, "desc": "一条 SELECT，表名和列名用双引号"},
                   "inputs": {"type": "tables", "desc": "用到的表（可不填，自动识别）"}},
                  ["自定义", "sql", "特殊规则"]),
            _build_custom_sql, lambda p: "自定义处理（SQL）",
        ),
    ]
    return {item.spec.id: item for item in items}


BUILTIN_FORMULAS: dict[str, BuiltinFormula] = _register()
