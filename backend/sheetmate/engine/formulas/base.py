"""公式（Formula）的定义、参数校验与 SQL 模板渲染。

SheetMate 的指令三层：
- 原语：join / group / unstack / fillna … 由 DuckDB 原生提供
- 公式：可复用的变换模板 = 参数声明（params）+ SQL（内置公式由 Python 生成 SQL，客户公式是 {{param}} 模板）
- 配方（Recipe）：一串公式的有序组合，对应一个客户的完整流程

参数有两种身份：公式里的 params 是「声明」（类型/必填/默认值/说明），Recipe 步骤里的 params 是「取值」。
校验器只做一件事：取值必须满足声明。
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from sheetmate.engine.sqlutil import (
    UnsafeSQLError,
    assert_safe_expression,
    assert_single_select,
    quote_ident,
    quote_literal,
)
from sheetmate.engine.workspace import Workspace, WorkspaceError

ParamType = Literal[
    "table",  # 工作区里的一张表
    "tables",  # 多张表
    "column",  # 某张表里的一列（of 指向表参数）
    "columns",  # 某张表里的多列
    "str",
    "int",
    "number",
    "bool",
    "list",
    "enum",
    "expr",  # SQL 表达式片段（筛选条件、计算公式）
    "sql",  # 完整的 SELECT 查询
    "dict",  # 映射表，例如 {旧值: 新值}
    "any",
]

_PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_一-鿿][\w一-鿿]*)\s*\}\}")
_PARAM_NAME = re.compile(r"[A-Za-z_一-鿿][\w一-鿿]*")  # 参数名的写法和模板里的 {{参数名}} 一致
MAX_PARAM_NAME = 64
RESERVED_PARAM_NAMES = {"result_name"}  # 公式工具自带的「结果表名」参数，客户公式不能再声明


class FormulaError(ValueError):
    """公式参数不合法或执行前检查失败。消息会直接给 AI 用于修复，所以要具体。"""


class ParamSpec(BaseModel):
    """参数声明（open schema：未知字段忽略，方便扩展）。"""

    model_config = {"extra": "ignore", "populate_by_name": True}

    type: ParamType = "any"
    required: bool = False
    default: Any = None
    desc: str = ""
    values: list[Any] | None = None  # enum 的可选值
    item_type: str | None = None  # list 的元素类型
    of: str | None = None  # column / columns 属于哪个表参数
    schema_: dict[str, Any] | None = Field(default=None, alias="schema")  # 可选：覆盖生成工具时的 JSON Schema


class Formula(BaseModel):
    """一个公式。内置公式 type=builtin（Python 生成 SQL）；客户公式 type=sql（{{param}} 模板）。"""

    model_config = {"extra": "ignore"}

    id: str = Field(min_length=1, max_length=64)
    version: int = 1
    scope: Literal["global", "customer"] = "global"
    customer_id: str | None = None
    type: Literal["builtin", "sql"] = "sql"
    name: str
    category: str = "自定义"
    intent: str = ""  # 一句话人话说明，流程图上展示
    triggers: list[str] = Field(default_factory=list)  # 检索关键词
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    sql: str | None = None
    example: dict[str, Any] | None = None
    status: Literal["draft", "active", "deprecated"] = "active"
    created_by: Literal["ai", "human"] = "human"
    tags: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_\-]*", value):
            raise ValueError("公式 id 只能用小写字母、数字、下划线、短横线，且以字母开头")
        return value

    def check_definition(self) -> None:
        """客户公式的定义检查（保存、试运行之前都会调用），消息直接展示给用户：

        1. 名称、参数声明合法（参数名、枚举的可选值、column 所属的表、默认值能转成声明的类型）
        2. 模板里的占位符都声明了、没有写在引号里、没有用到对象类型的参数
        3. 参数用示例值代入后是一条合法的 SELECT
        """
        if self.type != "sql":
            return
        if not self.name.strip():
            raise FormulaError("公式名称不能为空")
        self._check_params()
        if not self.sql or not self.sql.strip():
            raise FormulaError("SQL 公式必须填写 sql 模板")
        used = set(_PLACEHOLDER.findall(self.sql))
        undeclared = used - set(self.params)
        if undeclared:
            raise FormulaError(f"模板里用到了未声明的参数：{sorted(undeclared)}")
        for name in sorted(used):
            if self.params[name].type == "dict":
                raise FormulaError(f"参数「{name}」是对象类型，不能直接放进 SQL 模板")
        quoted = quoted_placeholders(self.sql)
        if quoted:
            token = "{{" + quoted[0] + "}}"
            raise FormulaError(f"占位符 {token} 写在了引号里面。代入时会自动带上引号，请直接写 {token}，不要再包一层引号")
        if "{{" in _PLACEHOLDER.sub("", self.sql):
            raise FormulaError("模板里有写法不对的占位符，应该写成 {{参数名}}（参数名只能用中文、字母、数字和下划线）")
        self._check_renders()

    def _check_params(self) -> None:
        for name, p in self.params.items():
            if not _PARAM_NAME.fullmatch(name) or len(name) > MAX_PARAM_NAME:
                raise FormulaError(f"参数名「{name}」不合法：只能用中文、字母、数字和下划线，不能以数字开头，最长 {MAX_PARAM_NAME} 个字符")
            if name in RESERVED_PARAM_NAMES:
                raise FormulaError(f"参数名「{name}」是系统保留的，请换一个")
            if p.type == "enum" and not p.values:
                raise FormulaError(f"参数「{name}」是选项类型，请填写可选值")
            if p.type in {"column", "columns"} and p.of:
                target = self.params.get(p.of)
                if target is None or target.type != "table":
                    raise FormulaError(f"参数「{name}」所属的表必须是一个「表」类型的参数，现在是「{p.of}」")
            if not _is_missing(p.default):
                try:
                    _coerce(self, name, p, p.default)
                except FormulaError as exc:
                    detail = str(exc).removeprefix(f"参数「{name}」")
                    raise FormulaError(f"参数「{name}」的默认值{detail}") from exc

    def _check_renders(self) -> None:
        """参数用示例值代入，检查模板是不是一条合法的 SELECT（只检查语法，表和列是否存在要到执行时才知道）。"""
        samples = {name: _sample_value(p) for name, p in self.params.items()}
        text = _substitute(self, samples)
        try:
            assert_single_select(text)
        except UnsafeSQLError as exc:
            reason = str(exc).split("\n\nLINE")[0].strip()
            shown = text if len(text) <= 240 else text[:240] + "…"
            raise FormulaError(f"模板检查没通过：{reason}。参数用示例值代入后是：{shown}") from exc


@dataclass
class FormulaPlan:
    """公式编译结果：一条 SELECT（结果存成输出表）+ 附带信息。"""

    sql: str
    inputs: list[str]
    warnings: list[str] = field(default_factory=list)
    notes: dict[str, Any] = field(default_factory=dict)  # 例如拆列时实际用到的取值
    # 输出表建好后再计算的指标（例如匹配率），返回 dict
    post: Callable[[Workspace, str], dict[str, Any]] | None = None


Builder = Callable[[dict[str, Any], Workspace], FormulaPlan]
Describer = Callable[[dict[str, Any]], str]


@dataclass
class BuiltinFormula:
    """内置公式 = 定义 + SQL 生成函数 + 人话描述函数。"""

    spec: Formula
    build: Builder
    describe: Describer


# ---------------------------------------------------------------------------
# 参数校验：取值必须满足声明
# ---------------------------------------------------------------------------


def normalize_params(spec: Formula, values: dict[str, Any] | None, ws: Workspace) -> dict[str, Any]:
    """补默认值、做类型转换、检查必填/枚举/表和列是否存在。返回规整后的参数。"""
    values = dict(values or {})
    unknown = set(values) - set(spec.params)
    if unknown:
        allowed = "、".join(spec.params) or "（无参数）"
        raise FormulaError(f"公式「{spec.name}」没有参数 {sorted(unknown)}，可用参数：{allowed}")

    result: dict[str, Any] = {}
    for name, p in spec.params.items():
        raw = values.get(name, p.default)
        if _is_missing(raw):
            if p.required:
                raise FormulaError(f"公式「{spec.name}」缺少必填参数「{name}」（{p.desc or p.type}）")
            result[name] = None if p.default is None else p.default
            continue
        result[name] = _coerce(spec, name, p, raw)

    # 表必须存在；列必须在所属的表里
    for name, p in spec.params.items():
        value = result.get(name)
        if value is None:
            continue
        if p.type == "table":
            ws.require(value)
        elif p.type == "tables":
            for t in value:
                ws.require(t)
    for name, p in spec.params.items():
        value = result.get(name)
        if value is None or p.type not in {"column", "columns"} or not p.of:
            continue
        table = result.get(p.of)
        if not table:
            continue
        check_columns(ws, table, [value] if p.type == "column" else value)
    return result


def check_columns(ws: Workspace, table: str, columns: list[str]) -> None:
    """列不存在时给出可用列和最接近的候选，便于 AI 自我纠正。"""
    available = ws.require(table).column_names
    for col in columns:
        if col in available:
            continue
        guess = difflib.get_close_matches(str(col), available, n=2, cutoff=0.4)
        hint = f"，你是不是想用：{'、'.join(guess)}" if guess else ""
        raise FormulaError(f"列「{col}」不在表「{table}」里。可用的列：{'、'.join(available)}{hint}")


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) or value == [] or value == {}


def _coerce(spec: Formula, name: str, p: ParamSpec, raw: Any) -> Any:
    label = f"参数「{name}」"
    try:
        if p.type in {"table", "column", "str"}:
            if isinstance(raw, (list, tuple)) and len(raw) == 1:
                raw = raw[0]
            if isinstance(raw, dict):
                raise FormulaError(f"{label}应该是文本")
            return str(raw).strip()
        if p.type in {"tables", "columns", "list"}:
            if isinstance(raw, str):
                items = [s.strip() for s in re.split(r"[,，、]", raw) if s.strip()] if p.type != "list" else [raw]
            elif isinstance(raw, (list, tuple)):
                items = list(raw)
            else:
                items = [raw]
            if p.type in {"tables", "columns"}:
                items = [str(i).strip() for i in items if str(i).strip()]
            return items
        if p.type == "int":
            return int(float(raw))
        if p.type == "number":
            return float(raw)
        if p.type == "bool":
            if isinstance(raw, str):
                return raw.strip().lower() in {"true", "1", "yes", "是", "y"}
            return bool(raw)
        if p.type == "enum":
            value = str(raw).strip()
            if p.values and value not in [str(v) for v in p.values]:
                raise FormulaError(f"{label}只能是 {p.values} 之一，收到「{value}」")
            return value
        if p.type == "expr":
            return assert_safe_expression(str(raw))
        if p.type == "sql":
            return assert_single_select(str(raw))
        if p.type == "dict":
            if not isinstance(raw, dict):
                raise FormulaError(f"{label}应该是对象，例如 {{\"旧值\": \"新值\"}}")
            return raw
        return raw
    except UnsafeSQLError as exc:
        raise FormulaError(f"{label}：{exc}") from exc
    except (TypeError, ValueError) as exc:
        if isinstance(exc, FormulaError):
            raise
        raise FormulaError(f"{label}类型不对，需要 {p.type}，收到 {raw!r}") from exc


# ---------------------------------------------------------------------------
# 客户 SQL 公式：{{param}} 模板渲染（安全替换，防注入）
# ---------------------------------------------------------------------------


def quoted_placeholders(sql: str) -> list[str]:
    """找出写在引号里面的占位符（'%{{x}}%' 或 "{{x}}"）。

    占位符代入时会按类型自己带上引号（文本 → 'abc'，列名 → "abc"），外面再包一层引号就错了，
    这是写模板最常见的失误。注释里的引号不算；引号没配对时返回空（交给语法检查报错）。
    """
    found: list[str] = []
    i, n, quote = 0, len(sql), ""
    while i < n:
        ch = sql[i]
        if quote:
            if ch == quote:
                if sql[i + 1 : i + 2] == quote:  # 引号里的转义：'' 或 ""
                    i += 2
                    continue
                quote = ""
            else:
                match = _PLACEHOLDER.match(sql, i)
                if match:
                    found.append(match.group(1))
                    i = match.end()
                    continue
        elif ch in {"'", '"'}:
            quote = ch
        elif sql.startswith("--", i):
            end = sql.find("\n", i)
            i = n if end < 0 else end
            continue
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        i += 1
    return [] if quote else found


def _sample_value(p: ParamSpec) -> Any:
    """检查模板语法时代入的示例值（按参数类型）。"""
    if p.type in {"tables", "columns", "list"}:
        return ["示例1", "示例2"]
    if p.type == "int":
        return 1
    if p.type == "number":
        return 1.5
    if p.type == "bool":
        return True
    if p.type == "enum":
        return (p.values or ["示例"])[0]
    if p.type == "expr":
        return "TRUE"
    if p.type == "sql":
        return "SELECT 1"
    return "示例"


def render_template(spec: Formula, params: dict[str, Any]) -> str:
    """把 {{param}} 替换成按类型转义后的 SQL 片段，最后整体再校验一次只能是单条 SELECT。"""
    try:
        return assert_single_select(_substitute(spec, params))
    except UnsafeSQLError as exc:
        raise FormulaError(f"公式「{spec.name}」渲染后的 SQL 不合法：{exc}") from exc


def _substitute(spec: Formula, params: dict[str, Any]) -> str:
    """只做占位符替换（还没做整体安全检查）。"""
    if not spec.sql:
        raise FormulaError(f"公式「{spec.name}」没有 SQL 模板")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in spec.params:
            raise FormulaError(f"模板参数「{name}」未声明")
        value = params.get(name)
        p = spec.params[name]
        if value is None:
            return "NULL"
        if p.type in {"table", "column"}:
            return quote_ident(value)
        if p.type in {"tables", "columns"}:
            return ", ".join(quote_ident(v) for v in value)
        if p.type == "list":
            return ", ".join(quote_literal(v) for v in value)
        if p.type in {"expr", "sql"}:
            return f"({value})"
        if p.type == "dict":
            raise FormulaError(f"模板参数「{name}」是对象，不能直接放进 SQL")
        return quote_literal(value)

    return _PLACEHOLDER.sub(replace, spec.sql)


def template_inputs(spec: Formula, params: dict[str, Any]) -> list[str]:
    tables: list[str] = []
    for name, p in spec.params.items():
        value = params.get(name)
        if value is None:
            continue
        if p.type == "table":
            tables.append(value)
        elif p.type == "tables":
            tables.extend(value)
    return tables


def ensure_table_exists(ws: Workspace, name: str) -> None:
    try:
        ws.require(name)
    except WorkspaceError as exc:
        raise FormulaError(str(exc)) from exc
