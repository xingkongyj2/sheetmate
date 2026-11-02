"""Recipe 学习器（SheetAgent inferrer 的对应物）：处理说明 + 输入画像 + 人工结果 + diff + 公式目录 → LLM → Recipe。

和 SheetAgent 一样，输入定义（inputs）不完全依赖 LLM，而是根据真实输入表重新生成：
预期列、必需列（步骤实际用到的列）、业务主键、行数范围、表头位置、文件名匹配规则。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from sheetmate.agent.prompts import (
    RECIPE_FIX_STRUCTURE_PROMPT,
    RECIPE_LEARN_PROMPT,
    RECIPE_REFINE_PROMPT,
    RECIPE_REVISE_PROMPT,
    RECIPE_SYSTEM,
)
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.profile import detect_keys, profile_columns
from sheetmate.engine.sqlutil import frame_to_text
from sheetmate.recipe.differ import diff_to_text
from sheetmate.recipe.schema import (
    OutputSheet,
    Recipe,
    RecipeInput,
    RecipeStep,
    StepAssertion,
    ValidationRule,
    file_pattern_from_name,
    step_output_tables,
    step_table_refs,
)
from sheetmate.recipe.verifier import report_to_text

# LLM 常见的参数名写错，自动纠正
_PARAM_SYNONYMS = {"table_name": "table", "input_table": "table", "source_table": "table", "source": "table"}
_DROP_PARAMS = {"result_name", "output", "output_name"}


@dataclass
class InputMeta:
    """一张输入表的来源信息（生成 RecipeInput 用）。"""

    filename: str = ""
    kind: str = "table"
    sheet: str | None = None
    header: dict[str, Any] | None = None
    file_id: str | None = None
    path: Path | None = None  # 原文件在本机的路径：输出时复制它来保持原样式


@dataclass
class LearnContext:
    """学习 / 修订 Recipe 需要的全部上下文。"""

    requirement: str
    library: FormulaLibrary
    input_frames: dict[str, pd.DataFrame]
    input_meta: dict[str, InputMeta] = field(default_factory=dict)
    expected: dict[str, pd.DataFrame] = field(default_factory=dict)
    expected_source: str = ""  # 人工结果的文件名
    diff: dict[str, Any] | None = None
    memory_text: str = ""
    field_mapping: dict[str, list[str]] = field(default_factory=dict)
    customer_id: str | None = None
    conversation_id: str | None = None
    recipe_name: str | None = None
    # learn：照着人工结果学（输出列对齐人工结果）；revise：按用户意见修改（人工结果只作对比参考）
    mode: str = "learn"


class RecipeLearner:
    def __init__(self, llm: Any):
        self.llm = llm

    async def generate(self, ctx: LearnContext) -> Recipe:
        prompt = RECIPE_LEARN_PROMPT.format(
            requirement=ctx.requirement or "（用户没有写说明，请根据差异证据推断）",
            memory=ctx.memory_text or "（无）",
            field_mapping=_mapping_text(ctx.field_mapping),
            inputs=inputs_text(ctx),
            expected=expected_text(ctx),
            diff=diff_to_text(ctx.diff) if ctx.diff else "（没有提供人工结果，没有差异证据，请严格按处理说明生成）",
            catalog=ctx.library.catalog_text(),
            base_recipe="",
        )
        result = await self.llm.complete_json(prompt, system=RECIPE_SYSTEM)
        return build_recipe(result, ctx)

    async def refine(self, ctx: LearnContext, recipe: Recipe, report: dict[str, Any]) -> Recipe:
        prompt = RECIPE_REFINE_PROMPT.format(
            recipe=_recipe_json(recipe),
            report=report_to_text(report),
            inputs=inputs_text(ctx),
            expected=expected_text(ctx),
            diff=diff_to_text(ctx.diff) if ctx.diff else "（无）",
            catalog=ctx.library.catalog_text(),
        )
        result = await self.llm.complete_json(prompt, system=RECIPE_SYSTEM)
        return build_recipe(result, ctx, base=recipe)

    async def revise(self, ctx: LearnContext, recipe: Recipe, instruction: str) -> Recipe:
        prompt = RECIPE_REVISE_PROMPT.format(
            instruction=instruction,
            recipe=_recipe_json(recipe),
            inputs=inputs_text(ctx),
            memory=ctx.memory_text or "（无）",
            catalog=ctx.library.catalog_text(),
        )
        result = await self.llm.complete_json(prompt, system=RECIPE_SYSTEM)
        return build_recipe(result, ctx, base=recipe)

    async def fix_structure(self, ctx: LearnContext, recipe: Recipe, problems: list[str]) -> Recipe:
        prompt = RECIPE_FIX_STRUCTURE_PROMPT.format(
            problems="\n".join(f"- {p}" for p in problems),
            recipe=_recipe_json(recipe),
            inputs=inputs_text(ctx),
            catalog=ctx.library.catalog_text(),
        )
        result = await self.llm.complete_json(prompt, system=RECIPE_SYSTEM)
        return build_recipe(result, ctx, base=recipe)


# ---------------------------------------------------------------------------
# 上下文文本
# ---------------------------------------------------------------------------


def frame_profile_text(name: str, df: pd.DataFrame, label: str = "") -> str:
    columns = profile_columns(df)
    lines = [f"### 表「{name}」{('（' + label + '）') if label else ''}：{len(df)} 行 × {len(df.columns)} 列"]
    for col in columns:
        extra = f"，合计 {col['sum']}" if "sum" in col else ""
        lines.append(f"- {col['name']}（{col['dtype']}，空值 {col['null_rate']:.0%}，{col['unique']} 个不同值{extra}）样本 {col['samples'][:5]}")
    keys = detect_keys(df, columns)
    if keys["candidates"]:
        lines.append(f"候选主键：{keys['candidates']}")
    for dup in keys["duplicated"]:
        lines.append(f"「{dup['column']}」有 {dup['duplicate_values']} 个值重复（最多 {dup['max_repeat']} 次），可能是一单多行")
    lines.append("前 5 行：\n" + frame_to_text(df, limit=5))
    return "\n".join(lines)


def inputs_text(ctx: LearnContext) -> str:
    parts = []
    for alias, df in ctx.input_frames.items():
        meta = ctx.input_meta.get(alias, InputMeta())
        label = f"{meta.filename} · {meta.sheet}" if meta.filename else ""
        parts.append(frame_profile_text(alias, df, label))
    for alias, meta in ctx.input_meta.items():
        if meta.kind == "file" and alias not in ctx.input_frames:
            parts.append(f"### 文件「{alias}」：{meta.filename or alias}（完整文件，可用于 Python input_files）")
    return "\n\n".join(parts) or "（没有输入表或文件）"


def expected_text(ctx: LearnContext) -> str:
    if not ctx.expected:
        return "（未提供人工结果）"
    parts = [f"来自文件：{ctx.expected_source}" if ctx.expected_source else ""]
    for sheet, df in ctx.expected.items():
        parts.append(frame_profile_text(sheet, df, "输出 sheet"))
    return "\n\n".join(p for p in parts if p)


def _mapping_text(mapping: dict[str, list[str]]) -> str:
    if not mapping:
        return "（无）"
    return "\n".join(f"- {std} ← {aliases}" for std, aliases in mapping.items())


def _recipe_json(recipe: Recipe) -> str:
    data = recipe.to_dict()
    for key in ("created_at", "updated_at", "learned_from", "metadata"):
        data.pop(key, None)
    for step in data.get("steps", []):
        step.pop("sql", None)
    return json.dumps(data, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# LLM JSON → Recipe
# ---------------------------------------------------------------------------


def build_recipe(result: dict[str, Any], ctx: LearnContext, base: Recipe | None = None) -> Recipe:
    if not isinstance(result, dict):
        raise ValueError("模型返回的 Recipe 不是 JSON 对象")
    steps = parse_steps(result, ctx.library)
    key_columns = result.get("key_columns") if isinstance(result.get("key_columns"), dict) else {}
    inputs = build_inputs(ctx, steps, key_columns or {}, base)
    outputs = parse_outputs(result, steps, ctx, base)
    rules = []
    for raw in result.get("validation_rules") or []:
        if isinstance(raw, dict) and raw.get("type"):
            try:
                rules.append(ValidationRule(**raw))
            except ValueError:
                continue
    metadata = dict(base.metadata) if base else {}
    alignment = result.get("alignment_report") if isinstance(result.get("alignment_report"), dict) else {}
    metadata.update(
        {
            "confidence_overall": float(alignment.get("confidence_overall") or result.get("confidence_overall") or 0.0),
            "notes": str(result.get("notes") or ""),
            "questions": [q for q in result.get("questions") or [] if isinstance(q, dict) and q.get("question")],
            "alignment_report": alignment,
            "has_expected": bool(ctx.expected),
            "requirement": ctx.requirement,
        }
    )
    learned_from = dict(base.learned_from) if base else {}
    learned_from.update(
        {
            "input_files": [m.filename for m in ctx.input_meta.values() if m.filename],
            "expected_file": ctx.expected_source,
            "requirement": ctx.requirement,
            "conversation_id": ctx.conversation_id,
        }
    )
    name = ctx.recipe_name or str(result.get("recipe_name") or (base.name if base else "未命名流程"))
    recipe = Recipe(
        name=name[:80],
        description=str(result.get("recipe_description") or (base.description if base else "")),
        customer_id=ctx.customer_id if base is None else base.customer_id,
        scope=base.scope if base else "customer",
        inputs=inputs,
        field_mapping=dict(ctx.field_mapping),
        steps=steps,
        output_spec=outputs,
        validation_rules=rules,
        learned_from=learned_from,
        metadata=metadata,
    )
    if base is not None:
        recipe.id = base.id
        recipe.version = base.version
        recipe.created_at = base.created_at
    return recipe


def parse_steps(result: dict[str, Any], library: FormulaLibrary) -> list[RecipeStep]:
    steps: list[RecipeStep] = []
    used_outputs: set[str] = set()
    for i, raw in enumerate(result.get("steps") or [], start=1):
        if not isinstance(raw, dict):
            continue
        formula = str(raw.get("formula") or raw.get("action") or "").strip()
        tool_kind = str(raw.get("kind") or "")
        if tool_kind == "python":
            formula = "python_script"
        elif tool_kind == "sql":
            formula = "custom_sql"
        params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
        params = _fix_params(params, formula, library)
        output = str(raw.get("output") or params.pop("result_name", None) or f"步骤{i}结果").strip()
        while output in used_outputs:
            output = f"{output}_{i}"
        used_outputs.add(output)
        assertions = []
        for a in raw.get("assertions") or []:
            try:
                assertions.append(StepAssertion(**a))
            except (TypeError, ValueError):
                continue
        kind = library.resolve(formula).kind if library.has(formula) else "builtin"
        try:
            confidence = float(raw.get("confidence", 0.8))
        except (TypeError, ValueError):
            confidence = 0.8
        steps.append(
            RecipeStep(
                id=i,
                name=str(raw.get("name") or f"第 {i} 步")[:40],
                action=str(raw.get("action") or "custom"),
                formula=formula,
                kind="python" if formula == "python_script" else "sql" if formula == "custom_sql" else "formula",
                formula_kind=kind,
                params=params,
                output=output,
                description=str(raw.get("description") or ""),
                explanation=str(raw.get("explanation") or ""),
                confidence=max(0.0, min(1.0, confidence)),
                assertions=assertions,
            )
        )
    if not steps:
        raise ValueError("模型没有生成任何步骤")
    return steps


def _fix_params(params: dict[str, Any], formula: str, library: FormulaLibrary) -> dict[str, Any]:
    fixed = {k: v for k, v in params.items() if k not in _DROP_PARAMS}
    if not library.has(formula):
        return fixed
    declared = library.resolve(formula).spec.params
    for wrong, right in _PARAM_SYNONYMS.items():
        if wrong in fixed and wrong not in declared and right in declared and right not in fixed:
            fixed[right] = fixed.pop(wrong)
    return fixed


def parse_outputs(result: dict[str, Any], steps: list[RecipeStep], ctx: LearnContext, base: Recipe | None = None) -> list[OutputSheet]:
    outputs: list[OutputSheet] = []
    table_outputs = [name for step in steps for name in step_output_tables(step)]
    known = set(table_outputs)
    for raw in result.get("output_sheets") or result.get("output_spec") or []:
        if not isinstance(raw, dict):
            continue
        source = str(raw.get("source") or "")
        match = re.fullmatch(r"step_(\d+)(?:_output)?", source)
        if match:  # 兼容 SheetAgent 的 step_N_output 写法
            idx = int(match.group(1))
            source = next((s.output for s in steps if s.id == idx), source)
        if source not in known:
            continue
        columns = raw.get("columns")
        outputs.append(OutputSheet(
            name=str(raw.get("name") or source), source=source, description=str(raw.get("description") or ""),
            split_by=raw.get("split_by"),
            columns=[str(c) for c in columns] if isinstance(columns, list) and columns else None,
        ))
    if not outputs and known:
        last = table_outputs[-1]
        name = next(iter(ctx.expected)) if len(ctx.expected) == 1 else "结果"
        outputs.append(OutputSheet(name=name, source=last))
    previous = {o.name: o for o in (base.output_spec if base else [])}
    for out in outputs:
        if ctx.mode == "revise":
            # 修订：模型给了列就用模型的；没给就沿用修订前的列顺序
            if out.columns is None and out.name in previous:
                out.columns = previous[out.name].columns
        elif out.name in ctx.expected:
            # 学习：输出列按人工结果的列和顺序对齐（SheetAgent 的自动重排）
            out.columns = [str(c) for c in ctx.expected[out.name].columns]
    return outputs


def build_inputs(ctx: LearnContext, steps: list[RecipeStep], key_columns: dict[str, list[str]], base: Recipe | None) -> list[RecipeInput]:
    """根据真实输入表生成 RecipeInput；必需列 = 步骤里实际引用到的列。"""
    previous = {i.alias: i for i in (base.inputs if base else [])}
    used = _referenced_columns(steps, ctx)
    inputs: list[RecipeInput] = []
    for alias, df in ctx.input_frames.items():
        columns = [str(c) for c in df.columns]
        meta = ctx.input_meta.get(alias, InputMeta())
        old = previous.get(alias)
        if meta.kind == "file" or (old is not None and old.kind == "file"):
            continue
        required = [c for c in columns if c in used.get(alias, set())]
        if not required:  # SheetAgent 的兜底：前 10 个没有空值的列
            required = [c for c in columns if df[c].notna().all()][:10]
        keys = [c for c in key_columns.get(alias, []) if c in columns]
        rows = len(df)
        inputs.append(
            RecipeInput(
                alias=alias,
                description=(old.description if old and old.description else f"来自「{meta.filename or alias}」" + (f"的「{meta.sheet}」" if meta.sheet else "")),
                file_pattern=(old.file_pattern if old and old.file_pattern else file_pattern_from_name(meta.filename) if meta.filename else ""),
                sheet=meta.sheet,
                source_file=meta.filename,
                expected_columns=columns,
                required_columns=required,
                key_columns=keys or (old.key_columns if old else []),
                column_matching="fuzzy",
                expected_row_range=[max(1, int(rows * 0.3)), max(10, int(rows * 3) + 10)],
                header=meta.header,
            )
        )
    file_refs = {
        str(ref)
        for step in steps if step.kind == "python"
        for ref in (step.params.get("input_files") if isinstance(step.params.get("input_files"), dict) else {}).values()
    }
    aliases = {item.alias for item in inputs}
    for alias in sorted(file_refs - aliases):
        old = previous.get(alias)
        meta = ctx.input_meta.get(alias)
        if old is not None and old.kind == "file":
            inputs.append(old.model_copy(deep=True))
        elif meta is not None and meta.kind == "file":
            inputs.append(RecipeInput(
                alias=alias, kind="file", description=f"来自「{meta.filename or alias}」的完整文件",
                file_pattern=file_pattern_from_name(meta.filename) if meta.filename else "",
                source_file=meta.filename,
            ))
    return inputs


def _referenced_columns(steps: list[RecipeStep], ctx: LearnContext) -> dict[str, set[str]]:
    """统计每张输入表被用到的列：列参数 + 表达式里双引号包住的名字。

    按「血缘」追溯：后面的步骤用在中间表上的列，如果来自某张输入表（同名列），也算这张输入表的必需列。
    例如先筛选再按「店铺名称」汇总，「店铺名称」也是输入表必需的。
    """
    columns = {alias: {str(c) for c in df.columns} for alias, df in ctx.input_frames.items()}
    used: dict[str, set[str]] = {alias: set() for alias in ctx.input_frames}
    lineage: dict[str, set[str]] = {alias: {alias} for alias in ctx.input_frames}  # 表 → 它来自哪些输入表
    for step in steps:
        refs = step_table_refs(step, ctx.library)
        sources = set().union(*(lineage.get(r, set()) for r in refs)) if refs else set()
        for output in step_output_tables(step):
            lineage[output] = sources
        if step.kind == "python":
            # 代码可能动态访问列，不能只取前十列或靠字符串猜测；保守保留声明输入的列契约。
            for alias in sources:
                used[alias] |= columns[alias]
            continue
        if not ctx.library.has(step.formula):
            continue
        spec = ctx.library.resolve(step.formula).spec
        names: set[str] = set()
        blob = []
        for name, p in spec.params.items():
            value = step.params.get(name)
            if value is None:
                continue
            if p.type in {"column", "columns"}:
                names |= {str(v) for v in (value if isinstance(value, list) else [value])}
            elif p.type in {"expr", "sql", "dict", "any", "str"}:
                blob.append(json.dumps(value, ensure_ascii=False))
        names |= set(re.findall(r'\\?"([^"\\]+)\\?"', " ".join(blob)))
        for alias in sources:
            used[alias] |= names & columns[alias]
    return used
