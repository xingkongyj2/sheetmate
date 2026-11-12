"""analysis / builtin tool capabilities."""

from __future__ import annotations

import json
from typing import Any
import pandas as pd
from sheetmate.agent.tools.sql_scope import checked_sql_tables
from sheetmate.engine.formulas.lineage import compile_sql_row_lineage, create_table_with_optional_lineage
from sheetmate.engine.tables.analysis import column_stats, table_info, table_info_text, value_counts
from sheetmate.engine.tables.sql import assert_single_select, frame_to_text
from sheetmate.agent.tools.contracts import AgentContext, ToolResult
from sheetmate.agent.tools.presentation import (
    _assert_table_scope,
    _clip,
    _result_name,
    _table_detail,
    OBSERVATION_MAX_ROWS,
    ephemeral_payload,
    public_detail_section,
    render_query_observation,
    render_table_observation,
    table_payload,
)


def _tool_table_info(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    table = str(args.get("table", ""))
    _assert_table_scope(ctx, [table])
    data = table_info(ctx.ws, table)
    columns = [f"「{col['name']}」：{col['dtype']}，空值 {col['null_rate']:.0%}，{col['unique']} 个不同值" for col in data["columns"]]
    keys = data.get("keys", {})
    checks = [f"「{name}」非空且没有重复值，可作为候选主键。" for name in keys.get("candidates", [])]
    checks.extend(f"「{item['column']}」有 {item['duplicate_values']} 个重复值，单值最多出现 {item['max_repeat']} 次。" for item in keys.get("duplicated", []))
    return ToolResult(table_info_text(data), summary=f"「{data['name']}」{data['rows']} 行 × {data['cols']} 列",
                      public_details=[public_detail_section("查看对象", [_table_detail(ctx.ws.require(data["name"]))]),
                                      public_detail_section("字段检查", columns),
                                      *([public_detail_section("主键检查", checks)] if checks else [])])


def _tool_value_counts(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    table, column = str(args.get("table", "")), str(args.get("column", ""))
    _assert_table_scope(ctx, [table])
    df = value_counts(ctx.ws, table, column, int(args.get("limit") or 20))
    distribution = df.attrs.get("distribution") or {}
    total = distribution.get("total_categories")
    omitted = distribution.get("omitted_categories")
    coverage = distribution.get("coverage")
    detail = (f"共 {total} 种取值，返回前 {len(df)} 种，省略 {omitted} 种；覆盖 "
              f"{distribution.get('covered_rows')}/{distribution.get('total_rows')} 行（{coverage:.2%}）"
              if total is not None and coverage is not None else f"返回前 {len(df)} 种取值，未确认全部类别覆盖率")
    return ToolResult(_clip(f"「{table}.{column}」取值分布：{detail}。\n{frame_to_text(df, limit=30)}"),
                      summary=f"「{column}」{detail}",
                      tables=[ephemeral_payload(f"{column} 分布", df)],
                      public_details=[public_detail_section("统计对象", [f"「{table}」的「{column}」字段"]),
                                      public_detail_section("统计范围", [detail]),
                                      public_detail_section("统计结果", [f"{row.iloc[0]}：{row.iloc[1]} 条，占比 {row.iloc[2]}%" for _, row in df.head(100).iterrows()])])


def _tool_column_stats(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    table = str(args.get("table", ""))
    _assert_table_scope(ctx, [table])
    df = column_stats(ctx.ws, table, args.get("columns") or None)
    statistics = [f"「{row['列']}」：" + "，".join(f"{key} {row[key]}" for key in ("最小", "最大", "均值", "约不同值", "空值比例") if key in row and not pd.isna(row[key])) for _, row in df.iterrows()]
    return ToolResult(_clip(f"「{table}」描述统计：\n{frame_to_text(df, limit=40, max_cell=24)}"),
                      summary=f"「{table}」{len(df)} 列的统计", tables=[ephemeral_payload(f"{table} 统计", df)],
                      public_details=[public_detail_section("统计对象", [_table_detail(ctx.ws.require(table))]),
                                      public_detail_section("检查字段", args.get("columns") or ctx.ws.require(table).column_names),
                                      public_detail_section("统计结果", statistics)])


def _tool_preview(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    table = str(args.get("table", ""))
    _assert_table_scope(ctx, [table])
    offset, limit = args.get("offset", 0), args.get("limit", OBSERVATION_MAX_ROWS)
    text = render_table_observation(ctx.ws, table, offset=offset, limit=limit,
                                    expected_revision=args.get("expected_revision"), text_offset=args.get("text_offset", 0))
    observation = json.loads(text)
    count = observation["returned_rows"]
    fragment = observation["encoding"] == "json_fragment"
    return ToolResult(text, summary=f"查看「{table}」数据片段" if fragment else f"查看「{table}」{count} 行",
                      public_details=[public_detail_section("查看对象", [_table_detail(ctx.ws.require(table))]),
                                      public_detail_section("读取范围", ["当前行过长，已按文本分页读取。" if fragment else
                                          f"第 {offset + 1}～{offset + count} 行，共读取 {count} 行。" if count else
                                          f"从第 {offset + 1} 行开始读取，未读到记录。"])])


def _tool_run_sql(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    sql = assert_single_select(str(args.get("sql", "")))
    references = checked_sql_tables(ctx, sql)
    _assert_table_scope(ctx, references)
    inputs = [ctx.ws.require(name) for name in references]
    for info in inputs:
        if info.read_quality.get("truncated"):
            raise ValueError(f"表「{info.name}」仅载入部分数据，不能作为完整查询结果；请先从原件分块读取完整数据。")
        if any(info.read_quality.get(key) for key in ("missing_formula_cache", "error_cells", "formula_error_cells")):
            raise ValueError(f"表「{info.name}」存在缺失公式缓存或错误单元格，不能把未确认的值当成完整数据计算；请用原件脚本核对公式或重算后再分析。")
    details = [public_detail_section("查询对象", [_table_detail(info) for info in inputs])]
    if args.get("description"):
        details.append(public_detail_section("执行内容", [args["description"]]))
    result_name = args.get("result_name")
    if result_name:
        # Execute once, then use the stored row count and revision on all pages.
        with ctx.ws.locked():
            out = _result_name(ctx, result_name, "查询结果")
            lineage = compile_sql_row_lineage(ctx.ws, sql, references[0]) if len(set(references)) == 1 else None
            create_table_with_optional_lineage(
                ctx.ws, out, sql, lineage, label=str(result_name), source="computed", role="derived", created_by="run_sql",
                description=str(args.get("description") or "SQL 查询结果"), step={"formula": "custom_sql", "params": {"sql": sql}},
            )
            if ctx.table_filter is not None:
                ctx.table_filter.add(out)
            info = ctx.ws.require(out)
            text = render_table_observation(ctx.ws, out, expected_revision=info.revision)
            payload = table_payload(ctx.ws, out)
        return ToolResult(text, summary=f"生成「{out}」{info.rows} 行", tables=[payload],
                          public_details=[section for section in [*details,
                              public_detail_section("查询结果", [f"生成「{out}」：{info.rows} 行 × {len(info.columns)} 列。"]),
                              public_detail_section("结果字段", info.column_names)] if section["items"]])
    # One lookahead row distinguishes a known total from a bounded query. Do
    # not materialize unnamed reads or report the read cap as a true total.
    limit = 500
    df = ctx.ws.query(sql, limit=limit + 1)
    text = render_query_observation(df, sql=sql, result_name=_result_name(ctx, None, "查询结果"), row_limit=limit)
    count = str(len(df)) if len(df) <= limit else f"至少 {len(df)}"
    return ToolResult(text, summary=f"查询返回 {count} 行",
                      tables=[ephemeral_payload("查询结果", df)] if len(df) <= limit else [],
                      public_details=[section for section in [*details,
                          public_detail_section("查询结果", [f"查询得到 {count} 行；需要完整续读时提供结果名保存查询。"]),
                          public_detail_section("结果字段", [str(column) for column in df.columns])] if section["items"]])
