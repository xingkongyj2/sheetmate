"""Agent 工具（对应 TabClaw 的 skills/registry.py + builtin.py，执行改为 DuckDB 引擎）。

工具分组：
- 只读分析：table_info / value_counts / column_stats / preview_table / run_sql
- 公式工具：每个公式自动生成一个工具（内置公式 + 客户独有公式），结果存成新表（仅分析模式）
- 文件：list_files / load_file / export_excel
- Recipe：build_recipe / revise_recipe / run_recipe（修改模式）、save_as_recipe / get_recipe
- 记忆：remember / save_field_mapping
- 澄清：ask_user（提问后结束本轮，等用户回答）

工具的「宿主」能力（读写数据库、登记文件、保存 Recipe）通过 ToolHost 协议由服务层提供，
这样 Agent 层不依赖数据库，测试时也可以换成假的宿主。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import pandas as pd

from sheetmate.engine.analysis import column_stats, table_info, table_info_text, value_counts
from sheetmate.engine.formulas import Formula, FormulaLibrary, ParamSpec
from sheetmate.engine.runner import apply_formula
from sheetmate.engine.sqlutil import assert_single_select, frame_rows, frame_to_text, sanitize_table_name
from sheetmate.engine.workspace import TableInfo, Workspace
from sheetmate.recipe.compiler import compile_from_workspace
from sheetmate.recipe.executor import InputSource, RecipeRunner, has_reviewable_output
from sheetmate.recipe.learner import InputMeta, LearnContext
from sheetmate.recipe.matcher import Candidate, match_inputs
from sheetmate.recipe.pipeline import PipelineHost, RecipePipeline
from sheetmate.recipe.schema import Recipe

MAX_TOOL_TEXT = 6000
PREVIEW_ROWS = 50
RUN_STATUS_LABELS = {"success": "成功", "warning": "完成（有提示）", "failed": "失败", "escalated": "需要人工处理", "running": "运行中"}


def _status_label(status: str) -> str:
    return RUN_STATUS_LABELS.get(status, status)


# ---------------------------------------------------------------------------
# 基础结构
# ---------------------------------------------------------------------------


@dataclass
class ToolResult:
    text: str  # 回传给 LLM 的结果
    ok: bool = True
    summary: str = ""  # 界面上显示的一句话
    tables: list[dict[str, Any]] = field(default_factory=list)  # 表格预览
    events: list[dict[str, Any]] = field(default_factory=list)  # 额外事件（文件、Recipe 保存、记忆…）
    end_turn: bool = False  # ask_user 之后结束本轮


class ToolHost(Protocol):
    """服务层提供给工具的能力。"""

    def list_files(self) -> list[dict[str, Any]]: ...
    def load_file(self, file_id: str) -> list[TableInfo]: ...
    def export_excel(self, sheets: list[tuple[str, str]], filename: str) -> dict[str, Any]: ...
    def list_recipes(self) -> list[dict[str, Any]]: ...
    def get_recipe(self, recipe_id: str) -> Recipe | None: ...
    def latest_conversation_recipe(self) -> Recipe | None: ...
    def store_recipe(self, recipe: Recipe, *, note: str = "") -> dict[str, Any]: ...
    def pipeline_host(self) -> PipelineHost: ...
    def field_mapping(self) -> dict[str, list[str]]: ...
    def memory_text(self, query: str) -> str: ...
    def remember(self, category: str, key: str, value: str) -> str: ...
    def save_field_mapping(self, standard: str, aliases: list[str]) -> str: ...


@dataclass
class AgentContext:
    """一次对话请求的上下文。"""

    mode: str  # analyze / modify
    ws: Workspace
    library: FormulaLibrary
    host: ToolHost
    llm: Any
    conversation_id: str
    customer: dict[str, Any] | None = None  # {id, name, summary}
    max_repair: int = 3
    verify_rounds: int = 2
    table_filter: set[str] | None = None  # 多表并行时，每个专员只看自己的表

    def visible_tables(self) -> list[TableInfo]:
        tables = self.ws.list_tables()
        if self.table_filter is not None:
            return [t for t in tables if t.name in self.table_filter or t.source == "computed"]
        return tables


StreamFn = Callable[[dict[str, Any], AgentContext], AsyncIterator[Any]]
RunFn = Callable[[dict[str, Any], AgentContext], ToolResult]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    modes: frozenset[str] = frozenset({"analyze", "modify"})
    run: RunFn | None = None  # 同步工具（在线程里执行）
    stream: StreamFn | None = None  # 流式工具：先产出事件，最后产出 ToolResult
    label: Callable[[dict[str, Any]], str] | None = None
    needs_customer: bool = False

    def definition(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}

    def display(self, args: dict[str, Any]) -> str:
        try:
            return self.label(args) if self.label else self.name
        except Exception:
            return self.name


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


# ---------------------------------------------------------------------------
# 表格预览
# ---------------------------------------------------------------------------


def table_payload(ws: Workspace, name: str) -> dict[str, Any]:
    info = ws.require(name)
    page = ws.page(name, 0, PREVIEW_ROWS)
    return {
        "name": info.name, "label": info.label, "rows": info.rows, "cols": len(info.columns),
        "columns": info.column_names, "preview": page["rows"], "source": info.source, "role": info.role,
        "description": info.description,
    }


def ephemeral_payload(label: str, df: pd.DataFrame, total: int | None = None) -> dict[str, Any]:
    return {
        "name": None, "label": label, "rows": int(total if total is not None else len(df)), "cols": len(df.columns),
        "columns": [str(c) for c in df.columns], "preview": frame_rows(df, PREVIEW_ROWS), "source": "query",
        "role": "query", "ephemeral": True,
    }


def _result_name(ctx: AgentContext, requested: str | None, default: str) -> str:
    """结果表命名：覆盖之前分析算出来的同名表；不覆盖上传的原始表和期望结果。"""
    base = sanitize_table_name(requested or default)
    info = ctx.ws.info(base)
    if info is None or (info.source == "computed" and info.role == "derived"):
        return base
    return ctx.ws.unique_name(base)


def _clip(text: str) -> str:
    return text if len(text) <= MAX_TOOL_TEXT else text[:MAX_TOOL_TEXT] + "\n…（内容过长已截断）"


# ---------------------------------------------------------------------------
# 只读分析工具
# ---------------------------------------------------------------------------


def _tool_table_info(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    data = table_info(ctx.ws, str(args.get("table", "")))
    return ToolResult(_clip(table_info_text(data)), summary=f"「{data['name']}」{data['rows']} 行 × {data['cols']} 列")


def _tool_value_counts(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    table, column = str(args.get("table", "")), str(args.get("column", ""))
    df = value_counts(ctx.ws, table, column, int(args.get("limit") or 20))
    return ToolResult(_clip(f"「{table}.{column}」取值分布：\n{frame_to_text(df, limit=30)}"),
                      summary=f"「{column}」有 {len(df)} 种取值（前 {len(df)}）",
                      tables=[ephemeral_payload(f"{column} 分布", df)])


def _tool_column_stats(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    table = str(args.get("table", ""))
    df = column_stats(ctx.ws, table, args.get("columns") or None)
    return ToolResult(_clip(f"「{table}」描述统计：\n{frame_to_text(df, limit=40, max_cell=24)}"),
                      summary=f"「{table}」{len(df)} 列的统计", tables=[ephemeral_payload(f"{table} 统计", df)])


def _tool_preview(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    table = str(args.get("table", ""))
    offset, limit = int(args.get("offset") or 0), min(int(args.get("limit") or 20), 100)
    page = ctx.ws.page(table, offset, limit)
    df = pd.DataFrame(page["rows"], columns=[c["name"] for c in page["columns"]])
    return ToolResult(_clip(f"「{table}」第 {offset + 1}~{offset + len(df)} 行（共 {page['total']} 行）：\n{frame_to_text(df, limit=limit)}"),
                      summary=f"查看「{table}」{len(df)} 行")


def _tool_run_sql(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    sql = assert_single_select(str(args.get("sql", "")))
    result_name = args.get("result_name") if ctx.mode == "analyze" else None
    if result_name:
        out = _result_name(ctx, result_name, "查询结果")
        ctx.ws.create_table_as(
            out, sql, label=str(result_name), source="computed", role="derived", created_by="run_sql",
            description=str(args.get("description") or "SQL 查询结果"), step={"formula": "custom_sql", "params": {"sql": sql}},
        )
        df = ctx.ws.df(out, limit=10)
        info = ctx.ws.require(out)
        return ToolResult(_clip(f"已生成表「{out}」：{info.rows} 行 × {len(info.columns)} 列。前几行：\n{frame_to_text(df, limit=10)}"),
                          summary=f"生成「{out}」{info.rows} 行", tables=[table_payload(ctx.ws, out)])
    df = ctx.ws.query(sql, limit=500)
    return ToolResult(_clip(f"查询结果（{len(df)} 行{'，最多显示 500 行' if len(df) == 500 else ''}）：\n{frame_to_text(df, limit=40)}"),
                      summary=f"查询返回 {len(df)} 行", tables=[ephemeral_payload("查询结果", df)])


# ---------------------------------------------------------------------------
# 公式工具（由公式库自动生成）
# ---------------------------------------------------------------------------


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
        first_table = next((params.get(n) for n, p in resolved.spec.params.items() if p.type == "table" and params.get(n)), "")
        out = _result_name(ctx, args.get("result_name"), f"{resolved.spec.name}_{first_table}" if first_table else resolved.spec.name)
        outcome = apply_formula(ctx.ws, ctx.library, fid, params, out, label=str(args.get("result_name") or out), created_by=fid)
        ctx.ws.update_meta(out, role="derived", step={"formula": fid, "params": outcome.params})
        preview = pd.DataFrame(outcome.preview["rows"][:5], columns=outcome.columns_out)
        text = f"{outcome.summary()}\n前 5 行：\n{frame_to_text(preview, limit=5)}"
        return ToolResult(_clip(text), summary=f"{outcome.description} → 「{out}」{outcome.rows_out} 行",
                          tables=[table_payload(ctx.ws, out)])

    def label(args: dict[str, Any]) -> str:
        table = args.get("table") or args.get("left") or ", ".join(args.get("tables") or [])
        return f"{spec.name} · {table}" if table else spec.name

    return Tool(fid, description, _obj(properties, required), frozenset({"analyze"}), run=run, label=label)


# ---------------------------------------------------------------------------
# 文件工具
# ---------------------------------------------------------------------------


def _tool_list_files(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    files = ctx.host.list_files()
    if not files:
        return ToolResult("没有可加载的文件。", summary="没有文件")
    lines = [
        f"- file_id={f['file_id']}「{f['filename']}」（{f.get('scope_label', '')}，{f.get('kind_label', '')}）"
        + (f" 已加载为表：{f['tables']}" if f.get("tables") else " 未加载")
        for f in files
    ]
    return ToolResult("\n".join(lines), summary=f"{len(files)} 个文件")


def _tool_load_file(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    infos = ctx.host.load_file(str(args.get("file_id", "")))
    names = [f"「{t.name}」{t.rows} 行 × {len(t.columns)} 列" for t in infos]
    return ToolResult("已加载：" + "、".join(names) + "。可以用 table_info 查看结构。", summary="加载了 " + "、".join(t.name for t in infos),
                      tables=[table_payload(ctx.ws, t.name) for t in infos])


def _tool_export_excel(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    sheets_raw = args.get("sheets") or []
    sheets: list[tuple[str, str]] = []
    for item in sheets_raw:
        if isinstance(item, dict):
            table = str(item.get("table") or "")
            sheets.append((table, str(item.get("sheet_name") or table)))
        else:
            sheets.append((str(item), str(item)))
    if not sheets:
        raise ValueError("sheets 至少要有一张表，例如 [{\"table\": \"汇总\", \"sheet_name\": \"汇总\"}]")
    info = ctx.host.export_excel(sheets, str(args.get("filename") or "导出结果"))
    return ToolResult(f"已导出文件「{info['filename']}」，包含 {len(sheets)} 个 sheet，用户可以在右侧下载。",
                      summary=f"导出「{info['filename']}」", events=[{"type": "file", **info}])


# ---------------------------------------------------------------------------
# Recipe 工具
# ---------------------------------------------------------------------------


def _table_meta(info: TableInfo) -> InputMeta:
    return InputMeta(filename=info.filename or info.label, sheet=info.sheet, header=info.header, file_id=info.file_id)


def _learn_context(ctx: AgentContext, requirement: str, inputs: dict[str, str], expected_tables: list[str],
                   recipe_name: str | None = None, mode: str = "learn") -> LearnContext:
    frames, meta = {}, {}
    for alias, table in inputs.items():
        info = ctx.ws.require(table)
        frames[alias] = ctx.ws.df(table)
        meta[alias] = _table_meta(info)
    expected, sources = {}, []
    for table in expected_tables:
        info = ctx.ws.require(table)
        sheet = info.sheet or table
        expected[sheet if sheet not in expected else table] = ctx.ws.df(table)
        if info.filename:
            sources.append(info.filename)
    return LearnContext(
        requirement=requirement, library=ctx.library, input_frames=frames, input_meta=meta,
        expected=expected, expected_source="、".join(dict.fromkeys(sources)),
        memory_text=ctx.host.memory_text(requirement), field_mapping=ctx.host.field_mapping(),
        customer_id=(ctx.customer or {}).get("id"), conversation_id=ctx.conversation_id, recipe_name=recipe_name,
        mode=mode,
    )


def _default_inputs(ctx: AgentContext) -> list[str]:
    return [t.name for t in ctx.ws.list_tables() if t.source == "uploaded" and t.role == "input"]


def _default_expected(ctx: AgentContext) -> list[str]:
    return [t.name for t in ctx.ws.list_tables() if t.role == "expected"]


def _copy_results(ctx: AgentContext, outputs: dict[str, pd.DataFrame], recipe: Recipe) -> list[dict[str, Any]]:
    """把流程产出的结果表放回会话工作区，方便继续追问和查看。同一个流程再次执行时覆盖上一次的结果表。"""
    payloads = []
    owner = f"recipe:{recipe.id}"
    for sheet, df in outputs.items():
        name = sanitize_table_name(f"结果_{sheet}")
        info = ctx.ws.info(name)
        if info is not None and not (info.role == "result" and info.created_by == owner):
            name = ctx.ws.unique_name(name)
        ctx.ws.put_dataframe(name, df, label=f"流程结果 · {sheet}", source="computed", role="result",
                             created_by=owner, description=f"「{recipe.name}」v{recipe.version} 的输出")
        payloads.append(table_payload(ctx.ws, name))
    return payloads


def _recipe_result_text(result: dict[str, Any]) -> str:
    if not result:
        return "流程没有完成。"
    recipe: Recipe = result["recipe"]
    if result.get("error"):
        return f"流程生成失败：{result['error']}"
    lines = [f"流程「{recipe.name}」v{recipe.version}（recipe_id={recipe.id}）：执行状态 {_status_label(result['report']['status'])}。"]
    by_id = {s["step_id"]: s for s in result["report"]["steps"]}
    for step in recipe.ordered_steps():
        rep = by_id.get(step.id, {})
        kind = "客户独有" if step.formula_kind == "customer" or step.formula == "custom_sql" else "内置公式"
        rows = f"，结果 {rep['rows_out']} 行" if rep.get("rows_out") is not None else ""
        status = {"repaired": "（自动修复过）", "failed": "（失败）", "warning": "（有提示）"}.get(rep.get("status", ""), "")
        lines.append(f"{step.id}. {step.name}［{step.formula}·{kind}］{step.description}{rows}{status}")
        for w in rep.get("warnings", [])[:2]:
            lines.append(f"   提示：{w}")
    lines.append(f"执行摘要：{result.get('summary', '')}")
    if result.get("verification"):
        lines.append(f"与人工结果比对：{result['verification']['summary']}")
    if result.get("file"):
        sheets = "；".join(f"「{x['sheet']}」{x['rows']} 行 × {len(x.get('columns') or [])} 列（{'、'.join(x.get('columns') or [])}）"
                          for x in result["report"].get("outputs", []))
        lines.append(f"输出文件：「{result['file']['filename']}」（右侧可下载），包含 {sheets}")
    questions = recipe.metadata.get("questions") or []
    if questions:
        lines.append("需要和用户确认：" + "；".join(q["question"] for q in questions[:3]))
    if result["report"].get("escalation"):
        lines.append("错误报告：\n" + result["report"]["escalation"])
    return "\n".join(lines)


async def _stream_build_recipe(args: dict[str, Any], ctx: AgentContext) -> AsyncIterator[Any]:
    requirement = str(args.get("requirement") or "").strip()
    if not requirement:
        raise ValueError("requirement 不能为空：请把用户的处理需求写成清楚的处理说明")
    input_tables = [str(t) for t in (args.get("input_tables") or _default_inputs(ctx))]
    expected_tables = [str(t) for t in (args.get("expected_tables") or _default_expected(ctx))]
    expected_tables = [t for t in expected_tables if t not in input_tables]
    if not input_tables:
        raise ValueError("没有输入表：请先上传要处理的文件")
    lctx = _learn_context(ctx, requirement, {t: t for t in input_tables}, expected_tables, args.get("recipe_name"))
    pipeline = RecipePipeline(ctx.llm, ctx.library, ctx.host.pipeline_host(), max_rounds=ctx.verify_rounds, max_repair=ctx.max_repair)
    async for event in pipeline.build(lctx):
        yield event
    yield _finish_recipe_tool(ctx, pipeline.result)


async def _stream_revise_recipe(args: dict[str, Any], ctx: AgentContext) -> AsyncIterator[Any]:
    instruction = str(args.get("instruction") or "").strip()
    if not instruction:
        raise ValueError("instruction 不能为空")
    recipe = ctx.host.get_recipe(str(args["recipe_id"])) if args.get("recipe_id") else ctx.host.latest_conversation_recipe()
    if recipe is None:
        raise ValueError("没有找到要修订的流程。请先用 build_recipe 生成，或提供 recipe_id")
    mapping = _resolve_inputs(ctx, recipe, args.get("inputs"))
    expected_tables = [str(t) for t in (args.get("expected_tables") or _default_expected(ctx))]
    requirement = (recipe.metadata.get("requirement") or recipe.description or "") + f"\n修改要求：{instruction}"
    lctx = _learn_context(ctx, requirement, mapping, expected_tables, mode="revise")
    pipeline = RecipePipeline(ctx.llm, ctx.library, ctx.host.pipeline_host(), max_rounds=ctx.verify_rounds, max_repair=ctx.max_repair)
    async for event in pipeline.build(lctx, base=recipe, instruction=instruction):
        yield event
    yield _finish_recipe_tool(ctx, pipeline.result)


def _finish_recipe_tool(ctx: AgentContext, result: dict[str, Any]) -> ToolResult:
    tables = []
    if result.get("ok") and result.get("outputs"):
        tables = _copy_results(ctx, result["outputs"], result["recipe"])
    ok = bool(result.get("ok"))
    recipe = result.get("recipe")
    summary = (f"流程「{recipe.name}」v{recipe.version}：{_status_label(result['report']['status'])}" if recipe and result.get("report")
               else result.get("error", "流程未完成"))
    return ToolResult(_clip(_recipe_result_text(result) + _result_tables_hint(tables)), ok=ok, summary=summary, tables=tables)


def _result_tables_hint(tables: list[dict[str, Any]]) -> str:
    """告诉模型流程结果在会话工作区里的表名（流程中间表在独立的运行工作区里，会话里查不到）。"""
    if not tables:
        return ""
    names = "、".join(f"「{t['name']}」（{t['rows']} 行）" for t in tables)
    return f"\n结果已放进当前工作区：{names}，可以直接用这些表名继续查询或分析。"


def _resolve_inputs(ctx: AgentContext, recipe: Recipe, explicit: Any) -> dict[str, str]:
    """Recipe 输入 → 工作区里的表。用户指定的优先，其余按列匹配。"""
    mapping: dict[str, str] = {}
    if isinstance(explicit, dict):
        mapping = {str(k): str(v) for k, v in explicit.items() if v}
    pending = [i for i in recipe.inputs if i.alias not in mapping]
    if pending:
        candidates = [Candidate(key=t.name, columns=t.column_names, filename=t.filename, sheet=t.sheet, rows=t.rows)
                      for t in ctx.ws.list_tables() if t.source == "uploaded" and t.name not in mapping.values()]
        partial = Recipe(name=recipe.name, inputs=pending)
        for alias, hit in match_inputs(partial, candidates, ctx.host.field_mapping()).items():
            mapping[alias] = hit["key"]
    missing = [i.alias for i in recipe.inputs if i.alias not in mapping]
    if missing:
        tables = "、".join(t.name for t in ctx.ws.list_tables() if t.source == "uploaded") or "（没有）"
        raise ValueError(f"找不到流程需要的输入 {missing}。工作区里的表：{tables}。请用 inputs 参数指定 {{输入名: 表名}}，或先上传对应文件")
    for table in mapping.values():
        ctx.ws.require(table)
    return mapping


async def _stream_run_recipe(args: dict[str, Any], ctx: AgentContext) -> AsyncIterator[Any]:
    recipe_id = str(args.get("recipe_id") or "")
    recipe = ctx.host.get_recipe(recipe_id)
    if recipe is None:
        raise ValueError(f"没有找到流程 {recipe_id}")
    mapping = _resolve_inputs(ctx, recipe, args.get("inputs"))
    host = ctx.host.pipeline_host()
    handle = host.open_run("replay", recipe, f"在对话中执行「{recipe.name}」")
    from sheetmate.agent.repair import LLMStepRepairer

    repairer = LLMStepRepairer(ctx.llm) if args.get("allow_repair", True) else None
    runner = RecipeRunner(recipe, ctx.library, handle.ws, field_mapping=ctx.host.field_mapping(),
                          repairer=repairer, max_repair=ctx.max_repair, run_id=handle.run_id)
    sources = {}
    for alias, table in mapping.items():
        info = ctx.ws.require(table)
        sources[alias] = InputSource(alias=alias, df=ctx.ws.df(table), filename=info.filename or info.label,
                                     sheet=info.sheet, file_id=info.file_id)
    async for event in runner.run(sources, output_path=handle.output_path, history=host.history_metrics(recipe.id)):
        yield event
    file_info = host.finish_run(handle, runner.recipe, runner.report)
    if file_info:
        yield {"type": "file", **file_info}
    note = ""
    if runner.recipe_changed:
        saved = ctx.host.store_recipe(runner.recipe, note="执行时自动修复")
        yield {"type": "recipe_saved", **saved, "flow": runner.recipe.flow()}
        note = f"执行中自动修复了第 {runner.report['repaired_steps']} 步，已保存为 v{saved['version']}。"
    ok = runner.report["status"] not in {"failed", "escalated"}
    # 只是运行校验没通过时，结果表也放回工作区，方便和用户一起核对差在哪里
    tables = _copy_results(ctx, runner.collect_outputs(), runner.recipe) if has_reviewable_output(runner.report) else []
    text = f"执行流程「{recipe.name}」v{recipe.version}：{_status_label(runner.report['status'])}。{runner.summary()} {note}".strip()
    if file_info:
        text += f"\n输出文件：「{file_info['filename']}」"
    text += _result_tables_hint(tables)
    yield ToolResult(_clip(text), ok=ok, summary=f"执行「{recipe.name}」：{_status_label(runner.report['status'])}", tables=tables)


def _tool_save_as_recipe(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    targets = [str(t) for t in (args.get("output_tables") or [])]
    recipe = compile_from_workspace(
        ctx.ws, targets, ctx.library, name=str(args.get("name") or "分析流程"), description=str(args.get("description") or ""),
        customer_id=(ctx.customer or {}).get("id"), conversation_id=ctx.conversation_id, field_mapping=ctx.host.field_mapping(),
    )
    saved = ctx.host.store_recipe(recipe, note="由分析过程沉淀")
    steps = "；".join(f"{s.id}. {s.name}（{s.formula}）" for s in recipe.ordered_steps())
    return ToolResult(f"已保存流程「{recipe.name}」v{saved['version']}（recipe_id={saved['recipe_id']}），共 {len(recipe.steps)} 步：{steps}",
                      summary=f"保存流程「{recipe.name}」", events=[{"type": "recipe_saved", **saved, "flow": recipe.flow()}])


def _tool_get_recipe(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    recipe = ctx.host.get_recipe(str(args.get("recipe_id") or ""))
    if recipe is None:
        raise ValueError("没有找到这个流程")
    lines = [f"流程「{recipe.name}」v{recipe.version}（{recipe.status}）：{recipe.description}", "输入："]
    for inp in recipe.inputs:
        lines.append(f"- {inp.alias}：{inp.description}；文件规则 {inp.file_pattern or '无'}；必需列 {inp.required_columns}")
    lines.append("步骤：")
    for step in recipe.ordered_steps():
        lines.append(f"{step.id}. {step.name}［{step.formula}］{step.description} 参数={json.dumps(step.params, ensure_ascii=False)} → {step.output}")
    lines.append("输出：" + "、".join(f"{o.name}←{o.source}" for o in recipe.output_spec))
    return ToolResult(_clip("\n".join(lines)), summary=f"查看流程「{recipe.name}」")


# ---------------------------------------------------------------------------
# 记忆 / 澄清
# ---------------------------------------------------------------------------


def _tool_remember(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    category = str(args.get("category") or "domain_knowledge")
    key, value = str(args.get("key") or "").strip(), str(args.get("value") or "").strip()
    if not key or not value:
        raise ValueError("key 和 value 都不能为空")
    scope = ctx.host.remember(category, key, value)
    return ToolResult(f"已记住（{scope}）：{key} = {value}", summary=f"记住：{key}",
                      events=[{"type": "memory_saved", "items": [{"category": category, "key": key, "value": value}], "scope": scope}])


def _tool_save_field_mapping(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    standard = str(args.get("standard") or "").strip()
    aliases = [str(a).strip() for a in (args.get("aliases") or []) if str(a).strip()]
    if not standard or not aliases:
        raise ValueError("standard 和 aliases 都不能为空")
    msg = ctx.host.save_field_mapping(standard, aliases)
    return ToolResult(msg, summary=f"字段映射：{standard} ← {'、'.join(aliases)}",
                      events=[{"type": "field_mapping_saved", "standard": standard, "aliases": aliases}])


def _tool_ask_user(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    question = str(args.get("question") or "").strip()
    options = [str(o) for o in (args.get("options") or []) if str(o).strip()][:4]
    return ToolResult("已向用户提问，等待用户回答后再继续。", summary=question,
                      events=[{"type": "clarify", "question": question, "options": options}], end_turn=True)


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": {"type": "string"}}


def _static_tools() -> list[Tool]:
    both = frozenset({"analyze", "modify"})
    analyze, modify = frozenset({"analyze"}), frozenset({"modify"})
    return [
        Tool("table_info", "查看一张表的结构：列名、类型、空值、不同值数量、样本、候选主键和前几行。处理任何表之前先调用它。",
             _obj({"table": {**_STR, "description": "表名"}}, ["table"]), both, run=_tool_table_info,
             label=lambda a: f"查看结构 · {a.get('table', '')}"),
        Tool("value_counts", "统计某一列的取值分布（出现次数和占比）。",
             _obj({"table": _STR, "column": _STR, "limit": {"type": "integer", "description": "最多返回多少个取值，默认 20"}},
                  ["table", "column"]), both, run=_tool_value_counts, label=lambda a: f"取值分布 · {a.get('column', '')}"),
        Tool("column_stats", "数值列的描述统计：最小、最大、均值、分位数、空值比例。",
             _obj({"table": _STR, "columns": {**_STR_LIST, "description": "可选，只统计这些列"}}, ["table"]), both,
             run=_tool_column_stats, label=lambda a: f"描述统计 · {a.get('table', '')}"),
        Tool("preview_table", "查看表的若干行原始数据。",
             _obj({"table": _STR, "offset": {"type": "integer"}, "limit": {"type": "integer", "description": "最多 100"}}, ["table"]),
             both, run=_tool_preview, label=lambda a: f"查看数据 · {a.get('table', '')}"),
        Tool("run_sql", "执行一条 DuckDB SELECT 查询做统计（只读）。列名和表名用双引号。分析模式下给 result_name 会把结果保存成表。",
             _obj({"sql": {**_STR, "description": "一条 SELECT 语句"},
                   "result_name": {**_STR, "description": "可选：保存为表的名字（仅分析模式）"},
                   "description": {**_STR, "description": "可选：这条查询做什么"}}, ["sql"]),
             both, run=_tool_run_sql, label=lambda a: "SQL 查询" + (f" → {a['result_name']}" if a.get("result_name") else "")),
        Tool("list_files", "列出可以加载的文件（本会话上传的、客户资料库里的），以及是否已加载。", _obj({}), both,
             run=_tool_list_files, label=lambda a: "查看文件"),
        Tool("load_file", "把一个文件加载到工作区（每个 sheet 一张表）。",
             _obj({"file_id": _STR}, ["file_id"]), both, run=_tool_load_file, label=lambda a: "加载文件"),
        Tool("export_excel", "把一张或多张表导出成 Excel 文件供用户下载。",
             _obj({"sheets": {"type": "array", "items": _obj({"table": _STR, "sheet_name": _STR}, ["table"])},
                   "filename": {**_STR, "description": "文件名（不含扩展名）"}}, ["sheets"]),
             analyze, run=_tool_export_excel, label=lambda a: f"导出 Excel · {a.get('filename', '')}"),
        Tool("build_recipe",
             "生成并执行一个可重复执行的处理流程（Recipe）：系统会比对输入和人工结果（如有）、规划步骤、用 DuckDB 执行、"
             "出错自动修复、校验并验证能否复现人工结果，最后输出 Excel 并保存流程。",
             _obj({"requirement": {**_STR, "description": "清楚的处理说明：要做哪些处理、业务规则、输出什么（相当于 SheetAgent 的 process.md）"},
                   "input_tables": {**_STR_LIST, "description": "输入表名；不填表示所有上传的输入表"},
                   "expected_tables": {**_STR_LIST, "description": "人工做好的期望结果表（如有）；不填会自动使用标记为期望结果的表"},
                   "recipe_name": {**_STR, "description": "可选：流程名"}}, ["requirement"]),
             modify, stream=_stream_build_recipe, label=lambda a: "生成处理流程"),
        Tool("revise_recipe", "按用户的修改意见修订已有流程（生成新版本，重新执行和验证）。",
             _obj({"instruction": {**_STR, "description": "修改要求"},
                   "recipe_id": {**_STR, "description": "可选：不填表示本会话最近的流程"},
                   "inputs": {"type": "object", "description": "可选：{流程输入名: 表名}"},
                   "expected_tables": _STR_LIST}, ["instruction"]),
             modify, stream=_stream_revise_recipe, label=lambda a: "修订处理流程"),
        Tool("run_recipe", "用已保存的流程直接处理当前的表（不需要重新生成），输出 Excel。",
             _obj({"recipe_id": _STR, "inputs": {"type": "object", "description": "可选：{流程输入名: 表名}，不填自动按列匹配"},
                   "allow_repair": {"type": "boolean", "description": "出错时是否允许 AI 自动修复，默认 true"}}, ["recipe_id"]),
             both, stream=_stream_run_recipe, label=lambda a: "执行已有流程"),
        Tool("save_as_recipe", "把分析过程中用公式生成的结果表沉淀成可重复执行的流程（不调用 AI，按依赖自动整理步骤）。",
             _obj({"output_tables": {**_STR_LIST, "description": "最终要输出的结果表"}, "name": _STR,
                   "description": {**_STR, "description": "流程说明"}}, ["output_tables", "name"]),
             analyze, run=_tool_save_as_recipe, label=lambda a: f"保存流程 · {a.get('name', '')}"),
        Tool("get_recipe", "查看一个已保存流程的详细步骤和参数。", _obj({"recipe_id": _STR}, ["recipe_id"]), both,
             run=_tool_get_recipe, label=lambda a: "查看流程"),
        Tool("remember", "记住用户的偏好或业务规则（绑定客户时记到客户名下）。",
             _obj({"category": {"type": "string", "enum": ["preferences", "domain_knowledge", "user_context", "history_insights"]},
                   "key": _STR, "value": _STR}, ["category", "key", "value"]), both, run=_tool_remember,
             label=lambda a: f"记住 · {a.get('key', '')}"),
        Tool("save_field_mapping", "为当前客户保存字段映射：标准字段名 ← 各种表里出现的别名（以后读入时自动统一）。只把确定是同一个意思的列配成别名。",
             _obj({"standard": _STR, "aliases": _STR_LIST}, ["standard", "aliases"]), both, run=_tool_save_field_mapping,
             label=lambda a: f"字段映射 · {a.get('standard', '')}", needs_customer=True),
        Tool("ask_user", "需求有歧义、不同理解会得到明显不同的结果时，向用户提一个问题并给 2~4 个选项，然后停止等待回答。",
             _obj({"question": _STR, "options": _STR_LIST}, ["question", "options"]), both, run=_tool_ask_user,
             label=lambda a: "向你提问"),
    ]


class ToolRegistry:
    def __init__(self) -> None:
        self._static = {t.name: t for t in _static_tools()}

    def for_context(self, ctx: AgentContext) -> list[Tool]:
        tools = [t for t in self._static.values() if ctx.mode in t.modes and (not t.needs_customer or ctx.customer)]
        if ctx.mode == "analyze":
            for spec in ctx.library.customer_specs():
                tools.append(formula_tool(spec, "customer"))
            for spec in ctx.library.builtin_specs():
                if spec.id != "custom_sql":
                    tools.append(formula_tool(spec, "builtin"))
        return tools

    def find(self, name: str, ctx: AgentContext) -> Tool | None:
        for tool in self.for_context(ctx):
            if tool.name == name:
                return tool
        return None


async def execute_tool(tool: Tool, args: dict[str, Any], ctx: AgentContext) -> AsyncIterator[Any]:
    """统一执行：同步工具放线程里跑；流式工具逐个转发事件。最后一定产出一个 ToolResult。"""
    try:
        if tool.stream is not None:
            got_result = False
            async for item in tool.stream(args, ctx):
                if isinstance(item, ToolResult):
                    got_result = True
                yield item
            if not got_result:
                yield ToolResult("工具没有返回结果", ok=False)
        else:
            assert tool.run is not None
            yield await asyncio.to_thread(tool.run, args, ctx)
    except Exception as exc:  # 工具出错时把错误当结果交给 LLM，让它自己纠正
        message = str(exc) or type(exc).__name__
        yield ToolResult(f"工具 `{tool.name}` 出错：{message}", ok=False, summary=message[:200])
