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
import contextvars
import sys
import threading
from concurrent.futures import Future
import json
import re
import time
from collections.abc import AsyncIterator, Callable
from copy import copy, deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import pandas as pd
from jsonschema import Draft202012Validator

from sheetmate.agent.structured import StructuredOutputError, validate_payload
from sheetmate.agent.task_state import inspect_task
from sheetmate.agent.watchdog import phase, register_tool_worker, report_cleanup, report_wait, wait_tool
from sheetmate.agent.sql_scope import checked_sql_tables
from sheetmate.conversation_log import record_event
from sheetmate.engine.analysis import column_stats, table_info, table_info_text, value_counts
from sheetmate.engine.formulas import Formula, FormulaLibrary, ParamSpec
from sheetmate.engine.runner import apply_formula
from sheetmate.engine.sqlutil import assert_single_select, frame_rows, frame_to_text, sanitize_table_name, sql_table_refs
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
_LOG_DATA_EVENTS = {"file", "table", "script", "clarify", "memory_saved", "field_mapping_saved",
                    "recipe_saved", "recipe_completed", "recipe_deleted", "recipe_failed", "recipe_run_done"}


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
    public_details: list[dict[str, Any]] | None = None  # 面向用户的对象、操作、变化及执行证据
    error_kind: str | None = None  # protocol/schema 不属于工具内部的业务失败
    error_details: list[dict[str, str]] | None = None


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
    def list_recipes(self) -> list[dict[str, Any]]: ...
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
    def inspect_saved_memories(self, *, query: str = "", memory_id: int | None = None,
                               offset: int = 0, limit: int | None = None) -> dict[str, Any]: ...
    def inspect_conversation_history(self, *, message_id: int | None = None, query: str = "",
                                     role: str = "", offset: int = 0, limit: int | None = None) -> dict[str, Any]: ...
    def read_tool_result(self, result_id: str, *, section: str = "text", offset: int = 0,
                         limit: int = 4000, branch_id: str | None = None) -> dict[str, Any]: ...
    def remember(self, category: str, key: str, value: str, *, evidence_span: str = "") -> dict[str, Any]: ...
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
    customer: dict[str, Any] | None = None  # 客户资料及独立的当前流程说明，由服务层每轮刷新
    max_repair: int = 3
    verify_rounds: int = 2
    table_filter: set[str] | None = None  # 多表并行时，每个专员只看自己的表
    save_recipe_requested: bool = False  # 仅来自用户明确的保存意图，模型工具参数不能授权
    delete_recipe_ids: set[str] = field(default_factory=set)  # 用户对持久化待删除清单确认后，允许本轮删除的流程
    image_parts: list[dict[str, Any]] = field(default_factory=list)  # 当前消息的图片，仅放入多模态请求，不写入提示词文本
    file_memory: str = ""  # 已授权但尚未加载的原件结构与探测事实，每轮按文件版本刷新
    replace_file_ids: tuple[str, ...] = ()  # 本轮成功交付后更新的旧结果，由处理意图判断提供
    tool_group: str | None = None  # 可选：core 只暴露低成本分析工具，full 保持完整工具集
    branch_id: str | None = None  # 并行子任务的证据归属；协调器为空
    task_state_block: str = ""  # 服务层生成的结构化当前任务状态摘要
    task_state: dict[str, Any] = field(default_factory=dict)
    enabled_tool_groups: set[str] = field(default_factory=set)
    evidence_store: dict[str, dict[str, Any]] = field(default_factory=dict)
    parallel_read_only: bool = False  # Internal: publish only this read's target-table probe cache.

    def visible_tables(self) -> list[TableInfo]:
        tables = self.ws.list_tables()
        if self.table_filter is not None:
            return [t for t in tables if t.name in self.table_filter]
        return tables


StreamFn = Callable[[dict[str, Any], AgentContext], AsyncIterator[Any]]
RunFn = Callable[[dict[str, Any], AgentContext], ToolResult]


class ToolSchemaError(ValueError):
    """本地工具定义错误，不能要求模型通过修改业务参数修复。"""

    def __init__(self, name: str, reason: str):
        super().__init__(f"工具 `{name}` 的参数契约配置无效：{reason}。本轮操作尚未执行，请检查工具配置。")


def _check_tool_schema(name: str, schema: dict[str, Any]) -> None:
    def check_refs(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"$ref", "$dynamicRef", "$recursiveRef"} and (
                    not isinstance(item, str) or not item.startswith("#")
                ):
                    raise ToolSchemaError(name, "Schema 引用仅允许使用 # 开头的本地引用")
                check_refs(item)
        elif isinstance(value, list):
            for item in value:
                check_refs(item)

    try:
        check_refs(schema)  # 在 jsonschema 接触引用前拒绝任何外部资源。
        Draft202012Validator.check_schema(schema)
    except ToolSchemaError:
        raise
    except Exception as exc:
        raise ToolSchemaError(name, "JSON Schema 定义不合法") from exc


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
    process_safe: bool = False  # Trusted pure data operation, published only after a killable worker succeeds.
    parallel_read_safe: bool = False  # Explicit opt-in; the scheduler also validates per-call resources.
    _checked_schema: dict[str, Any] | None = field(default=None, init=False, repr=False, compare=False)

    def parameters_schema(self) -> dict[str, Any]:
        """模型与执行器共用契约；仅默认关闭顶层的未声明字段。"""
        if not isinstance(self.parameters, dict):
            raise ToolSchemaError(self.name, "parameters 必须是 JSON Schema 对象")
        if self.parameters.get("type", "object") != "object":
            raise ToolSchemaError(self.name, "参数契约顶层 type 必须是 object")
        schema = {**self.parameters, "type": "object"}
        schema.setdefault("additionalProperties", False)
        if self._checked_schema != schema:
            _check_tool_schema(self.name, schema)
            self._checked_schema = deepcopy(schema)
        return schema

    def definition(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                   "parameters": self.parameters_schema()}}

    def display(self, args: dict[str, Any]) -> str:
        try:
            return self.label(args) if self.label else self.name
        except Exception:
            return self.name


