"""Recipe 学习器（SheetAgent inferrer 的对应物）：处理说明 + 输入画像 + 人工结果 + diff + 公式目录 → LLM → Recipe。

和 SheetAgent 一样，输入定义（inputs）不完全依赖 LLM，而是根据真实输入表重新生成：
预期列、必需列（步骤实际用到的列）、业务主键、行数范围、表头位置、文件名匹配规则。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from sheetmate.agent.llm.prompts import (
    RECIPE_FIX_STRUCTURE_PROMPT,
    RECIPE_LEARN_PROMPT,
    RECIPE_REFINE_PROMPT,
    RECIPE_REVISE_PROMPT,
    RECIPE_SYSTEM,
)
from sheetmate.agent.llm.structured import StructuredOutputError, complete_structured
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workbooks.profile import detect_keys, profile_columns
from sheetmate.engine.scripts.store import resolve_script_code
from sheetmate.engine.tables.sql import frame_to_text
from sheetmate.recipe.learning.differ import diff_to_text
from sheetmate.recipe.learning.response_contract import RECIPE_RESPONSE_SCHEMA, validate_recipe_response
from sheetmate.recipe.definition.schema import (
    OutputSheet,
    Recipe,
    RecipeInput,
    RecipeStep,
    StepAssertion,
    ValidationRule,
    file_pattern_from_name,
    shorten_recipe_name,
    step_output_tables,
    step_table_refs,
)
from sheetmate.recipe.validation.comparison import report_to_text

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
    revision: dict[str, Any] = field(default_factory=dict)  # 已校验的本次输入/输出/步骤修改范围
    protected_steps: dict[int, RecipeStep] = field(default_factory=dict, repr=False)  # 本次修订的瞬态恢复基线，不写入流程元数据

    @property
    def output_revision(self) -> bool:
        return "output_sheets" in self.revision or (self.revision.get("target") or {}).get("kind") == "output"


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
        result = await complete_structured(self.llm, prompt, system=RECIPE_SYSTEM,
                                           schema=RECIPE_RESPONSE_SCHEMA, schema_name="recipe",
                                           validator=validate_recipe_response)
        return build_recipe(result, ctx)

    async def refine(self, ctx: LearnContext, recipe: Recipe, report: dict[str, Any]) -> Recipe:
        prompt = RECIPE_REFINE_PROMPT.format(
            requirement=ctx.requirement or "（沿用已有处理要求）",
            memory=ctx.memory_text or "（无）",
            recipe=_recipe_json(recipe),
            report=report_to_text(report),
            inputs=inputs_text(ctx),
            expected=expected_text(ctx),
            diff=diff_to_text(ctx.diff) if ctx.diff else "（无）",
            catalog=ctx.library.catalog_text(),
        )
        result = await complete_structured(self.llm, prompt, system=RECIPE_SYSTEM,
                                           schema=RECIPE_RESPONSE_SCHEMA, schema_name="recipe",
                                           validator=lambda result: validate_recipe_response(_revision_result(result, ctx)))
        return build_recipe(result, ctx, base=recipe)

    async def revise(self, ctx: LearnContext, recipe: Recipe, instruction: str) -> Recipe:
        prompt = RECIPE_REVISE_PROMPT.format(
            instruction=instruction,
            recipe=_recipe_json(recipe),
            inputs=inputs_text(ctx),
            revision=json.dumps(ctx.revision, ensure_ascii=False),
            expected=expected_text(ctx),
            memory=ctx.memory_text or "（无）",
            catalog=ctx.library.catalog_text(),
        )
        result = await complete_structured(self.llm, prompt, system=RECIPE_SYSTEM,
                                           schema=RECIPE_RESPONSE_SCHEMA, schema_name="recipe",
                                           validator=lambda result: validate_recipe_response(_revision_result(result, ctx)))
        return build_recipe(result, ctx, base=recipe)

    async def fix_structure(self, ctx: LearnContext, recipe: Recipe, problems: list[str]) -> Recipe:
        prompt = RECIPE_FIX_STRUCTURE_PROMPT.format(
            requirement=ctx.requirement or "（沿用已有处理要求）",
            memory=ctx.memory_text or "（无）",
            problems="\n".join(f"- {p}" for p in problems),
            recipe=_recipe_json(recipe),
            inputs=inputs_text(ctx),
            catalog=ctx.library.catalog_text(),
        )
        result = await complete_structured(self.llm, prompt, system=RECIPE_SYSTEM,
                                           schema=RECIPE_RESPONSE_SCHEMA, schema_name="recipe",
                                           validator=lambda result: validate_recipe_response(_revision_result(result, ctx)))
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
        text = frame_profile_text(alias, df, label)
        footers = (meta.header or {}).get("footer_rows") or []
        if footers:  # 页脚合计行不在表数据里；不说清楚，模型会自己再追加一行合计，导致合计翻倍
            from sheetmate.engine.tables.memory import footer_notice

            text += "\n说明：" + footer_notice([{"excel_row": int(row) + 1, "label": "合计"} for row in footers])
        parts.append(text)
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
        if step.get("kind") == "python":
            # 完整代码只进入本次调整的模型上下文，保存的流程和前端节点始终使用文件引用。
            step["params"]["code"] = resolve_script_code(step["params"])
    return json.dumps(data, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# LLM JSON → Recipe
# ---------------------------------------------------------------------------


def _revision_result(result: dict[str, Any], ctx: LearnContext) -> dict[str, Any]:
    if "output_sheets" in ctx.revision:
        # This complete declaration came from the tool contract, not the
        # learner's guesses. Adapt steps to it rather than restoring old output.
        result = {**result, "output_sheets": ctx.revision["output_sheets"]}
        result.pop("output_spec", None)
    return result


def build_recipe(result: dict[str, Any], ctx: LearnContext, base: Recipe | None = None) -> Recipe:
    result = _revision_result(result, ctx)
    validate_recipe_response(result)
    steps = parse_steps(result, ctx.library)
    key_columns = result.get("key_columns") if isinstance(result.get("key_columns"), dict) else {}
    inputs = build_inputs(ctx, steps, key_columns or {}, base)
    outputs = parse_outputs(result, steps, ctx, base)
    rules = [ValidationRule(**raw) for raw in result.get("validation_rules", [])]
    metadata = dict(base.metadata) if base else {}
    if base is not None:
        metadata["revision_base_version"] = ctx.revision.get("base_version", base.version)
        metadata["revision"] = ctx.revision
        if "saved_version" in ctx.revision:
            metadata["revision_saved_version"] = ctx.revision["saved_version"]
    if ctx.output_revision:
        metadata["explicit_outputs"] = True
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
        name=shorten_recipe_name(name),
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
        # Unsaved revisions also need a distinct version so an old flow card
        # cannot accidentally edit the newer in-conversation definition.
        recipe.version = ctx.revision["base_version"] + 1 if "base_version" in ctx.revision else base.version
        recipe.created_at = base.created_at
    return recipe


def parse_steps(result: dict[str, Any], library: FormulaLibrary) -> list[RecipeStep]:
    steps: list[RecipeStep] = []
    used_outputs: set[str] = set()
    for i, raw in enumerate(result.get("steps") or [], start=1):
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
        assertions = [StepAssertion(**a) for a in raw.get("assertions", [])]
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
                enabled=raw.get("enabled", True),
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


def _dynamic_schema_outputs(steps: list[RecipeStep], library: FormulaLibrary) -> set[str]:
    """只沿已知保留输入其他字段的内置算子传播动态分类，显式选列/汇总等终止传播。"""
    preserves_table_columns = {
        "drop_columns", "rename_columns", "filter_rows", "drop_duplicates", "sort_rows",
        "fill_null", "trim_text", "cast_columns", "compute_column", "map_values",
        "split_column", "concat_columns", "add_serial", "lookup", "top_n", "append_total", "append_rows",
    }
    dynamic: set[str] = set()
    for step in sorted(steps, key=lambda item: item.id):
        if not step.enabled or not library.has(step.formula) or library.resolve(step.formula).builtin is None:
            # 客户公式、SQL 和脚本的输出结构不能仅根据依赖推断。
            continue
        if step.formula == "pivot_wide" and not step.params.get("values"):
            dynamic.add(step.output)
        elif step.formula in preserves_table_columns:
            # lookup 只保留左表全部列，右表仅带出显式选择的列。
            source = step.params.get("table")
            if isinstance(source, str) and source in dynamic:
                dynamic.add(step.output)
        elif step.formula == "join_tables":
            if any(isinstance(step.params.get(key), str) and step.params[key] in dynamic for key in ("left", "right")):
                dynamic.add(step.output)
        elif step.formula == "concat_tables":
            tables = step.params.get("tables")
            if isinstance(tables, list) and any(isinstance(table, str) and table in dynamic for table in tables):
                dynamic.add(step.output)
    return dynamic


def parse_outputs(result: dict[str, Any], steps: list[RecipeStep], ctx: LearnContext, base: Recipe | None = None) -> list[OutputSheet]:
    outputs: list[OutputSheet] = []
    table_outputs = [name for step in steps for name in step_output_tables(step)]
    known = set(table_outputs)
    field = "output_sheets" if "output_sheets" in result else "output_spec"
    for index, raw in enumerate(result.get(field) or []):
        source = str(raw.get("source") or "")
        match = re.fullmatch(r"step_(\d+)(?:_output)?", source)
        if match:  # 兼容 SheetAgent 的 step_N_output 写法
            idx = int(match.group(1))
            source = next((s.output for s in steps if s.id == idx), source)
        if source not in known:
            raise StructuredOutputError([
                {"path": f"/{field}/{index}/source", "message": "必须引用步骤实际声明的输出表"},
            ])
        columns = raw.get("columns")
        outputs.append(OutputSheet(
            name=str(raw.get("name") or source), source=source, description=str(raw.get("description") or ""),
            split_by=raw.get("split_by"),
            columns=[str(c) for c in columns] if isinstance(columns, list) and columns else None,
        ))
    if not outputs and known and not ctx.output_revision:
        last = table_outputs[-1]
        name = next(iter(ctx.expected)) if len(ctx.expected) == 1 else "结果"
        outputs.append(OutputSheet(name=name, source=last))
    previous = {o.name: o for o in (base.output_spec if base else [])}
    dynamic_outputs = _dynamic_schema_outputs(steps, ctx.library)
    for out in outputs:
        if ctx.mode == "revise" and not ctx.output_revision:
            # 修订：模型给了列就用模型的；没给就沿用修订前的列顺序
            if out.columns is None and out.name in previous:
                out.columns = previous[out.name].columns
        elif out.columns is None and out.name in ctx.expected and out.source not in dynamic_outputs:
            # 动态分类不能被样例中的列清单裁掉；明确声明的输出列仍按用户要求保留。
            # 其他学习结果沿用人工结果的列顺序。
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
        if meta.kind == "file":
            continue
        same_source = old is not None and old.kind == "table" and old.source_file == meta.filename
        required = [c for c in columns if c in used.get(alias, set())]
        if not required:  # SheetAgent 的兜底：前 10 个没有空值的列
            required = [c for c in columns if df[c].notna().all()][:10]
        keys = [c for c in key_columns.get(alias, []) if c in columns]
        rows = len(df)
        inputs.append(
            RecipeInput(
                alias=alias,
                description=(old.description if same_source and old.description else f"来自「{meta.filename or alias}」" + (f"的「{meta.sheet}」" if meta.sheet else "")),
                file_pattern=(old.file_pattern if same_source and old.file_pattern else file_pattern_from_name(meta.filename) if meta.filename else ""),
                sheet=meta.sheet,
                source_file=meta.filename,
                expected_columns=columns,
                required_columns=required,
                key_columns=keys or ([column for column in old.key_columns if column in columns] if same_source else []),
                column_matching="fuzzy",
                expected_row_range=[max(1, int(rows * 0.3)), max(10, int(rows * 3) + 10)],
                header=meta.header,
            )
        )
    # Current source metadata is authoritative. Do not resurrect removed file
    # inputs from old steps; structure validation will require adapting them.
    for alias, meta in ctx.input_meta.items():
        if meta.kind != "file":
            continue
        old = previous.get(alias)
        if old is not None and old.kind == "file" and old.source_file == meta.filename:
            inputs.append(old.model_copy(deep=True))
        else:
            inputs.append(RecipeInput(
                alias=alias, kind="file", description=f"来自「{meta.filename or alias}」的完整文件",
                file_pattern=file_pattern_from_name(meta.filename) if meta.filename else "",
                source_file=meta.filename,
            ))
    return inputs


def step_execution_signature(step: RecipeStep) -> str:
    """Compare behavior without treating script storage or display text as edits."""
    params = dict(step.params)
    if step.kind == "python":
        try:
            code = resolve_script_code(params)
        except (OSError, ValueError):
            # Invalid scripts still reach the ordinary structure diagnostics;
            # their references must not compare equal to a valid baseline.
            code = None
        for key in ("code", "script_id", "script_title", "script_filename", "title"):
            params.pop(key, None)
        params["code"] = code
    definition = {
        "id": step.id, "kind": step.kind, "formula": step.formula,
        "params": params, "output": step.output, "enabled": step.enabled,
        "assertions": [assertion.model_dump(exclude={"description"}, mode="json") for assertion in step.assertions],
    }
    return hashlib.sha256(json.dumps(definition, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def output_delivery_identities(recipe: Recipe) -> list[tuple[str, str]]:
    """Delivery names identify additions; changing a table's source does not."""
    identities = [("sheet", output.name) for output in recipe.output_spec]
    # The automatically named workbook remains one delivery when the recipe is
    # renamed. Repeated script filenames still represent distinct final files.
    identities.extend(("file", output["name"]) if output.get("ref") else ("workbook", "")
                      for output in recipe.flow().get("output_files", []))
    return identities


