"""formulas / builtin tool capabilities."""

from __future__ import annotations

import json
from typing import Any
import pandas as pd
from sheetmate.engine.formulas import Formula, ParamSpec
from sheetmate.engine.formulas.runner import apply_formula
from sheetmate.engine.tables.sql import frame_to_text
from sheetmate.agent.tools.contracts import AgentContext, Tool, ToolResult, _obj
from sheetmate.agent.tools.presentation import (
    _assert_table_scope,
    _clip,
    _result_name,
    _table_detail,
    public_detail_section,
    table_payload,
)


def param_schema(p: ParamSpec) -> dict[str, Any]:
    if p.schema_:
        return {**p.schema_, "description": p.desc}
    schema: dict[str, Any] = {"description": p.desc}
    if p.type in {"table", "column", "str", "expr", "sql"}:
        schema["type"] = "string"
    elif p.type in {"tables", "columns"}:
        schema.update(type="array", items={"type": "string"})
    elif p.type == "int":
        schema["type"] = "integer"
    elif p.type == "number":
        schema["type"] = "number"
    elif p.type == "bool":
        schema["type"] = "boolean"
    elif p.type == "enum":
        schema.update(type="string", enum=[str(v) for v in (p.values or [])])
    elif p.type == "list":
        schema.update(type="array", items={"type": "string"})
    elif p.type == "dict":
        schema["type"] = "object"
    return schema


def formula_tool(spec: Formula, kind: str) -> Tool:
    properties = {name: param_schema(p) for name, p in spec.params.items()}
    properties["result_name"] = {"type": "string", "description": "结果表名（简短中文），后续可以引用"}
    required = [name for name, p in spec.params.items() if p.required]
    label_kind = "客户独有公式" if kind == "customer" else "内置公式"
    description = f"［{label_kind}·{spec.category}］{spec.name}：{spec.intent}。结果会保存为一张新表。"
    if spec.example:
        description += f" 示例参数：{json.dumps(spec.example, ensure_ascii=False)}"
    fid = spec.id

    def run(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
        params = {k: v for k, v in args.items() if k != "result_name"}
        resolved = ctx.library.resolve(fid)
        table_params = [
            value
            for name, spec_param in resolved.spec.params.items()
            if spec_param.type in {"table", "tables"}
            for value in (params.get(name) if isinstance(params.get(name), list) else [params.get(name)])
            if isinstance(value, str) and value.strip()
        ]
        _assert_table_scope(ctx, table_params)
        first_table = next((params.get(n) for n, p in resolved.spec.params.items() if p.type == "table" and params.get(n)), "")
        out = _result_name(ctx, args.get("result_name"), f"{resolved.spec.name}_{first_table}" if first_table else resolved.spec.name)
        outcome = apply_formula(ctx.ws, ctx.library, fid, params, out, label=str(args.get("result_name") or out), created_by=fid)
        ctx.ws.update_meta(out, role="derived", step={"formula": fid, "params": outcome.params})
        if ctx.table_filter is not None:
            ctx.table_filter.add(out)
        preview = pd.DataFrame(outcome.preview["rows"][:5], columns=outcome.columns_out)
        text = f"{outcome.summary()}\n前 5 行：\n{frame_to_text(preview, limit=5)}"
        details = [public_detail_section("处理对象", [_table_detail(ctx.ws.require(name)) for name in outcome.inputs])]
        if fid == "rename_columns":
            details.append(public_detail_section("字段变化", [f"「{old}」 → 「{new}」" for old, new in outcome.params.get("mapping", {}).items()]))
        elif fid == "cast_columns":
            info = ctx.ws.require(str(outcome.params["table"]))
            before = {column["name"]: column["type"] for column in info.columns}
            details.append(public_detail_section("类型变化", [f"「{column}」：{before.get(column, '未记录原类型')} → {kind}" for column, kind in outcome.params.get("types", {}).items()]))
        elif fid == "map_values":
            column = outcome.params.get("column", "")
            target = outcome.params.get("target") or column
            details.append(public_detail_section("值的变化", [f"「{column}」的 {old} → 「{target}」的 {new}" for old, new in outcome.params.get("mapping", {}).items()]))
        else:
            details.append(public_detail_section("执行内容", [outcome.description]))
        details.extend([public_detail_section("数据变化", [f"「{name}」{rows} 行 → 「{out}」{outcome.rows_out} 行" for name, rows in outcome.rows_in.items()]),
                        public_detail_section("结果字段", outcome.columns_out)])
        if outcome.metrics.get("match_rate") is not None:
            details.append(public_detail_section("匹配结果", [f"匹配 {outcome.metrics['matched_rows']}/{outcome.metrics['total_rows']} 行，匹配率 {outcome.metrics['match_rate']:.1%}。"] ))
        if outcome.warnings:
            details.append(public_detail_section("核对提示", outcome.warnings))
        return ToolResult(_clip(text), summary=f"{outcome.description} → 「{out}」{outcome.rows_out} 行",
                          tables=[table_payload(ctx.ws, out)], public_details=[section for section in details if section["items"]])

    def label(args: dict[str, Any]) -> str:
        table = args.get("table") or args.get("left") or ", ".join(args.get("tables") or [])
        return f"{spec.name} · {table}" if table else spec.name

    return Tool(fid, description, _obj(properties, required), frozenset({"analyze", "modify", "create"}),
                run=run, label=label, process_safe=True)
