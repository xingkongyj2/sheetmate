"""Agent 工具（对应 TabClaw 的 skills/registry.py + builtin.py，执行改为 DuckDB 引擎）。

工具分组：
- 只读分析：table_info / value_counts / column_stats / preview_table / run_sql
- 公式工具：每个公式自动生成一个工具（内置公式 + 客户独有公式），两个模式均可生成辅助分析表
- 文件：list_files / show_files / load_file / export_excel；独立建表技能 create_workbook
- 脚本：run_python_script / inspect_python_script，可与内置公式、SQL 配合使用
- Recipe：build_recipe / revise_recipe / run_recipe（修改模式）、save_as_recipe / list_recipes / get_recipe / delete_recipe / delete_recipes
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
from pathlib import Path
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
RECIPE_STATUS_LABELS = {"active": "已启用", "deprecated": "已停用"}


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
    public_text: str | None = None  # 与模型内部反馈分离；日志、调试数据无需发送到界面
    public_summary: str | None = None


class ToolHost(Protocol):
    """服务层提供给工具的能力。"""

    def list_files(self) -> list[dict[str, Any]]: ...
    def file_path(self, file_id: str) -> Path | None: ...
    def original_file_path(self, file_id: str) -> Path | None: ...
    def load_file(self, file_id: str) -> list[TableInfo]: ...
    def export_excel(self, sheets: list[tuple[str, str]], filename: str) -> dict[str, Any]: ...
    def create_workbook(self, spec: dict[str, Any], filename: str) -> dict[str, Any]: ...
    def created_workbook_spec(self, file_id: str) -> dict[str, Any]: ...
    def run_python_script(self, *, code: str, description: str, input_tables: list[str], input_files: dict[str, str], cancel_event: Any,
                          title: str = "", script_id: str | None = None) -> dict[str, Any]: ...
    def inspect_python_script(self, run_id: str) -> dict[str, Any]: ...
    def list_recipes(self, *, include_deprecated: bool = False) -> list[dict[str, Any]]: ...
    def get_recipe(self, recipe_id: str) -> Recipe | None: ...
    def latest_conversation_recipe(self) -> Recipe | None: ...
    def executed_recipe(self, run_id: str) -> Recipe | None: ...
    def store_recipe(self, recipe: Recipe, *, note: str = "") -> dict[str, Any]: ...
    def request_recipe_deletion(self, recipe_ids: list[str]) -> dict[str, Any]: ...
    def delete_recipe(self, recipe_id: str) -> dict[str, Any]: ...
    def delete_recipes_confirmed(self, recipe_ids: list[str]) -> list[dict[str, Any]]: ...
    def pipeline_host(self) -> PipelineHost: ...
    def field_mapping(self) -> dict[str, list[str]]: ...
    def memory_text(self, query: str) -> str: ...
    def remember(self, category: str, key: str, value: str) -> str: ...
    def save_field_mapping(self, standard: str, aliases: list[str]) -> str: ...


@dataclass
class AgentContext:
    """一次对话请求的上下文。"""

    mode: str  # analyze / modify / create（由后台 AI 判断）
    ws: Workspace
    library: FormulaLibrary
    host: ToolHost
    llm: Any
    conversation_id: str
    customer: dict[str, Any] | None = None  # {id, name, summary}
    max_repair: int = 3
    verify_rounds: int = 2
    table_filter: set[str] | None = None  # 多表并行时，每个专员只看自己的表
    save_recipe_requested: bool = False  # 仅来自用户明确的保存意图，模型工具参数不能授权
    delete_recipe_ids: set[str] = field(default_factory=set)  # 用户对持久化待删除清单确认后，允许本轮删除的流程

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
    modes: frozenset[str] = frozenset({"analyze", "modify", "create"})
    run: RunFn | None = None  # 同步工具（在线程里执行）
    stream: StreamFn | None = None  # 流式工具：先产出事件，最后产出 ToolResult
    label: Callable[[dict[str, Any]], str] | None = None
    needs_customer: bool = False
    public_params: Callable[[dict[str, Any]], dict[str, Any]] | None = None

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
    result_name = args.get("result_name")
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

    return Tool(fid, description, _obj(properties, required), frozenset({"analyze", "modify", "create"}), run=run, label=label)


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


def _tool_show_files(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    """Publish existing, accessible files without reading or re-exporting their contents."""
    if set(args) - {"file_id", "file_ids", "all", "kind", "kinds"}:
        raise ValueError("查看文件只接受已有文件 ID 或类型，不接受路径、网址或其他参数。")
    if "all" in args and not isinstance(args["all"], bool):
        raise ValueError("all 必须为 true 或 false。")
    kind = args.get("kind")
    kinds = args.get("kinds")
    if "kind" in args and "kinds" in args:
        raise ValueError("kind 与 kinds 请选择一种；查看多种文件类型使用 kinds。")
    if kind is not None and (not isinstance(kind, str) or kind not in {"input", "expected", "output"}):
        raise ValueError("文件类型只能是 input、expected 或 output。")
    if "kinds" in args and (not isinstance(kinds, list) or not kinds or
                            any(not isinstance(value, str) or value not in {"input", "expected", "output"} for value in kinds)):
        raise ValueError("kinds 必须为非空的文件类型列表，只能包含 input、expected 或 output。")
    selected_kinds = set(kinds) if kinds is not None else ({kind} if kind is not None else None)
    single = "file_id" in args
    multiple = "file_ids" in args
    all_files = args.get("all") is True
    if sum((single, multiple, all_files)) > 1:
        raise ValueError("请选择一种查看范围：file_id、file_ids 或 all=true。kind 或 kinds 可用于筛选文件类型。")
    requested: list[str] | None = None
    if single:
        value = args["file_id"]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("请提供要查看的文件 ID。")
        requested = [value.strip()]
    elif multiple:
        values = args["file_ids"]
        if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("file_ids 必须为非空的文件 ID 列表。")
        requested = list(dict.fromkeys(value.strip() for value in values))
    elif not all_files and selected_kinds is None:
        raise ValueError("请提供 file_id、file_ids、all=true 或文件类型 kind / kinds。")

    # list_files is scoped by the current conversation/customer; do not resolve arbitrary IDs or paths.
    available = {item["file_id"]: item for item in ctx.host.list_files() if item.get("kind") in {"input", "expected", "output"}}
    if requested is not None:
        if any(file_id not in available or (selected_kinds is not None and available[file_id].get("kind") not in selected_kinds) for file_id in requested):
            raise ValueError("部分文件不存在、不属于当前客户或会话，或不符合指定类型，请重新查看文件列表。")
        files = [available[file_id] for file_id in requested]
    else:
        files = [item for item in available.values() if selected_kinds is None or item.get("kind") in selected_kinds]
    if not files:
        return ToolResult("没有找到符合范围的文件。", summary="没有可查看的文件")

    public_fields = {"file_id", "filename", "kind", "kind_label", "size", "customer_id", "conversation_id", "run_id", "created_at", "sheets"}
    events = [{"type": "file", **{key: value for key, value in item.items() if key in public_fields},
               "download_url": f"/api/files/{item['file_id']}/download"} for item in files]
    summary = f"可查看和下载 {len(files)} 个文件。"
    return ToolResult(
        summary + "界面已提供原文件的查看和下载卡片；只需简短说明，不要读取或转写原表、重新导出副本或编造下载链接。"
        "若本轮还明确要求分析或修改，可继续完成那些要求。",
        summary=summary, public_text=summary, events=events,
    )


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


def _table_meta(info: TableInfo, ctx: AgentContext) -> InputMeta:
    path = ctx.host.original_file_path(info.file_id) if info.file_id else None
    return InputMeta(filename=info.filename or info.label, sheet=info.sheet, header=info.header, file_id=info.file_id, path=path)


def _recipe_input(ctx: AgentContext, table: str) -> TableInfo:
    info = ctx.ws.require(table)
    if info.source != "uploaded" or info.role != "input":
        inputs = "、".join(_default_inputs(ctx)) or "（请先加载原始文件）"
        raise ValueError(
            f"「{table}」可用于辅助分析；修改流程需要从上传的原始输入表开始以保留格式。"
            f"请将分析得出的处理步骤写入流程，并选择输入表：{inputs}"
        )
    return info


def _learn_context(ctx: AgentContext, requirement: str, inputs: dict[str, str], expected_tables: list[str],
                   recipe_name: str | None = None, mode: str = "learn", file_inputs: dict[str, str] | None = None) -> LearnContext:
    frames, meta = {}, {}
    for alias, table in inputs.items():
        info = _recipe_input(ctx, table)
        frames[alias] = ctx.ws.df(table)
        meta[alias] = _table_meta(info, ctx)
    available = {f["file_id"]: f for f in ctx.host.list_files()} if file_inputs else {}
    for alias, file_id in (file_inputs or {}).items():
        if alias in meta:
            raise ValueError(f"文件别名「{alias}」和输入表重名")
        if file_id not in available:
            raise ValueError("输入文件不属于当前客户或会话")
        meta[alias] = InputMeta(kind="file", filename=available[file_id]["filename"], file_id=file_id,
                                path=ctx.host.original_file_path(file_id))
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
    for index, (sheet, df) in enumerate(outputs.items()):
        name = sanitize_table_name(f"结果_{sheet}")
        info = ctx.ws.info(name)
        if info is not None and not (info.role == "result" and info.created_by == owner):
            name = ctx.ws.unique_name(name)
        ctx.ws.put_dataframe(name, df, label=f"流程结果 · {sheet}", source="computed", role="result",
                             created_by=owner, description=f"「{recipe.name}」v{recipe.version} 的输出",
                             step={"recipe_snapshot": recipe.to_dict(),
                                   "table_ref": next((o.source for o in recipe.output_spec if o.name == sheet),
                                                     recipe.output_spec[index].source if index < len(recipe.output_spec) else recipe.final_outputs()[-1]),
                                   "run_id": (recipe.metadata.get("last_run") or {}).get("run_id")})
        payloads.append(table_payload(ctx.ws, name))
    return payloads


def _recipe_result_text(result: dict[str, Any]) -> str:
    if not result:
        return "流程没有完成。"
    recipe: Recipe = result["recipe"]
    if result.get("error"):
        return f"流程生成失败：{result['error']}"
    lines = [f"本次处理「{recipe.name}」（recipe_id={recipe.id}）：执行状态 {_status_label(result['report']['status'])}。"]
    lines.append("已按用户要求保存为重复流程。" if result.get("saved") else "内部状态：本次没有保存或更新重复流程。无需向用户说明默认行为，也不要主动询问是否保存。")
    report_error = str(result.get("report", {}).get("error") or "").strip()
    if report_error:
        lines.append(f"处理未完成：{report_error}")
        if report_error.startswith("TemplatePreservationError:"):
            lines.append("原始文件未修改，未生成可交付文件。请依据具体错误修正执行方式，重新复制原件后按步骤处理；修改交付以保留原格式的副本为基础，去格式化副本只辅助分析定位。副本内的格式或结构变化不需要再次授权，不要把手动处理或放弃修改作为前置条件。")
    for warning in result["report"].get("output_warnings", []):
        lines.append(f"输出提示：{warning}")
    by_id = {s["step_id"]: s for s in result["report"]["steps"]}
    for step in recipe.ordered_steps():
        rep = by_id.get(step.id, {})
        kind = "Python 脚本" if step.kind == "python" else "SQL" if step.kind == "sql" else "客户独有公式" if step.formula_kind == "customer" else "内置公式"
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
    delivered = bool(result.get("file")) and result["report"].get("status") in {"success", "warning"}
    if questions and not delivered:
        lines.append("流程提出的待核实信息：" + "；".join(q["question"] for q in questions[:3]))
        lines.append("只询问尚未明确的必要业务信息；当前明确要求优先旧记忆，已明确的修改和副本内格式变化不需要再次确认。")
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
    file_inputs = {str(k): str(v) for k, v in (args.get("input_files") or {}).items()}
    if not input_tables and not file_inputs:
        raise ValueError("没有输入表或文件：请先上传要处理的文件")
    lctx = _learn_context(ctx, requirement, {t: t for t in input_tables}, expected_tables, args.get("recipe_name"), file_inputs=file_inputs)
    pipeline = RecipePipeline(ctx.llm, ctx.library, ctx.host.pipeline_host(), max_rounds=ctx.verify_rounds, max_repair=ctx.max_repair)
    async for event in pipeline.build(lctx, save_recipe=ctx.save_recipe_requested):
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
    previous = recipe.metadata.get("requirement") or recipe.description or ""
    requirement = (
        f"历史处理说明（仅沿用未被本次要求覆盖的部分）：\n{previous}\n\n"
        f"本次修改要求（与历史说明或客户记忆冲突时以本次要求为准）：\n{instruction}"
    )
    file_aliases = {i.alias for i in recipe.inputs if i.kind == "file"}
    lctx = _learn_context(ctx, requirement, {k: v for k, v in mapping.items() if k not in file_aliases}, expected_tables, mode="revise",
                          file_inputs={k: v for k, v in mapping.items() if k in file_aliases})
    pipeline = RecipePipeline(ctx.llm, ctx.library, ctx.host.pipeline_host(), max_rounds=ctx.verify_rounds, max_repair=ctx.max_repair)
    async for event in pipeline.build(lctx, base=recipe, instruction=instruction, save_recipe=ctx.save_recipe_requested):
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


def _input_mapping_hint(recipe: Recipe) -> str:
    definitions = "；".join(
        f"{i.alias}（{'完整文件' if i.kind == 'file' else '数据表'}"
        f"{('，来源：' + i.source_file) if i.source_file else ''}）" for i in recipe.inputs
    )
    example = json.dumps({i.alias: "file_id" if i.kind == "file" else "工作区表名" for i in recipe.inputs}, ensure_ascii=False)
    return f"流程输入：{definitions}。显式指定格式：inputs={example}；键必须使用这里的完整输入名，文件 ID 可用 list_files 查看"


def _resolve_inputs(ctx: AgentContext, recipe: Recipe, explicit: Any) -> dict[str, str]:
    """用户指定 → 当前范围内的原始绑定 → 按文件名或列匹配。"""
    mapping: dict[str, str] = {}
    if isinstance(explicit, dict):
        mapping = {str(k): str(v) for k, v in explicit.items() if v}
    unknown = set(mapping) - set(recipe.input_aliases())
    if unknown:
        raise ValueError(f"流程没有这些输入：{sorted(unknown)}。{_input_mapping_hint(recipe)}")
    available_files = {f["file_id"]: f for f in ctx.host.list_files()} if any(i.kind == "file" for i in recipe.inputs) else {}
    # 数据库记录可能仍在，但原件已丢失；不能将它当成可执行的绑定或匹配候选。
    available_files = {fid: item for fid, item in available_files.items()
                       if (path := ctx.host.original_file_path(fid)) is not None and path.is_file()}
    tables = [t for t in ctx.ws.list_tables() if t.source == "uploaded" and t.role == "input"]
    bindings = recipe.learned_from.get("input_bindings")
    if isinstance(bindings, dict):
        for inp in recipe.inputs:
            if inp.alias in mapping:
                continue
            binding = bindings.get(inp.alias)
            if not isinstance(binding, dict) or not binding.get("file_id"):
                continue
            file_id = binding["file_id"]
            if inp.kind == "file":
                if file_id in available_files:
                    mapping[inp.alias] = file_id
            else:
                sheet = binding.get("sheet") or inp.sheet
                bound_tables = [t.name for t in tables if t.file_id == file_id and (not sheet or t.sheet == sheet)]
                if len(bound_tables) == 1:
                    mapping[inp.alias] = bound_tables[0]
    pending = [i for i in recipe.inputs if i.alias not in mapping]
    if pending:
        candidates = [Candidate(key=t.name, columns=t.column_names, filename=t.filename, sheet=t.sheet, rows=t.rows)
                      for t in tables if t.name not in mapping.values()]
        candidates.extend(Candidate(key=f["file_id"], kind="file", columns=[], filename=f["filename"])
                          for f in available_files.values() if f.get("kind") == "input" and f["file_id"] not in mapping.values())
        partial = Recipe(name=recipe.name, inputs=pending)
        for alias, hit in match_inputs(partial, candidates, ctx.host.field_mapping()).items():
            mapping[alias] = hit["key"]
    missing = [f"{i.source_file or i.description or i.alias}（输入名：{i.alias}）" for i in recipe.inputs if i.alias not in mapping]
    if missing:
        table_names = "、".join(t.name for t in tables) or "（没有）"
        file_names = "、".join(f"{item['filename']}（file_id={fid}）" for fid, item in available_files.items()) or "（没有）"
        raise ValueError(f"找不到流程需要的输入：{'、'.join(missing)}。工作区里的表：{table_names}。"
                         f"可用文件：{file_names}。{_input_mapping_hint(recipe)}，或先上传对应文件")
    definitions = {i.alias: i for i in recipe.inputs}
    for alias, value in mapping.items():
        if definitions[alias].kind == "file":
            if value not in available_files:
                raise ValueError(f"输入文件不存在、已被删除或不属于当前客户或会话。{_input_mapping_hint(recipe)}")
        else:
            _recipe_input(ctx, value)
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
        if next(i for i in recipe.inputs if i.alias == alias).kind == "file":
            item = next(f for f in ctx.host.list_files() if f["file_id"] == table)
            sources[alias] = InputSource(alias=alias, filename=item["filename"], file_id=table, path=ctx.host.original_file_path(table))
            continue
        info = ctx.ws.require(table)
        sources[alias] = InputSource(alias=alias, df=ctx.ws.df(table), filename=info.filename or info.label,
                                     sheet=info.sheet, file_id=info.file_id, header=info.header,
                                     path=ctx.host.original_file_path(info.file_id) if info.file_id else None)
    async for event in runner.run(sources, output_path=handle.output_path, history=host.history_metrics(recipe.id)):
        yield event
    handle.output_path = runner.output_path or handle.output_path
    file_info = host.finish_run(handle, runner.recipe, runner.report)
    runner.recipe.metadata["last_run"] = {"run_id": handle.run_id, "status": runner.report["status"]}
    if file_info:
        for item in file_info.get("files", [file_info]):
            yield {"type": "file", **item}
    note = ""
    if runner.recipe_changed and ctx.save_recipe_requested and runner.report["status"] in {"success", "warning"}:
        saved = ctx.host.store_recipe(runner.recipe, note="执行时自动修复")
        yield {"type": "recipe_saved", **saved, "flow": runner.recipe.flow()}
        note = f"执行中自动修复了第 {runner.report['repaired_steps']} 步，已保存为 v{saved['version']}。"
    elif runner.recipe_changed and ctx.save_recipe_requested:
        note = "本次执行未通过，尚未保存修复后的流程。"
    ok = runner.report["status"] not in {"failed", "escalated"}
    # 只是运行校验没通过时，结果表也放回工作区，方便和用户一起核对差在哪里
    tables = _copy_results(ctx, runner.collect_outputs(), runner.recipe) if has_reviewable_output(runner.report) else []
    text = f"执行流程「{recipe.name}」v{recipe.version}：{_status_label(runner.report['status'])}。{runner.summary()} {note}".strip()
    if file_info:
        text += f"\n输出文件：「{file_info['filename']}」"
    text += _result_tables_hint(tables)
    yield ToolResult(_clip(text), ok=ok, summary=f"执行「{recipe.name}」：{_status_label(runner.report['status'])}", tables=tables)


def _recipe_for_saved_outputs(ctx: AgentContext, targets: list[str]) -> Recipe | None:
    """完整保存已有流程的输出时复用原定义，避免再次编译成第二个流程。"""
    snapshots = []
    refs = []
    for table in targets:
        info = ctx.ws.require(table)
        provenance = info.step or {}
        snapshot = provenance.get("recipe_snapshot")
        if info.role != "result" or not isinstance(snapshot, dict) or not provenance.get("table_ref"):
            return None
        snapshots.append(Recipe.model_validate(snapshot))
        refs.append(provenance["table_ref"])
    recipe = snapshots[0]
    run_id = (recipe.metadata.get("last_run") or {}).get("run_id")
    if any(item.id != recipe.id or (item.metadata.get("last_run") or {}).get("run_id") != run_id for item in snapshots):
        return None
    if len(refs) != len(recipe.output_spec) or set(refs) != {output.source for output in recipe.output_spec}:
        return None
    return recipe


def _tool_save_as_recipe(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    if not ctx.save_recipe_requested:
        raise ValueError("用户没有要求保存重复流程；只完成本次处理，不要主动保存或追问保存。")
    targets = [str(t) for t in (args.get("output_tables") or [])]
    if targets:
        recipe = _recipe_for_saved_outputs(ctx, targets) or compile_from_workspace(
            ctx.ws, targets, ctx.library, name=str(args.get("name") or "分析流程"), description=str(args.get("description") or ""),
            customer_id=(ctx.customer or {}).get("id"), conversation_id=ctx.conversation_id, field_mapping=ctx.host.field_mapping(),
            file_resolver=ctx.host.file_dependency,
        )
    else:
        recipe = (ctx.host.executed_recipe(str(args["run_id"])) if args.get("run_id") else
                  ctx.host.get_recipe(str(args["recipe_id"])) if args.get("recipe_id") else ctx.host.latest_conversation_recipe())
        if recipe is None:
            raise ValueError("没有找到可保存的执行记录；请指定已执行的 run_id 或最终结果表。公式、SQL 和 Python 脚本均可保存。")
    if args.get("name"):
        recipe.name = str(args["name"])
    if args.get("description"):
        recipe.description = str(args["description"])
    recipe.status = "active"
    saved = ctx.host.store_recipe(recipe, note="按用户要求保存本次处理流程")
    steps = "；".join(f"{s.id}. {s.name}（{s.formula}）" for s in recipe.ordered_steps())
    return ToolResult(f"已保存并启用流程「{recipe.name}」v{saved['version']}（recipe_id={saved['recipe_id']}），共 {len(recipe.steps)} 步：{steps}。"
                      "界面已展示这一个正式流程，无需再调用 get_recipe 展示，或再次保存相同流程。",
                      summary=f"保存流程「{recipe.name}」", events=[{"type": "recipe_saved", **saved, "flow": recipe.flow()}])


def _tool_list_recipes(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    recipes = ctx.host.list_recipes(include_deprecated=True)
    if not recipes:
        return ToolResult("当前客户或会话没有已保存的流程。", summary="没有已保存的流程")
    lines = [f"- recipe_id={item['recipe_id']}「{item['name']}」v{item['version']}（{item['status_label']}）"
             for item in recipes]
    return ToolResult("当前可访问的已保存流程（含已停用）：\n" + "\n".join(lines), summary=f"找到 {len(recipes)} 个已保存流程")


def _tool_delete_recipe(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    recipe_id = str(args.get("recipe_id") or "")
    if not recipe_id:
        raise ValueError("请先用 list_recipes 找到要删除的流程 ID。")
    return _delete_recipes([recipe_id], ctx)


def _tool_delete_recipes(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    if args.get("all") is True:
        targets = [item["recipe_id"] for item in ctx.host.list_recipes(include_deprecated=True)]
        if not targets:
            return ToolResult("当前客户或会话没有已保存的流程，无需删除。", summary="没有需要删除的流程")
    else:
        raw = args.get("recipe_ids")
        if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
            raise ValueError("请提供要删除的 recipe_ids；删除全部流程时使用 all=true。")
        targets = list(dict.fromkeys(raw))
        if not targets:
            raise ValueError("请选择要删除的流程；删除全部流程时使用 all=true。")
    return _delete_recipes(targets, ctx)


def _delete_recipes(recipe_ids: list[str], ctx: AgentContext) -> ToolResult:
    if not set(recipe_ids) <= ctx.delete_recipe_ids:
        confirmation = ctx.host.request_recipe_deletion(recipe_ids)
        question = str(confirmation["question"])
        return ToolResult(question, summary=f"等待确认删除 {len(recipe_ids)} 个流程", end_turn=True,
                          events=[{"type": "clarify", **confirmation}])
    deleted = ctx.host.delete_recipes_confirmed(recipe_ids)
    ctx.delete_recipe_ids.difference_update(recipe_ids)
    names = "、".join(f"「{item['name']}」" for item in deleted)
    return ToolResult(f"已删除流程{names}的全部版本。", summary=f"已删除 {len(deleted)} 个流程",
                      events=[{"type": "recipe_deleted", **item} for item in deleted])


def _tool_get_recipe(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    show_all = args.get("all") is True
    recipe_id = args.get("recipe_id")
    recipe_ids = args.get("recipe_ids")
    if sum((show_all, recipe_id is not None, recipe_ids is not None)) != 1:
        raise ValueError("请选择一种查看范围：单个 recipe_id、多个 recipe_ids，或全部流程 all=true。")
    if show_all:
        targets = [item["recipe_id"] for item in ctx.host.list_recipes(include_deprecated=True)]
    elif recipe_ids is not None:
        if not isinstance(recipe_ids, list) or not recipe_ids or not all(isinstance(item, str) and item.strip() for item in recipe_ids):
            raise ValueError("recipe_ids 必须是非空的流程 ID 列表。")
        targets = list(dict.fromkeys(item.strip() for item in recipe_ids))
    else:
        if not isinstance(recipe_id, str) or not recipe_id.strip():
            raise ValueError("请提供要查看的流程 ID。")
        targets = [recipe_id.strip()]
    if not targets:
        return ToolResult("当前客户或会话没有已保存的流程，没有可展示的流程图。", summary="没有已保存的流程")

    recipes = []
    for target in targets:
        recipe = ctx.host.get_recipe(target)
        if recipe is None:
            raise ValueError("没有找到这个流程" if len(targets) == 1 else "部分流程不存在或不属于当前客户或会话，请重新查看流程列表。")
        recipes.append(recipe)
    lines = [
        f"已向本轮对话界面发送 {len(recipes)} 个流程的原生流程图数据；界面会在正文显示完成后自动渲染客户页同款流程图组件。",
        "回复只需简短说明查看范围或用户关心的结论；不要再画 ASCII/字符框图、Mermaid 或代码块流程图，也不要重复列出完整输入、步骤和输出。",
        "以下详细设置仅供你理解和回答具体问题，不是需要重画的流程图：",
    ]
    events = []
    for recipe in recipes:
        lines.extend([f"流程「{recipe.name}」v{recipe.version}（{recipe.status}）：{recipe.description}", "输入："])
        for inp in recipe.inputs:
            lines.append(f"- {inp.alias}：{inp.description}；文件规则 {inp.file_pattern or '无'}；必需列 {inp.required_columns}")
        lines.append("步骤：")
        for step in recipe.ordered_steps():
            lines.append(f"{step.id}. {step.name}［{step.formula}］{step.description} 参数={json.dumps(step.params, ensure_ascii=False)} → {step.output}")
        lines.append("输出：" + "、".join(f"{o.name}←{o.source}" for o in recipe.output_spec))
        events.append({
            "type": "recipe_view", "recipe_id": recipe.id, "name": recipe.name,
            "description": recipe.description, "version": recipe.version,
            "status": recipe.status, "status_label": RECIPE_STATUS_LABELS.get(recipe.status, recipe.status),
            "flow": recipe.flow(),
        })
    summary = f"查看流程「{recipes[0].name}」" if len(recipes) == 1 else f"查看 {len(recipes)} 个流程的流程图"
    return ToolResult(
        _clip("\n".join(lines)), summary=summary, public_text=summary,
        events=events,
    )


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
    from sheetmate.agent.creation import workbook_creation_tool, workbook_inspection_tool
    from sheetmate.agent.scripting import python_script_tools

    both = frozenset({"analyze", "modify", "create"})
    analyze, modify = frozenset({"analyze"}), frozenset({"modify"})
    return [
        *python_script_tools(),
        workbook_creation_tool(),
        workbook_inspection_tool(),
        Tool("table_info", "查看一张表的结构：列名、类型、空值、不同值数量、样本、候选主键和前几行。处理任何表之前先调用它。",
             _obj({"table": {**_STR, "description": "表名"}}, ["table"]), both, run=_tool_table_info,
             label=lambda a: f"查看结构 · {a.get('table', '')}"),
        Tool("value_counts", "统计某一列的取值分布（出现次数和占比）。",
             _obj({"table": _STR, "column": _STR, "limit": {"type": "integer", "description": "最多返回多少个取值，默认 20"}},
                  ["table", "column"]), both, run=_tool_value_counts, label=lambda a: f"取值分布 · {a.get('column', '')}"),
        Tool("column_stats", "数值列的描述统计：最小、最大、均值、分位数、空值比例。",
             _obj({"table": _STR, "columns": {**_STR_LIST, "description": "可选，只统计这些列"}}, ["table"]), both,
             run=_tool_column_stats, label=lambda a: f"描述统计 · {a.get('table', '')}"),
        Tool("preview_table", "读取表的若干行供分析核对，不代表交付完整表格。正式明细和较大结果通过文件卡片预览下载；不要为展示工作簿而反复读取或拼接行数据。",
             _obj({"table": _STR, "offset": {"type": "integer"}, "limit": {"type": "integer", "description": "最多 100"}}, ["table"]),
             both, run=_tool_preview, label=lambda a: f"查看数据 · {a.get('table', '')}"),
        Tool("run_sql", "［优先数据处理工具］在分析副本上执行一条 DuckDB SELECT，适合关联、筛选、分组汇总、窗口计算和复杂条件，优先于 Python 脚本。列名和表名用双引号。给 result_name 可保存中间表并供后续公式或流程使用；不改原件或最终格式副本。0 行查询无需展示空表，正式明细或较大结果需通过文件交付。",
             _obj({"sql": {**_STR, "description": "一条 SELECT 语句"},
                   "result_name": {**_STR, "description": "可选：辅助分析结果表名，两种模式均可使用"},
                   "description": {**_STR, "description": "可选：这条查询做什么"}}, ["sql"]),
             both, run=_tool_run_sql, label=lambda a: "SQL 查询" + (f" → {a['result_name']}" if a.get("result_name") else "")),
        Tool("list_files", "查找当前会话或客户可访问的输入、期望结果、输出文件及准确 ID；只返回清单，不会向用户展示文件卡片。", _obj({}), both,
             run=_tool_list_files, label=lambda a: "查看文件"),
        Tool("show_files", "向用户展示任意已有输入、期望结果或输出文件的原生查看和下载卡片；保留原文件，不读取数据、不重新导出。查看单个用 file_id，多个用 file_ids，全部用 all=true，按单种类型用 kind，多种类型用 kinds；原始文件对应 input，不按文件名判断。用户只是要看文件时必须调用本工具，不能用 preview_table 或 Markdown 数据表代替。",
             _obj({"file_id": {**_STR, "description": "单个已有文件的 ID；与 file_ids、all=true 三选一"},
                   "file_ids": {**_STR_LIST, "description": "多个已有文件的 ID；按提供顺序展示并去重"},
                   "all": {"type": "boolean", "description": "true 展示当前客户或会话全部可访问文件；可结合 kind 筛选"},
                   "kind": {"type": "string", "enum": ["input", "expected", "output"], "description": "筛选单种文件类型；input为原始输入、expected为期望结果、output为已有输出。单独提供时展示该类型全部文件"},
                   "kinds": {"type": "array", "items": {"type": "string", "enum": ["input", "expected", "output"]},
                             "description": "筛选多种文件类型；例如查看所有输入输出用 [input,output]，与 kind 互斥；单独提供时展示这些类型全部文件"}}),
             both, run=_tool_show_files, label=lambda a: "展示文件"),
        Tool("load_file", "把一个文件加载到工作区（每个 sheet 一张表）。",
             _obj({"file_id": _STR}, ["file_id"]), both, run=_tool_load_file, label=lambda a: "加载文件"),
        Tool("export_excel", "把一张或多张已核对的结果表导出成 Excel，由文件卡片提供查看和下载。正式明细或较大结果使用此工具交付，不把完整数据粘贴到回复中；已有交付文件不重复导出。",
             _obj({"sheets": {"type": "array", "items": _obj({"table": _STR, "sheet_name": _STR}, ["table"])},
                   "filename": {**_STR, "description": "文件名（不含扩展名）"}}, ["sheets"]),
             analyze, run=_tool_export_excel, label=lambda a: f"导出 Excel · {a.get('filename', '')}"),
        Tool("build_recipe",
             "［数据修改优先］生成并执行一个可重复执行的处理流程（Recipe）：比对输入和人工结果（如有），优先使用公式或 SQL，仅为它们不好做或超出范围的部分加入 Python，可混合步骤，由相应引擎执行、"
             "出错自动修复、校验并验证能否复现人工结果，最后输出 Excel。默认只完成本次处理；仅当用户明确要求长期重复执行时才保存流程。",
             _obj({"requirement": {**_STR, "description": "清楚的处理说明：要做哪些处理、业务规则、输出什么（相当于 SheetAgent 的 process.md）"},
                   "input_tables": {**_STR_LIST, "description": "原始上传的输入表名；不填自动使用。辅助分析结果用于完善 requirement，不代替原始输入"},
                   "input_files": {"type": "object", "additionalProperties": {"type": "string"}, "description": "可选：{完整文件输入别名: file_id}，用于 Python 排版或读取原始工作簿；与 input_tables 可同时提供"},
                   "expected_tables": {**_STR_LIST, "description": "人工做好的期望结果表（如有）；不填会自动使用标记为期望结果的表"},
                   "recipe_name": {**_STR, "description": "可选：流程名"}}, ["requirement"]),
             modify, stream=_stream_build_recipe, label=lambda a: "生成处理流程"),
        Tool("revise_recipe", "按用户的修改意见调整本次处理，重新执行和验证；仅明确要求保存/更新重复流程时才持久保存新版本。",
             _obj({"instruction": {**_STR, "description": "修改要求"},
                   "recipe_id": {**_STR, "description": "可选：不填表示本会话最近的流程"},
                   "inputs": {"type": "object", "description": "可选：{流程输入名: 表名或文件file_id}；键使用 get_recipe 返回的完整输入 alias。table 对应真实工作区表名，file 对应完整文件 file_id；省略时优先沿用当前范围内的已有绑定，再自动匹配"},
                   "expected_tables": _STR_LIST}, ["instruction"]),
             modify, stream=_stream_revise_recipe, label=lambda a: "修订处理流程"),
        Tool("run_recipe", "用已保存的公式、SQL、Python 或混合流程直接处理当前表和文件，不需要重新生成步骤或重写代码，输出正式结果。",
             _obj({"recipe_id": _STR, "inputs": {"type": "object", "description": "可选：{流程输入名: 表名或文件file_id}；键使用 get_recipe 返回的完整输入 alias；省略时优先沿用当前范围内的已有绑定，再按列或文件名匹配"},
                   "allow_repair": {"type": "boolean", "description": "出错时是否允许 AI 自动修复，默认 true"}}, ["recipe_id"]),
             modify, stream=_stream_run_recipe, label=lambda a: "执行已有流程"),
        Tool("save_as_recipe", "仅用户明确要求保存重复流程时调用，直接保存并启用正式流程。支持公式、SQL、Python 脚本和混合流程；保存实际执行的代码与依赖，不需要重新处理文件。"
             "若 build_recipe、revise_recipe 或 run_recipe 已返回 recipe_saved，则同一流程已经保存，不要再次调用本工具。保存后界面自动展示流程图，无需再调用 get_recipe。",
             _obj({"output_tables": {**_STR_LIST, "description": "可选：公式、SQL 或脚本生成的最终结果表。不填则保存指定或最近已执行流程"}, "name": _STR,
                   "run_id": {**_STR, "description": "可选：明确指定脚本或混合流程的运行 ID，优先于 recipe_id"},
                   "recipe_id": {**_STR, "description": "可选：指定本会话已执行的流程 ID，不填用最近的"},
                   "description": {**_STR, "description": "流程说明"}}),
             both, run=_tool_save_as_recipe, label=lambda a: f"保存流程 · {a.get('name', '')}"),
        Tool("get_recipe", "在本轮对话中展示客户页同款原生流程图：单个用 recipe_id，多个用 recipe_ids，查看所有流程图直接用 all=true（含已停用流程）。界面自动绘制输入、步骤和输出，模型只需简短说明，不要画字符图或 Mermaid。只读，不执行、修改或保存流程。",
             _obj({"recipe_id": {**_STR, "description": "查看单个流程时指定 ID，与 recipe_ids、all=true 三选一"},
                   "recipe_ids": {**_STR_LIST, "description": "查看指定多个流程的 ID 列表"},
                   "all": {"type": "boolean", "description": "true 表示展示当前客户或会话全部已保存流程的原生流程图，包含已停用流程，无需先查 ID"}}),
             both, run=_tool_get_recipe, label=lambda a: "查看全部流程图" if a.get("all") is True else "查看流程"),
        Tool("list_recipes", "查看当前客户或会话已保存的流程，包含已停用流程及准确 ID。查找待删除流程或区分同名流程时使用。",
             _obj({}), both, run=_tool_list_recipes, label=lambda a: "查看已保存流程"),
        Tool("delete_recipe", "删除指定的已保存流程及全部版本。调用后由系统列出目标，请用户确认一次；确认后系统直接删除，无需额外授权。删除表格行或文件不等于删除流程。",
             _obj({"recipe_id": {**_STR, "description": "要删除的已保存流程 ID"}}, ["recipe_id"]),
             both, run=_tool_delete_recipe, label=lambda a: "删除已保存流程"),
        Tool("delete_recipes", "批量删除已保存流程及全部版本。用户说删除全部流程时使用 all=true；指定多条时提供 recipe_ids。系统列出清单并请用户确认一次，确认后直接执行。",
             _obj({"all": {"type": "boolean", "description": "true表示当前客户或会话的全部已保存流程，含已停用流程"},
                   "recipe_ids": {**_STR_LIST, "description": "指定要删除的流程 ID 列表；all=true时无需提供"}}),
             both, run=_tool_delete_recipes, label=lambda a: "删除全部流程" if a.get("all") is True else "批量删除流程"),
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


def reserved_tool_names() -> set[str]:
    """固定工具的名字。公式会被注册成同名工具，所以客户公式的 id 不能和它们重名（services/recipes.py 保存公式时检查）。"""
    return {t.name for t in _static_tools()} | {"reconsider_approach"}


class ToolRegistry:
    def __init__(self) -> None:
        self._static = {t.name: t for t in _static_tools()}

    def for_context(self, ctx: AgentContext) -> list[Tool]:
        tools = [t for t in self._static.values() if ctx.mode in t.modes and (not t.needs_customer or ctx.customer)]
        if ctx.mode in {"analyze", "modify", "create"}:
            for spec in ctx.library.customer_specs():
                tools.append(formula_tool(spec, "customer"))
            for spec in ctx.library.builtin_specs():
                if spec.id != "custom_sql":
                    tools.append(formula_tool(spec, "builtin"))
        # 与选择规则一致，脚本入口放在公式、SQL 和流程工具之后。
        tools.sort(key=lambda tool: tool.name == "run_python_script")
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
            worker = asyncio.create_task(asyncio.to_thread(tool.run, args, ctx))
            try:
                result = await asyncio.shield(worker)
            except asyncio.CancelledError:
                ctx.ws.interrupt()
                try:
                    await worker
                except Exception:
                    pass
                raise
            yield result
    except Exception as exc:  # 工具出错时把错误当结果交给 LLM，让它自己纠正
        message = str(exc) or type(exc).__name__
        yield ToolResult(f"工具 `{tool.name}` 出错：{message}", ok=False, summary=message[:200])