def revision_problems(ctx: LearnContext, recipe: Recipe) -> list[str]:
    """Enforce the trusted structural scope throughout revision and refinement."""
    problems = []
    steps = {step.id: step for step in recipe.steps}
    for protected in ctx.revision.get("protected_steps", []):
        step_id = protected["id"]
        current = steps.get(step_id)
        if current is None or step_execution_signature(current) != protected["signature"]:
            problem = f"第 {step_id} 步不在本次选中步骤及其下游依赖范围内，必须恢复原执行定义，不能删除、停用或修改公式、参数、输出及断言。"
            original = ctx.protected_steps.get(step_id)
            if original is not None:
                restore = original.model_dump(mode="json", exclude={"sql"})
                if original.kind == "python":
                    restore["params"]["code"] = resolve_script_code(original.params)
                problem += "原定义：" + json.dumps(restore, ensure_ascii=False)
            problems.append(problem)
    missing = set(ctx.revision.get("protected_output_names", [])) - {output.name for output in recipe.output_spec}
    if missing:
        problems.append(f"本次未要求删除这些输出，必须保留并适配来源：{'、'.join(sorted(missing))}")
    files = recipe.flow().get("output_files", [])
    missing_files = set(ctx.revision.get("protected_output_files", [])) - {output["name"] for output in files}
    if missing_files:
        problems.append(f"本次未要求删除这些输出文件，必须保留实际生成代码与声明：{'、'.join(sorted(missing_files))}")
    target = ctx.revision.get("target") or {}
    if (target.get("kind") == "output" and ctx.revision.get("operation") == "add"
            and "base_output_identities" in ctx.revision):
        baseline = Counter(tuple(item) for item in ctx.revision["base_output_identities"])
        if not Counter(output_delivery_identities(recipe)) - baseline:
            problems.append("本次要求新增输出，必须在保留原有输出的基础上新增至少一个最终工作表或文件；原样返回或仅修改来源不算新增。")
    if target.get("kind") == "output" and ctx.revision.get("operation") == "remove":
        file_target = "sources" in target
        selected_ref = ctx.revision.get("target_file_ref")
        for output in files if file_target else recipe.flow().get("outputs", []):
            if file_target and selected_ref:
                selected = output.get("ref") == selected_ref
            elif file_target and selected_ref == "":
                # A workbook remains the same delivery when its contributing
                # tables change. Script files with that name are independent.
                selected = not output.get("ref") and output.get("name") == target.get("name")
            else:
                selected = output.get("name") == target.get("name") and (
                    not target.get("source") or output.get("source") == target["source"])
            if selected:
                problems.append("选中的输出文件仍被声明为最终产物，请同步移除对应工作表/脚本 output_files 并适配后续引用。")
                break
    return problems


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
