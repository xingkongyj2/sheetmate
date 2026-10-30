"""Recipe 数据结构。

总体结构沿用 SheetAgent 的 Recipe（inputs / steps / output_spec / validation_rules / learned_from / metadata），
并加入 SheetMate 的逻辑：
- 步骤不再保存 pandas / M 代码，而是「公式 id + 参数取值」，执行时由引擎编译成 DuckDB SQL
- formula_kind 标明这一步用的是内置公式还是客户独有公式
- 步骤可以挂断言（row_count_range / match_rate_min / no_duplicate_keys …），不通过就触发修复
- field_mapping 记录这个流程依赖的字段别名（标准字段 → 别名）
- 带 scope / customer_id / status / version，客户专属覆盖公共，版本只升不改
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.sqlutil import sql_table_refs


def _now() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def new_recipe_id() -> str:
    return f"rcp_{uuid.uuid4().hex[:10]}"


class RecipeInput(BaseModel):
    """运行时怎样识别输入（SheetAgent RecipeInput + 文件匹配规则 + 表头位置）。"""

    model_config = {"extra": "ignore"}

    alias: str  # 表名：步骤参数里引用它
    description: str = ""
    file_pattern: str = ""  # 文件名规则，例如「运费明细*」，重复执行时自动匹配文件
    sheet: str | None = None
    source_file: str = ""  # 学习时用的文件名
    expected_columns: list[str] = Field(default_factory=list)
    required_columns: list[str] = Field(default_factory=list)
    key_columns: list[str] = Field(default_factory=list)  # 业务主键（运单号、客户编码…）
    column_matching: Literal["exact", "fuzzy"] = "fuzzy"
    expected_row_range: list[int] | None = None
    header: dict[str, Any] | None = None  # {"header_row": 2, "header_rows": 1}


class StepAssertion(BaseModel):
    """步骤断言：硬错误直接抛，软错误（匹配率低、行数异常、重复键）靠断言发现。"""

    model_config = {"extra": "ignore"}

    type: Literal[
        "not_empty", "row_count_range", "row_count_ratio", "match_rate_min", "no_duplicate_keys", "no_nulls", "columns_exist",
        "no_dropped_values",  # 长转宽时不允许出现没拆成列的新取值（金额会丢）
    ]
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["error", "warning"] = "error"
    description: str = ""


class RecipeStep(BaseModel):
    """Recipe 的一步：调用一个公式，结果存成 output 表。"""

    model_config = {"extra": "ignore"}

    id: int
    name: str
    action: str = "custom"  # filter / merge / pivot / aggregate / calculate / clean / output / custom
    formula: str
    formula_kind: Literal["builtin", "customer"] = "builtin"
    params: dict[str, Any] = Field(default_factory=dict)
    output: str
    description: str = ""  # 人话说明（流程图上展示）
    explanation: str = ""  # 推断依据：diff 证据 / 处理说明（SheetAgent）
    confidence: float = 0.8
    sql: str = ""  # 最近一次执行时编译出的 SQL，仅供审计
    assertions: list[StepAssertion] = Field(default_factory=list)
    enabled: bool = True

    @field_validator("output")
    @classmethod
    def _clean_output(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("步骤必须有 output（结果表名）")
        return text[:60]


class OutputSheet(BaseModel):
    """哪个结果表写成输出文件里的哪个 sheet。"""

    model_config = {"extra": "ignore"}

    name: str
    source: str
    description: str = ""
    columns: list[str] | None = None  # 可选：只输出这些列（按顺序）
    split_by: str | None = None  # 可选：按这一列的取值拆成多个 sheet


class ValidationRule(BaseModel):
    """运行后的校验规则（SheetAgent ValidationRule）。"""

    model_config = {"extra": "ignore"}

    id: str = Field(default_factory=lambda: f"v_{uuid.uuid4().hex[:8]}")
    type: str
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["error", "warning", "info"] = "warning"
    requires_history: bool = False


class Recipe(BaseModel):
    model_config = {"extra": "ignore"}

    id: str = Field(default_factory=new_recipe_id)
    version: int = 1
    name: str
    description: str = ""
    scope: Literal["customer", "global"] = "customer"
    customer_id: str | None = None
    status: Literal["draft", "active", "deprecated"] = "draft"
    created_by: Literal["ai", "human"] = "ai"
    learned_from: dict[str, Any] = Field(default_factory=dict)
    inputs: list[RecipeInput] = Field(default_factory=list)
    field_mapping: dict[str, list[str]] = Field(default_factory=dict)
    steps: list[RecipeStep] = Field(default_factory=list)
    output_spec: list[OutputSheet] = Field(default_factory=list)
    validation_rules: list[ValidationRule] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    # ------------------------------------------------------------------

    def ordered_steps(self) -> list[RecipeStep]:
        return sorted((s for s in self.steps if s.enabled), key=lambda s: s.id)

    def step(self, step_id: int) -> RecipeStep:
        for s in self.steps:
            if s.id == step_id:
                return s
        raise KeyError(f"Recipe 没有第 {step_id} 步")

    def input_aliases(self) -> list[str]:
        return [i.alias for i in self.inputs]

    def final_outputs(self) -> list[str]:
        if self.output_spec:
            return [o.source for o in self.output_spec]
        steps = self.ordered_steps()
        return [steps[-1].output] if steps else []

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def flow(self) -> dict[str, Any]:
        """流程图数据：输入 → 步骤 → 输出（前端 RecipeFlow 组件用）。"""
        return {
            "inputs": [{"alias": i.alias, "description": i.description, "file_pattern": i.file_pattern} for i in self.inputs],
            "steps": [
                {
                    "id": s.id,
                    "name": s.name,
                    "action": s.action,
                    "formula": s.formula,
                    "formula_kind": s.formula_kind,
                    "description": s.description,
                    "output": s.output,
                    "inputs": step_table_refs(s),
                }
                for s in self.ordered_steps()
            ],
            "outputs": [{"name": o.name, "source": o.source} for o in self.output_spec],
        }


# ---------------------------------------------------------------------------
# 静态检查（执行前）：公式存在、表引用顺序正确、输出不重名
# ---------------------------------------------------------------------------

_TABLE_PARAM_TYPES = {"table", "tables"}


def step_table_refs(step: RecipeStep, library: FormulaLibrary | None = None) -> list[str]:
    """这一步读了哪些表。"""
    lib = library or FormulaLibrary()
    refs: list[str] = []
    try:
        spec = lib.resolve(step.formula).spec
    except ValueError:
        spec = None
    if spec is not None:
        for name, p in spec.params.items():
            value = step.params.get(name)
            if value is None or p.type not in _TABLE_PARAM_TYPES:
                continue
            for v in value if isinstance(value, list) else [value]:
                if str(v) not in refs:
                    refs.append(str(v))
    if step.formula == "custom_sql" and not refs:
        refs = sql_table_refs(str(step.params.get("sql", "")))
    return refs


def check_recipe(recipe: Recipe, library: FormulaLibrary) -> list[str]:
    """返回问题列表（空列表表示通过）。只检查结构，列是否存在要到执行时由引擎检查。"""
    problems: list[str] = []
    known = set(recipe.input_aliases())
    if len(known) != len(recipe.inputs):
        problems.append("输入别名有重复")
    if not recipe.steps:
        problems.append("Recipe 至少需要一个步骤")
    seen_ids: set[int] = set()
    for step in recipe.ordered_steps():
        label = f"第 {step.id} 步「{step.name}」"
        if step.id in seen_ids:
            problems.append(f"{label}：步骤 id 重复")
        seen_ids.add(step.id)
        if not library.has(step.formula):
            problems.append(f"{label}：公式「{step.formula}」不存在")
            continue
        spec = library.resolve(step.formula).spec
        unknown = set(step.params) - set(spec.params)
        if unknown:
            problems.append(f"{label}：公式「{step.formula}」没有参数 {sorted(unknown)}")
        missing = [n for n, p in spec.params.items() if p.required and step.params.get(n) in (None, "", [], {})]
        if missing:
            problems.append(f"{label}：缺少必填参数 {missing}")
        for ref in step_table_refs(step, library):
            if ref not in known:
                problems.append(f"{label}：引用的表「{ref}」不存在（只能用输入表或前面步骤的输出）")
        if step.output in recipe.input_aliases():
            problems.append(f"{label}：输出名「{step.output}」和输入表重名")
        known.add(step.output)
    for out in recipe.output_spec:
        if out.source not in known:
            problems.append(f"输出 sheet「{out.name}」的来源「{out.source}」不存在")
    return problems


def file_pattern_from_name(filename: str) -> str:
    """从文件名推出匹配规则：去掉扩展名，把日期/月份/数字部分换成 *。例：运费明细_2026-09.xlsx → 运费明细*"""
    stem = re.sub(r"\.(xlsx|xlsm|xls|csv)$", "", filename, flags=re.IGNORECASE)
    stem = re.sub(r"[\s_\-（(]*\d[\d\s_\-.年月日）)]*$", "", stem)
    stem = re.sub(r"\d+", "*", stem)
    stem = stem.strip(" _-")
    return f"{stem}*" if stem else "*"
