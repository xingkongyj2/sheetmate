"""把分析过程沉淀成 Recipe（不需要 LLM）。

分析模式里，每个由公式生成的表都记下了「公式 + 参数」（TableInfo.step）。
从用户选中的结果表往回追溯依赖，直到上传的原始表，按依赖顺序排列就是一个可以重复执行的流程。
这对应 TabClaw 的「工作流记录 → 技能沉淀」，但沉淀出来的是可确定执行的 Recipe，而不是给 LLM 的提示。
"""

from __future__ import annotations

from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workspace import Workspace
from sheetmate.recipe.learner import InputMeta, LearnContext, build_inputs
from sheetmate.recipe.schema import OutputSheet, Recipe, RecipeStep, step_table_refs


def compile_from_workspace(
    ws: Workspace,
    targets: list[str],
    library: FormulaLibrary,
    *,
    name: str,
    description: str = "",
    customer_id: str | None = None,
    conversation_id: str | None = None,
    field_mapping: dict[str, list[str]] | None = None,
) -> Recipe:
    if not targets:
        raise ValueError("请指定要输出的结果表")
    ordered: list[tuple[str, dict]] = []
    inputs: list[str] = []
    visiting: set[str] = set()
    done: set[str] = set()

    def visit(table: str) -> None:
        if table in done:
            return
        if table in visiting:
            raise ValueError(f"表「{table}」存在循环依赖")
        visiting.add(table)
        info = ws.require(table)
        if info.source == "uploaded":
            inputs.append(table)
        elif not info.step:
            raise ValueError(f"表「{table}」不是由公式生成的（可能来自临时查询），无法沉淀成流程")
        else:
            probe = RecipeStep(id=0, name=table, formula=info.step["formula"], params=info.step.get("params", {}), output=table)
            for ref in step_table_refs(probe, library):
                visit(ref)
            ordered.append((table, info.step))
        visiting.discard(table)
        done.add(table)

    for target in targets:
        visit(target)

    steps = []
    for i, (table, step) in enumerate(ordered, start=1):
        info = ws.require(table)
        resolved = library.resolve(step["formula"])
        steps.append(
            RecipeStep(
                id=i,
                name=(info.label if info.label and info.label != table else table)[:40],
                action=resolved.spec.category,
                formula=step["formula"],
                formula_kind=resolved.kind,
                params=step.get("params", {}),
                output=table,
                description=info.description or resolved.describe(step.get("params", {})),
                explanation="由分析过程沉淀",
                confidence=0.9,
            )
        )
    frames = {alias: ws.df(alias) for alias in inputs}
    meta = {}
    for alias in inputs:
        info = ws.require(alias)
        meta[alias] = InputMeta(filename=info.filename or info.label, sheet=info.sheet, header=info.header, file_id=info.file_id)
    ctx = LearnContext(requirement=description, library=library, input_frames=frames, input_meta=meta,
                       field_mapping=field_mapping or {}, customer_id=customer_id, conversation_id=conversation_id)
    recipe = Recipe(
        name=name[:80],
        description=description,
        customer_id=customer_id,
        created_by="ai",
        inputs=build_inputs(ctx, steps, {}, None),
        field_mapping=dict(field_mapping or {}),
        steps=steps,
        output_spec=[OutputSheet(name=(ws.require(t).label or t)[:31], source=t) for t in targets],
        learned_from={"conversation_id": conversation_id, "input_files": [m.filename for m in meta.values()], "from": "analysis"},
        metadata={"requirement": description, "confidence_overall": 0.9},
    )
    return recipe
