"""输出计划：每个输出 sheet 对应原来的哪张表，谁来「改写原 sheet」，谁「追加成新 sheet」。

规则：
1. 沿着 Recipe 的步骤往回追，每个输出表都能找到它的「主输入」（每一步取第一个输入表，例如匹配步骤里取被匹配的那张主表）
2. 一张原 sheet 只能被一个输出改写：名字和原 sheet 相同的优先；否则选和原表共同列最多的；再相同取靠前的
3. 只改写同一个原文件里的 sheet（输出以这个文件的副本为底）；其他输出追加成新 sheet，样式照着原表
4. 原 sheet 的名字：学习时有人工结果样例，输出 sheet 名按样例（样例叫「账单明细」就叫「账单明细」）；
   没有样例就保持原 sheet 名不变（输入输出一个样）
5. 配置了 split_by 的输出按列值拆成多个新 sheet
"""

from __future__ import annotations

import pandas as pd

from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workbooks.template_writer import OutputPlan, TemplateSource
from sheetmate.recipe.definition.schema import Recipe, step_table_refs, step_output_tables


def primary_inputs(recipe: Recipe, library: FormulaLibrary) -> dict[str, str]:
    """表名 → 它的主输入别名（输入表自己指向自己）。"""
    mapping = {inp.alias: inp.alias for inp in recipe.inputs}
    for step in recipe.ordered_steps():
        refs = step_table_refs(step, library)
        parent = next((mapping[r] for r in refs if r in mapping), None)
        if parent is not None:
            for output in step_output_tables(step):
                mapping[output] = parent
        if step.formula == "python_script":
            for target in step.params.get("file_tables", {}):
                mapping[target] = target
    return mapping


def plan_outputs(
    recipe: Recipe,
    library: FormulaLibrary,
    sources: dict[str, TemplateSource],
    outputs: dict[str, pd.DataFrame],
) -> list[OutputPlan]:
    """按上面的规则排出每个输出 sheet 的写法。outputs 的 key 是输出 sheet 名，顺序和 output_spec 一致。"""
    primary = primary_inputs(recipe, library)
    summaries: set[str] = set()
    for step in recipe.ordered_steps():
        if step.formula == "group_aggregate" or any(ref in summaries for ref in step_table_refs(step, library)):
            summaries.update(step_output_tables(step))
    spec_by_name = {o.name: o for o in recipe.output_spec}
    candidates: list[tuple[str, pd.DataFrame, TemplateSource | None]] = []
    for name, df in outputs.items():
        spec = spec_by_name.get(name)
        fallback = recipe.ordered_steps()[-1].output if recipe.steps else None
        alias = primary.get(spec.source if spec else fallback)
        src = sources.get(alias) if alias else None
        candidates.append((name, df, src if src is not None and src.usable() else None))

    base_index = _choose_base(candidates)
    base_source = candidates[base_index][2] if base_index is not None else next(iter(sources.values()), None)
    base_path = base_source.path if base_source else None
    edits: dict[str, tuple[tuple[int, int, int], int]] = {}
    for index, (name, frame, source) in enumerate(candidates):
        spec = spec_by_name.get(name)
        output_source = spec.source if spec else recipe.ordered_steps()[-1].output if recipe.steps else None
        if source is None or source.path != base_path or output_source in summaries or (spec and spec.split_by):
            continue
        score = (int(name == source.sheet), len(set(frame.columns) & set(source.columns)), -index)
        if source.sheet not in edits or score > edits[source.sheet][0]:
            edits[source.sheet] = (score, index)
    edit_indices = {index for _, index in edits.values()}
    keep_original_name = not recipe.metadata.get("has_expected")
    plans: list[OutputPlan] = []
    for i, (name, df, src) in enumerate(candidates):
        spec = spec_by_name.get(name)
        if i in edit_indices and src is not None:
            plans.append(OutputPlan(name=src.sheet if keep_original_name else name, df=df, template=src, edit=True))
            continue
        split = spec.split_by if spec else None
        if split and split in df.columns:
            groups = df.groupby(df[split].fillna("(空)").astype(str), sort=False)
            plans.extend(OutputPlan(name=str(value), df=part.reset_index(drop=True), template=base_source) for value, part in groups)
        else:
            plans.append(OutputPlan(name=name, df=df, template=src if src and src.path == base_path else base_source))
    return plans


def _choose_base(candidates: list[tuple[str, pd.DataFrame, TemplateSource | None]]) -> int | None:
    """选出要改写原 sheet 的那个输出：同名 > 共同列最多 > 靠前；底稿文件取第一个有模板的输出的文件。"""
    usable = [(i, name, df, src) for i, (name, df, src) in enumerate(candidates) if src is not None]
    if not usable:
        return None
    base_path = usable[0][3].path
    best: tuple[tuple[int, int, int], int] | None = None
    for i, name, df, src in usable:
        if src.path != base_path:
            continue
        same_name = int(name.strip().lower() == src.sheet.strip().lower())
        overlap = len(set(map(str, df.columns)) & set(src.columns))
        score = (same_name, overlap, -i)
        if best is None or score > best[0]:
            best = (score, i)
    return best[1] if best else None