def _obj(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


def validate_tool_args(tool: Tool, args: Any) -> dict[str, Any]:
    """执行前验证公开契约，不转换类型，也不补全任何业务参数。"""
    schema = tool.parameters_schema()
    try:
        return validate_payload(args, schema)
    except StructuredOutputError:
        raise
    except Exception as exc:
        raise ToolSchemaError(tool.name, "参数契约无法完成校验，请检查本地引用和约束配置") from exc


def tool_argument_error(name: str, error_kind: str, errors: list[dict[str, str]]) -> ToolResult:
    """同一错误协议供流式执行器和直接工具调用使用。"""
    payload = {"error_kind": error_kind, "errors": errors, "executed": False}
    return ToolResult(
        f"工具 `{name}` 尚未执行。请按工具定义修正调用结构：\n" + json.dumps(payload, ensure_ascii=False),
        ok=False, summary="调用参数未通过校验，尚未执行", error_kind=error_kind, error_details=errors,
        public_summary="正在核对处理参数", public_text="调用参数尚未通过校验，本次操作未执行。",
    )


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


# ---------------------------------------------------------------------------
# 只读分析工具
# ---------------------------------------------------------------------------


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
    offset, limit = int(args.get("offset") or 0), min(int(args.get("limit") or 20), 100)
    page = ctx.ws.page(table, offset, limit)
    df = pd.DataFrame(page["rows"], columns=[c["name"] for c in page["columns"]])
    return ToolResult(_clip(f"「{table}」第 {offset + 1}~{offset + len(df)} 行（共 {page['total']} 行）：\n{frame_to_text(df, limit=limit)}"),
                      summary=f"查看「{table}」{len(df)} 行",
                      public_details=[public_detail_section("查看对象", [_table_detail(ctx.ws.require(table))]),
                                      public_detail_section("读取范围", [f"第 {offset + 1}～{offset + len(df)} 行，共读取 {len(df)} 行。" if len(df) else f"从第 {offset + 1} 行开始读取，未读到记录。"]),
                                      public_detail_section("读取字段", [column["name"] for column in page["columns"]])])


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
        out = _result_name(ctx, result_name, "查询结果")
        ctx.ws.create_table_as(
            out, sql, label=str(result_name), source="computed", role="derived", created_by="run_sql",
            description=str(args.get("description") or "SQL 查询结果"), step={"formula": "custom_sql", "params": {"sql": sql}},
        )
        if ctx.table_filter is not None:
            ctx.table_filter.add(out)
        df = ctx.ws.df(out, limit=10)
        info = ctx.ws.require(out)
        return ToolResult(_clip(f"已生成表「{out}」：{info.rows} 行 × {len(info.columns)} 列。前几行：\n{frame_to_text(df, limit=10)}"),
                          summary=f"生成「{out}」{info.rows} 行", tables=[table_payload(ctx.ws, out)],
                          public_details=[section for section in [*details,
                              public_detail_section("查询结果", [f"生成「{out}」：{info.rows} 行 × {len(info.columns)} 列。"]),
                              public_detail_section("结果字段", info.column_names)] if section["items"]])
    df = ctx.ws.query(sql, limit=500)
    return ToolResult(_clip(f"查询结果（{len(df)} 行{'，最多显示 500 行' if len(df) == 500 else ''}）：\n{frame_to_text(df, limit=40)}"),
                      summary=f"查询返回 {len(df)} 行", tables=[ephemeral_payload("查询结果", df)],
                      public_details=[section for section in [*details,
                          public_detail_section("查询结果", [f"读取 {len(df)} 行结果" + ("（达到 500 行显示上限）。" if len(df) == 500 else "。")]),
                          public_detail_section("结果字段", [str(column) for column in df.columns])] if section["items"]])


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


# ---------------------------------------------------------------------------
# 文件工具
# ---------------------------------------------------------------------------


def _tool_list_files(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    files = ctx.host.list_files()
    if not files:
        return ToolResult("没有可加载的文件。", summary="没有文件", public_details=[public_detail_section("查看结果", ["当前可访问范围没有文件。"])])
    lines = [
        f"- file_id={f['file_id']}「{f['filename']}」（{f.get('scope_label', '')}，{f.get('kind_label', '')}）"
        + (f" 已加载为表：{f['tables']}" if f.get("tables") else " 未加载")
        for f in files
    ]
    return ToolResult("\n".join(lines), summary=f"{len(files)} 个文件",
                      public_text="\n".join(_file_detail(item) for item in files),
                      public_details=[public_detail_section("查看到的文件", [_file_detail(item) for item in files]),
                                      public_detail_section("查看内容", ["已查询文件名称、类型、工作表和加载状态。"])])


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
        files = [item for item in available.values() if (selected_kinds is None or item.get("kind") in selected_kinds)
                 and not item.get("intermediate") and item.get("delivery_status") not in {"candidate", "intermediate"}]
    if not files:
        return ToolResult("没有找到符合范围的文件。", summary="没有可查看的文件")

    public_fields = {"file_id", "filename", "kind", "kind_label", "size", "customer_id", "conversation_id", "run_id", "created_at", "sheets", "media_type", "preview_url", "intermediate", "delivery_status"}
    events = [{"type": "file", **{key: value for key, value in item.items() if key in public_fields},
               "download_url": f"/api/files/{item['file_id']}/download", "explicit": True} for item in files]
    summary = f"可查看和下载 {len(files)} 个文件。"
    return ToolResult(
        summary + "界面已提供原文件的查看和下载卡片；只需简短说明，不要读取或转写原表、重新导出副本或编造下载链接。"
        "若本轮还明确要求分析或修改，可继续完成那些要求。",
        summary=summary, public_text=summary, events=events,
        public_details=[public_detail_section("展示文件", [_file_detail(item) for item in files]),
                        public_detail_section("提供内容", ["已提供以上文件的查看和下载入口。"])],
    )


def _tool_load_file(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    infos = ctx.host.load_file(str(args.get("file_id", "")))
    names = [f"「{t.name}」{t.rows} 行 × {len(t.columns)} 列" for t in infos]
    return ToolResult("已加载：" + "、".join(names) + "。可以用 table_info 查看结构。", summary="加载了 " + "、".join(t.name for t in infos),
                      tables=[table_payload(ctx.ws, t.name) for t in infos],
                      public_details=[public_detail_section("加载来源和结果", [_table_detail(info) for info in infos]),
                                      public_detail_section("已加载字段", [f"「{info.name}」：{'、'.join(info.column_names)}" for info in infos])])


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
    _assert_table_scope(ctx, [table for table, _ in sheets])
    info = ctx.host.export_excel(sheets, str(args.get("filename") or "导出结果"))
    return ToolResult(f"已导出文件「{info['filename']}」，包含 {len(sheets)} 个 sheet，用户可以在右侧下载。",
                      summary=f"导出「{info['filename']}」", events=[{"type": "file", **info}],
                      public_details=[public_detail_section("数据来源与写入位置", [f"「{table}」{ctx.ws.require(table).rows} 行 → 「{info['filename']}」的「{sheet}」工作表" for table, sheet in sheets])])


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
    activate = getattr(ctx.host, "activate_recipe", None)
    if activate is not None:
        activate(recipe)
    mapping = _resolve_inputs(ctx, recipe, args.get("inputs"))
    expected_tables = [str(t) for t in (args.get("expected_tables") or _default_expected(ctx))]
    previous = recipe.metadata.get("requirement") or recipe.description or ""
    requirement = (
        f"历史处理说明（仅沿用未被本次要求覆盖的部分）：\n{previous}\n\n"
        f"本次修改要求（沿用用户维护的流程记忆；未明确取舍的冲突先澄清，用户已明确本次例外则直接执行）：\n{instruction}"
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
    activate = getattr(ctx.host, "activate_recipe", None)
    if activate is not None:
        activate(recipe)
    mapping = _resolve_inputs(ctx, recipe, args.get("inputs"))
    host = ctx.host.pipeline_host()
    handle = host.open_run("replay", recipe, f"在对话中执行「{recipe.name}」")
    from sheetmate.agent.repair import LLMStepRepairer

    repairer = LLMStepRepairer(ctx.llm) if args.get("allow_repair", True) else None
    runner = RecipeRunner(recipe, ctx.library, handle.ws, field_mapping=ctx.host.field_mapping(),
                          field_mapping_authoritative=bool(getattr(ctx.host, "field_mapping_authoritative", lambda: False)()),
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
    saved = ctx.host.store_recipe(recipe, note="按用户要求保存本次处理流程")
    steps = "；".join(f"{s.id}. {s.name}（{s.formula}）" for s in recipe.ordered_steps())
    return ToolResult(f"已保存流程「{recipe.name}」v{saved['version']}（recipe_id={saved['recipe_id']}），共 {len(recipe.steps)} 步：{steps}。"
                      "界面已展示这一个正式流程，无需再调用 get_recipe 展示，或再次保存相同流程。",
                      summary=f"保存流程「{recipe.name}」", events=[{"type": "recipe_saved", **saved, "flow": recipe.flow()}])


def _tool_list_recipes(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    recipes = ctx.host.list_recipes()
    if not recipes:
        return ToolResult("当前客户或会话没有已保存的流程。", summary="没有已保存的流程")
    lines = [f"- recipe_id={item['recipe_id']}「{item['name']}」v{item['version']}"
             for item in recipes]
    return ToolResult("当前可访问的已保存流程：\n" + "\n".join(lines), summary=f"找到 {len(recipes)} 个已保存流程")


def _tool_delete_recipe(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    recipe_id = str(args.get("recipe_id") or "")
    if not recipe_id:
        raise ValueError("请先用 list_recipes 找到要删除的流程 ID。")
    return _delete_recipes([recipe_id], ctx)


def _tool_delete_recipes(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    if args.get("all") is True:
        targets = [item["recipe_id"] for item in ctx.host.list_recipes()]
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
        targets = [item["recipe_id"] for item in ctx.host.list_recipes()]
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
        lines.extend([f"流程「{recipe.name}」v{recipe.version}：{recipe.description}", "输入："])
        for inp in recipe.inputs:
            lines.append(f"- {inp.alias}：{inp.description}；文件规则 {inp.file_pattern or '无'}；必需列 {inp.required_columns}")
        lines.append("步骤：")
        for step in recipe.ordered_steps():
            lines.append(f"{step.id}. {step.name}［{step.formula}］{step.description} 参数={json.dumps(step.params, ensure_ascii=False)} → {step.output}")
        lines.append("输出：" + "、".join(f"{o.name}←{o.source}" for o in recipe.output_spec))
        events.append({
            "type": "recipe_view", "recipe_id": recipe.id, "name": recipe.name,
            "description": recipe.description, "version": recipe.version,
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
    receipt = ctx.host.remember(category, key, value, evidence_span=str(args.get("evidence_span") or ""))
    if not isinstance(receipt, dict):
        return ToolResult("记忆保存未返回可验证的写入结果，本轮不能确认已保存。", ok=False,
                          summary="记忆写入结果未确认")
    status = receipt.get("status")
    scope = str(receipt.get("scope_label") or "记忆")
    actual_key, actual_value = str(receipt.get("key") or key), str(receipt.get("value") or "")
    if status == "needs_evidence":
        return ToolResult("记忆未保存：" + str(receipt.get("reason") or "需要可核实的用户原话证据。"),
                          ok=False, summary=f"记忆证据不足：{actual_key}")
    if status == "blocked":
        return ToolResult(f"未更新（{scope}）：人工规则受到保护。当前保存的是 {actual_key} = {actual_value}。",
                          summary=f"未覆盖人工记忆：{actual_key}")
    if status == "unchanged":
        return ToolResult(f"记忆未变更（{scope}）：{actual_key} = {actual_value}。", summary=f"记忆未变更：{actual_key}")
    if status not in {"created", "updated"}:
        return ToolResult("记忆保存未返回可验证的写入状态，本轮不能确认已保存。", ok=False,
                          summary="记忆写入结果未确认")
    label = "已记住" if status == "created" else "已更新记忆"
    return ToolResult(f"{label}（{scope}）：{actual_key} = {actual_value}", summary=f"{label}：{actual_key}",
                      events=[{"type": "memory_saved", "items": [receipt], "scope": scope, "status": status}])


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
        Tool("read_tool_result", "按结果引用分页读取已保存的完整安全工具结果。预览不足时回查；省略部分不代表不存在。text 按文本分页，tables/events/payload 等结构按 JSON 字符分页。只能读取当前会话和当前专员范围内的结果。",
             _obj({"result_id": _STR, "section": {"type": "string", "enum": [
                 "text", "summary", "tables", "events", "public_text", "public_summary", "public_details", "error_details", "payload"]},
                   "offset": {"type": "integer", "minimum": 0},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 8000}}, ["result_id"]), both,
             run=lambda args, ctx: ToolResult(json.dumps(ctx.host.read_tool_result(
                 **args, branch_id=ctx.branch_id), ensure_ascii=False), summary="已读取完整工具结果片段"),
             label=lambda args: "回查工具结果"),
        Tool("inspect_execution_evidence", "按证据 ID 分页读取之前实际工具返回的完整原文与源表版本。上下文摘要不足时回查，不能把已压缩摘要当成完整数据。",
             _obj({"evidence_id": _STR, "offset": {"type": "integer", "minimum": 0},
                   "limit": {"type": "integer", "minimum": 100, "maximum": 8000}}, ["evidence_id"]), both,
             run=_tool_inspect_evidence, label=lambda args: "回查执行证据"),
        Tool("inspect_task_context", "按消息 ID 分页读取当前任务的用户原始要求，或按关键词查找。摘要未完整展示的要求必须按需回查。",
             _obj({"message_id": {"type": "integer"}, "query": _STR, "offset": {"type": "integer", "minimum": 0},
                   "limit": {"type": "integer", "minimum": 100, "maximum": 8000}}), both,
             run=lambda args, ctx: ToolResult(json.dumps(inspect_task(ctx.task_state, **args), ensure_ascii=False), summary="已读取任务原始要求"),
             label=lambda args: "查看任务要求"),
        Tool("inspect_conversation_history", "按需检索本会话全部历史原消息，包括已压缩内容和已完成旧任务。省略 message_id 时按关键词/角色检索片段，offset/limit 为消息条数（默认8，最多12）；指定 message_id 后分页读取原文，offset/limit 为字符数（默认4000，最多8000）。不读取其他会话；旧助手声明不代表当前事实或完成证据。",
             _obj({"message_id": {"type": "integer", "minimum": 1}, "query": {**_STR, "maxLength": 200},
                   "role": {"type": "string", "enum": ["user", "assistant"]},
                   "offset": {"type": "integer", "minimum": 0},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 8000}}), both,
             run=lambda args, ctx: ToolResult(json.dumps(ctx.host.inspect_conversation_history(**args), ensure_ascii=False),
                                              summary="已检索会话原始消息"),
             label=lambda args: "回查历史原文"),
        Tool("inspect_saved_memories", "只检索当前可用的个人/客户/会话/流程记忆。省略 memory_id 时按关键词找相关记忆或列出近期记忆，offset/limit 按条目分页（最多12条）；指定 memory_id 后按字符分页读取原文。结果带记忆版本、来源和范围；不扩大当前权限范围。",
             _obj({"memory_id": {"type": "integer", "minimum": 1}, "query": {**_STR, "maxLength": 200},
                   "offset": {"type": "integer", "minimum": 0},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 8000}}), both,
             run=lambda args, ctx: ToolResult(json.dumps(ctx.host.inspect_saved_memories(**args), ensure_ascii=False),
                                              summary="已检索当前作用域记忆"),
             label=lambda args: "回查已保存记忆"),
        Tool("load_tool_group", "按需加载工具定义：formulas=清洗/匹配/汇总公式，workbook=建表与格式，python=脚本，workflow=已有流程/保存/删除，all=全部。只加载能力，不执行或授权操作。",
             _obj({"group": {"type": "string", "enum": ["formulas", "workbook", "python", "workflow", "all"]}}, ["group"]), both,
             run=_tool_load_group, label=lambda args: "加载处理工具"),
        *python_script_tools(),
        workbook_creation_tool(),
        workbook_inspection_tool(),
        Tool("table_info", "［表格探测技能］读取当前表版本的结构化长期记忆：全部列名、类型、空值、不同值数量、样本、候选主键和前几行。首次补测后自动持久保存，表未更新直接复用。系统已提供所需结构时直接使用，仅缺少必要信息时调用。",
             _obj({"table": {**_STR, "description": "表名"}}, ["table"]), both, run=_tool_table_info,
             label=lambda a: f"查看结构 · {a.get('table', '')}"),
        Tool("value_counts", "统计某一列的取值分布（出现次数和占比），按当前表版本和参数保存并复用探测结果。已有相同范围分布时直接使用。",
             _obj({"table": _STR, "column": _STR, "limit": {"type": "integer", "minimum": 1, "maximum": 10000, "description": "最多返回多少个取值，默认 20，最多 10000"}},
                  ["table", "column"]), both, run=_tool_value_counts, label=lambda a: f"取值分布 · {a.get('column', '')}"),
        Tool("column_stats", "列的描述统计：最小、最大、均值、分位数、空值比例，按当前表版本和字段保存并复用。已有所需统计时直接使用。",
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
             _obj({"sheets": {"type": "array", "items": {"anyOf": [_STR, _obj({"table": _STR, "sheet_name": _STR}, ["table"])]}},
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
        Tool("save_as_recipe", "仅用户明确要求保存重复流程时调用，直接保存正式流程。支持公式、SQL、Python 脚本和混合流程；保存实际执行的代码与依赖，不需要重新处理文件。"
             "若 build_recipe、revise_recipe 或 run_recipe 已返回 recipe_saved，则同一流程已经保存，不要再次调用本工具。保存后界面自动展示流程图，无需再调用 get_recipe。",
             _obj({"output_tables": {**_STR_LIST, "description": "可选：公式、SQL 或脚本生成的最终结果表。不填则保存指定或最近已执行流程"}, "name": _STR,
                   "run_id": {**_STR, "description": "可选：明确指定脚本或混合流程的运行 ID，优先于 recipe_id"},
                   "recipe_id": {**_STR, "description": "可选：指定本会话已执行的流程 ID，不填用最近的"},
                   "description": {**_STR, "description": "流程说明"}}),
             both, run=_tool_save_as_recipe, label=lambda a: f"保存流程 · {a.get('name', '')}"),
        Tool("get_recipe", "在本轮对话中展示客户页同款原生流程图：单个用 recipe_id，多个用 recipe_ids，查看所有流程图直接用 all=true。界面自动绘制输入、步骤和输出，模型只需简短说明，不要画字符图或 Mermaid。只读，不执行、修改或保存流程。",
             _obj({"recipe_id": {**_STR, "description": "查看单个流程时指定 ID，与 recipe_ids、all=true 三选一"},
                   "recipe_ids": {**_STR_LIST, "description": "查看指定多个流程的 ID 列表"},
                   "all": {"type": "boolean", "description": "true 表示展示当前客户或会话全部已保存流程的原生流程图，无需先查 ID"}}),
             both, run=_tool_get_recipe, label=lambda a: "查看全部流程图" if a.get("all") is True else "查看流程"),
        Tool("list_recipes", "查看当前客户或会话已保存的流程及准确 ID。查找待删除流程或区分同名流程时使用。",
             _obj({}), both, run=_tool_list_recipes, label=lambda a: "查看已保存流程"),
        Tool("delete_recipe", "删除指定的已保存流程及全部版本。调用后由系统列出目标，请用户确认一次；确认后系统直接删除，无需额外授权。删除表格行或文件不等于删除流程。",
             _obj({"recipe_id": {**_STR, "description": "要删除的已保存流程 ID"}}, ["recipe_id"]),
             both, run=_tool_delete_recipe, label=lambda a: "删除已保存流程"),
        Tool("delete_recipes", "批量删除已保存流程及全部版本。用户说删除全部流程时使用 all=true；指定多条时提供 recipe_ids。系统列出清单并请用户确认一次，确认后直接执行。",
             _obj({"all": {"type": "boolean", "description": "true表示当前客户或会话的全部已保存流程"},
                   "recipe_ids": {**_STR_LIST, "description": "指定要删除的流程 ID 列表；all=true时无需提供"}}),
             both, run=_tool_delete_recipes, label=lambda a: "删除全部流程" if a.get("all") is True else "批量删除流程"),
        Tool("remember", "记住用户明确表达的偏好、业务规则或背景。必须引用本轮用户原话，不得把模型猜测或表格样本推断保存为规则；经验记忆需要已验证执行证据，当前工具不支持。",
             _obj({"category": {"type": "string", "enum": ["preferences", "domain_knowledge", "user_context", "history_insights"]},
                   "key": _STR, "value": {**_STR, "description": "逐字摘自用户原话的完整规则，不得改写推断；保留否定、条件及范围，不得保存本次临时例外。"},
                   "evidence_span": {**_STR, "description": "支持本条记忆的用户消息确切原文，不可引用助手文字或工具数据。"}},
                  ["category", "key", "value"]), both, run=_tool_remember,
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


def _tool_load_group(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    ctx.enabled_tool_groups.add(str(args["group"]))
    return ToolResult("相应工具已加载，下一轮可直接调用。加载工具不代表已经执行任何处理或获得保存/删除授权。", summary="已加载所需工具")


def _tool_inspect_evidence(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    item = ctx.evidence_store.get(str(args["evidence_id"]))
    if item is None:
        resolver = getattr(ctx.host, "resolve_execution_evidence", None)
        if callable(resolver):
            item = resolver(str(args["evidence_id"]))
            if item is not None:
                ctx.evidence_store[str(args["evidence_id"])] = item
    if item is None or (ctx.branch_id and item.get("facts", {}).get("branch_id") != ctx.branch_id):
        raise ValueError("当前范围没有这条执行证据，请使用本任务实际返回的证据 ID。")
    text = str(item.get("text") or "")
    offset = max(0, int(args.get("offset") or 0))
    limit = min(8000, max(100, int(args.get("limit") or 4000)))
    payload = {"evidence_id": args["evidence_id"], "facts": item.get("facts"), "summary": item.get("summary"),
               "text": text[offset:offset + limit], "total_chars": len(text), "offset": offset,
               "next_offset": offset + limit if offset + limit < len(text) else None}
    return ToolResult(json.dumps(payload, ensure_ascii=False), summary="已回查原始执行证据")


class ToolRegistry:
    # Read-only analysis keeps the common path small while preserving all
    # formula tools (they are often the cheapest exact operation). Heavy
    # workflow/code tools remain available through the full group.
    _HEAVY_TOOL_NAMES = frozenset({
        "run_python_script", "inspect_python_script", "create_workbook", "inspect_workbook_creation",
        "build_recipe", "revise_recipe", "run_recipe", "save_as_recipe", "get_recipe",
        "list_recipes", "delete_recipe", "delete_recipes",
    })
    _BRANCH_TOOL_NAMES = frozenset({"table_info", "value_counts", "column_stats", "preview_table", "run_sql", "ask_user", "inspect_task_context", "inspect_execution_evidence", "read_tool_result"})
    _GROUP_TOOLS = {
        "workbook": frozenset({"create_workbook", "inspect_workbook_creation"}),
        "python": frozenset({"run_python_script", "inspect_python_script"}),
        "workflow": frozenset({"build_recipe", "revise_recipe", "run_recipe", "save_as_recipe", "get_recipe", "list_recipes", "delete_recipe", "delete_recipes"}),
    }

    def __init__(self) -> None:
        self._static = {t.name: t for t in _static_tools()}
        for name in ("run_sql", "column_stats", "value_counts", "table_info", "preview_table"):
            self._static[name].process_safe = True
        for name in ("column_stats", "value_counts", "table_info", "preview_table"):
            self._static[name].parallel_read_safe = True

    def for_context(self, ctx: AgentContext, *, group: str | None = None) -> list[Tool]:
        tools = [t for t in self._static.values() if ctx.mode in t.modes and (not t.needs_customer or ctx.customer)]
        selected_group = group or ctx.tool_group
        if ctx.table_filter is not None:
            # Specialists analyse one slice; file/recipe/memory side effects
            # and cross-table work are owned by the coordinating agent.
            return [tool for tool in tools if tool.name in self._BRANCH_TOOL_NAMES]
        if selected_group == "core":
            enabled = self._HEAVY_TOOL_NAMES if "all" in ctx.enabled_tool_groups else frozenset().union(
                *(self._GROUP_TOOLS.get(group, frozenset()) for group in ctx.enabled_tool_groups))
            tools = [tool for tool in tools if tool.name not in self._HEAVY_TOOL_NAMES or tool.name in enabled]
        if ctx.mode in {"analyze", "modify", "create"} and (
            selected_group != "core" or bool(ctx.enabled_tool_groups & {"formulas", "all"})
        ):
            for spec in ctx.library.customer_specs():
                tools.append(formula_tool(spec, "customer"))
            for spec in ctx.library.builtin_specs():
                if spec.id != "custom_sql":
                    tools.append(formula_tool(spec, "builtin"))
        # 与选择规则一致，脚本入口放在公式、SQL 和流程工具之后。
        tools.sort(key=lambda tool: tool.name == "run_python_script")
        return tools

    def core_for_context(self, ctx: AgentContext) -> list[Tool]:
        """Return the low-cost read/transform tool set for a common analysis turn."""
        return self.for_context(ctx, group="core")

    def load_named_tool(self, name: str, ctx: AgentContext) -> Tool | None:
        """A remembered tool name may activate its group, never widen scope."""
        if ctx.table_filter is not None:
            return None
        tool = next((item for item in self.for_context(ctx, group="full") if item.name == name), None)
        if tool is not None:
            group = next((key for key, names in self._GROUP_TOOLS.items() if name in names),
                         "formulas" if name not in self._static else None)
            if group:
                ctx.enabled_tool_groups.add(group)
        return tool

    def find(self, name: str, ctx: AgentContext) -> Tool | None:
        for tool in self.for_context(ctx):
            if tool.name == name:
                return tool
        return None


class _ToolCancelled(BaseException):
    """Bypass business error recovery when a revoked invocation unwinds."""


class ToolCleanupPending(RuntimeError):
    code = "tool_cleanup_pending"


class _CallCapability:
    """Revocation fences a late worker's next workspace/host operation.

    An already-entered host operation remains owned by its job until its
    real worker future completes; this proxy never claims it killed native IO.
    """

    def __init__(self, target: Any, cancelled: threading.Event):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_cancelled", cancelled)

    def __setattr__(self, name: str, value: Any) -> None:
        if self._cancelled.is_set():
            raise _ToolCancelled()
        setattr(self._target, name, value)

    def __delattr__(self, name: str) -> None:
        if self._cancelled.is_set():
            raise _ToolCancelled()
        delattr(self._target, name)

    def __getattr__(self, name: str) -> Any:
        if self._cancelled.is_set():
            raise _ToolCancelled()
        value = getattr(self._target, name)
        if not callable(value):
            return value

        def invoke(*args, **kwargs):
            if self._cancelled.is_set():
                raise _ToolCancelled()
            return value(*args, **kwargs)
        return invoke


async def _sync_tool(tool: Tool, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    """Bound the waiter while retaining ownership of every real live worker."""
    from sheetmate.agent.tool_worker import DataWorkerCancelled, run_data_tool
    from sheetmate.model_policy import LLMRuntimePolicy

    policy = getattr(getattr(ctx.llm, "cfg", None), "runtime_policy", None) or LLMRuntimePolicy()
    timeout = getattr(policy, "tool_timeout_seconds", 1800)
    grace = getattr(policy, "cancellation_grace_seconds", 5)
    cancelled = threading.Event()
    publication = threading.Lock()
    future: Future[ToolResult] = Future()
    ws = _CallCapability(ctx.ws, cancelled)
    host = copy(ctx.host) if ctx.host is not None else None
    host_baseline = dict(vars(host)) if host is not None and hasattr(host, "__dict__") else {}
    # Mutable bookkeeping also stays private until this invocation settles;
    # late native callbacks must not append to the live host after revocation.
    for name, value in host_baseline.items():
        if type(value) in {list, dict, set}:
            setattr(host, name, copy(value))
    if host is not None and hasattr(host, "ws"):
        host.ws = ws
    worker_ctx = replace(ctx, ws=ws, host=_CallCapability(host, cancelled) if host is not None else None)
    invocation_context = contextvars.copy_context()

    def publish_host() -> None:
        if host is None or not hasattr(host, "__dict__"):
            return
        with publication:
            if cancelled.is_set():
                return
            changes = {}
            missing = object()
            for name, value in vars(host).items():
                if name == "ws":
                    continue
                previous = host_baseline.get(name, missing)
                if value is previous:
                    continue
                if type(value) in {list, dict, set} and type(value) is type(previous):
                    try:
                        if value == previous:
                            continue
                    except (TypeError, ValueError):
                        pass
                changes[name] = value
            vars(ctx.host).update(changes)
            for name in host_baseline.keys() - vars(host).keys() - {"ws"}:
                vars(ctx.host).pop(name, None)

    def trace(frame, event, arg):
        if (cancelled.is_set() and event == "line"
                and frame.f_globals.get("__name__") not in {__name__, "sheetmate.agent.tool_worker"}):
            raise _ToolCancelled()
        return trace

    def run():
        future.set_running_or_notify_cancel()
        try:
            # The cooperative tracer handles Python work; native pure data
            # computations run in a separate, killable process instead.
            if not tool.process_safe:
                # Restrict cancellation checks to the tool and domain code.
                # Raising inside SQLAlchemy/SQLite/threading cleanup would
                # interrupt the very rollback/release paths we must preserve.
                implementation_module = getattr(tool.run, "__module__", "")

                def domain_trace(frame, event, arg):
                    module = frame.f_globals.get("__name__", "")
                    if (module.startswith("sheetmate.db.")
                            or module in {"sheetmate.engine.workspace", "sheetmate.engine.file_copies",
                                          "sheetmate.services.analysis"}
                            or frame.f_code.co_name in {"close", "aclose", "__exit__", "__aexit__", "cleanup"}):
                        return None
                    if module == implementation_module or module.startswith("sheetmate."):
                        return trace(frame, event, arg)
                    return None

                sys.settrace(domain_trace)
            result = (run_data_tool(tool.name, args, worker_ctx, cancelled) if tool.process_safe
                      else tool.run(args, worker_ctx))
            if cancelled.is_set():
                raise _ToolCancelled()
        except (_ToolCancelled, DataWorkerCancelled):
            sys.settrace(None)
            future.set_exception(_ToolCancelled())
        except BaseException as exc:
            sys.settrace(None)
            publish_host()
            future.set_exception(exc)
        else:
            sys.settrace(None)
            publish_host()
            future.set_result(result)
        finally:
            sys.settrace(None)

    with phase("executing_tool", tool.name) as item:
        register_tool_worker(future)
        thread = threading.Thread(target=lambda: invocation_context.run(run), daemon=True,
                                  name=f"sheetmate-tool-{tool.name}")
        thread.start()
        wrapped = asyncio.wrap_future(future)

        async def revoke() -> bool:
            with publication:
                cancelled.set()
            ctx.ws.interrupt()
            done, _ = await asyncio.wait({wrapped}, timeout=grace)
            if done:
                if not wrapped.cancelled():
                    wrapped.exception()
                return True
            wrapped.add_done_callback(lambda value: value.exception() if not value.cancelled() else None)
            await report_cleanup(item)
            return False

        try:
            done, _ = await asyncio.wait({wrapped}, timeout=timeout)
            if done:
                return wrapped.result()
            await report_wait(item, elapsed=timeout, hard=True, code="tool_timeout")
            if not await revoke():
                # No retry may overlap an unfinished side-effect-capable call.
                raise ToolCleanupPending("工具已超时，正在清理资源；清理完成前不能开始下一次处理。")
            return ToolResult(f"工具「{tool.name}」超过 {timeout:g} 秒执行时限，已停止本次尝试。",
                              ok=False, summary="本次工具执行超时", error_kind="timeout")
        except asyncio.CancelledError:
            await revoke()
            raise


async def _stream_tool(tool: Tool, args: dict[str, Any], ctx: AgentContext) -> AsyncIterator[Any]:
    """Own streaming-tool silence separately from a nested model's idle clock."""
    from sheetmate.model_policy import LLMRuntimePolicy

    policy = getattr(getattr(ctx.llm, "cfg", None), "runtime_policy", None) or LLMRuntimePolicy()
    timeout = getattr(policy, "tool_timeout_seconds", 1800)
    grace = getattr(policy, "cancellation_grace_seconds", 5)
    stream = tool.stream(args, ctx)
    with phase("executing_tool", tool.name) as item:
        while True:
            task = asyncio.create_task(anext(stream))
            marker: Future[None] = Future()
            register_tool_worker(marker)
            task.add_done_callback(lambda done, marker=marker: marker.set_result(None))

            async def revoke() -> bool:
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=grace)
                if done:
                    if not task.cancelled():
                        task.exception()
                    return True
                task.add_done_callback(lambda value: value.exception() if not value.cancelled() else None)
                await report_cleanup(item)
                return False

            try:
                if not await wait_tool(task, item, timeout):
                    await report_wait(item, elapsed=timeout, hard=True, code="tool_timeout")
                    if not await revoke():
                        raise ToolCleanupPending("流式工具已超时，正在等待执行器完成清理。")
                    yield ToolResult(f"工具「{tool.name}」连续 {timeout:g} 秒未返回进展，已停止本次尝试。",
                                     ok=False, summary="本次工具执行超时", error_kind="timeout")
                    return
                try:
                    value = task.result()
                except StopAsyncIteration:
                    return
                yield value
            except asyncio.CancelledError:
                await revoke()
                raise


async def execute_tool(tool: Tool, args: dict[str, Any], ctx: AgentContext, *,
                       call_id: str | None = None) -> AsyncIterator[Any]:
    """统一执行：同步工具放线程里跑；流式工具逐个转发事件。最后一定产出一个 ToolResult。"""
    call_id = call_id or uuid4().hex
    started = time.perf_counter()
    implementation = tool.stream or tool.run
    function = (f"{getattr(implementation, '__module__', type(implementation).__module__)}."
                f"{getattr(implementation, '__qualname__', type(implementation).__qualname__)}") if implementation else ""
    metadata = {"call_id": call_id, "tool": tool.name, "implementation": function,
                "conversation_id": ctx.conversation_id}
    record_event("tool.input", function, args, **metadata)
    status = "interrupted"
    validated = False
    outcome: ToolResult | None = None
    failure: dict[str, str] | None = None
    data_events: list[dict[str, Any]] = []
    try:
        try:
            args = validate_tool_args(tool, args)
        except StructuredOutputError as exc:
            outcome = tool_argument_error(tool.name, "schema", exc.errors)
            status = "rejected"
            yield outcome
            return
        validated = True
        if tool.stream is not None:
            got_result = False
            async for item in _stream_tool(tool, args, ctx):
                if isinstance(item, ToolResult):
                    got_result = True
                    outcome = item
                    status = "ok" if item.ok else "failed"
                elif isinstance(item, dict) and item.get("type") in _LOG_DATA_EVENTS:
                    data_events.append(deepcopy(item))
                yield item
            if not got_result:
                outcome = ToolResult("工具没有返回结果", ok=False)
                status = "failed"
                yield outcome
        else:
            assert tool.run is not None
            outcome = await _sync_tool(tool, args, ctx)
            status = "ok" if outcome.ok else "failed"
            yield outcome
    except (asyncio.CancelledError, GeneratorExit) as exc:
        if outcome is None:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "interrupted"
            failure = {"reason": type(exc).__name__}
        raise
    except ToolCleanupPending as exc:
        status = "cleanup_pending"
        failure = {"error_type": type(exc).__name__, "error": str(exc), "code": exc.code}
        raise
    except Exception as exc:  # 工具出错时把错误当结果交给 LLM，让它自己纠正
        message = str(exc) or type(exc).__name__
        status = "error"
        failure = {"error_type": type(exc).__name__, "error": message}
        if not validated:
            # A broken local schema is a configuration fault, not a business
            # tool failure that the model can repair by changing its arguments.
            raise
        outcome = ToolResult(f"工具 `{tool.name}` 出错：{message}", ok=False, summary=message[:200])
        yield outcome
    finally:
        event = "tool.result" if outcome is not None else "tool.cancelled" if status in {"cancelled", "interrupted"} else "tool.error"
        extra_events = [item for item in data_events if outcome is None or item not in outcome.events]
        record_event(event, function if validated else "sheetmate.agent.tools.validate_tool_args", outcome or failure,
                     status=status, duration_ms=int((time.perf_counter() - started) * 1000),
                     **({"events": extra_events} if extra_events else {}), **metadata)
