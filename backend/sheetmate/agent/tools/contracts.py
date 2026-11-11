"""contracts tool capabilities."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from jsonschema import Draft202012Validator
from sheetmate.agent.llm.structured import StructuredOutputError, validate_payload
from sheetmate.agent.llm.protocol import LanguageModel


if TYPE_CHECKING:
    from sheetmate.engine.formulas import FormulaLibrary
    from sheetmate.engine.tables.workspace import TableInfo, Workspace
    from sheetmate.recipe.execution.pipeline import PipelineHost
    from sheetmate.recipe.definition.schema import Recipe


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
    def recipe_saved_version(self, recipe_id: str) -> int: ...
    def prune_recipe_results(self, recipe_id: str, keep_names: set[str]) -> None: ...
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
    def assert_live_context_scope(self) -> None: ...
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
    llm: LanguageModel
    conversation_id: str
    customer: dict[str, Any] | None = None  # 客户资料及独立的当前流程说明，由服务层每轮刷新
    max_repair: int = 3
    verify_rounds: int = 2
    table_filter: set[str] | None = None  # 多表并行时，每个专员只看自己的表
    save_recipe_requested: bool = False  # 仅来自用户明确的保存意图，模型工具参数不能授权
    recipe_edit: dict[str, Any] = field(default_factory=dict)  # 服务层验证的流程图对话目标与基础版本
    delete_recipe_ids: set[str] = field(default_factory=set)  # 用户对持久化待删除清单确认后，允许本轮删除的流程
    image_parts: list[dict[str, Any]] = field(default_factory=list)  # 当前消息的图片，仅放入多模态请求，不写入提示词文本
    file_memory: str = ""  # 已授权但尚未加载的原件结构与探测事实，每轮按文件版本刷新
    replace_file_ids: tuple[str, ...] = ()  # 本轮成功交付后更新的旧结果，由处理意图判断提供
    tool_group: str | None = None  # 可选：core 只暴露低成本分析工具，full 保持完整工具集
    branch_id: str | None = None  # 并行子任务的证据归属；协调器为空
    task_state_block: str = ""  # 服务层生成的结构化当前任务状态摘要
    task_state: dict[str, Any] = field(default_factory=dict)
    enabled_tool_groups: set[str] = field(default_factory=set)
    requested_tool_groups: tuple[str, ...] = ()  # 复用本轮意图判断的语义提示，运行时仍可按需加载。
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
