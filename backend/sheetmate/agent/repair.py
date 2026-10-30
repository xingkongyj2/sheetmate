"""内层 ReAct 修复器：Recipe 某一步出错时，把结构化错误上下文交给 LLM，拿回修好的步骤。

外层是 Plan-and-Execute（先生成整个 Recipe 再逐步执行），只有出错时才切进来「观察错误 → 推理原因 → 修复 → 再执行」，
重试次数由 RecipeRunner 控制（默认 3 次），超过次数生成人话错误报告。
"""

from __future__ import annotations

import json
from typing import Any

from sheetmate.agent.prompts import REPAIR_PROMPT, REPAIR_SYSTEM
from sheetmate.recipe.executor import RepairContext
from sheetmate.recipe.schema import RecipeStep


class LLMStepRepairer:
    def __init__(self, llm: Any):
        self.llm = llm

    async def repair(self, ctx: RepairContext) -> RecipeStep | None:
        step = ctx.step
        try:
            spec_text = _formula_text(ctx, step.formula)
        except ValueError:
            spec_text = "（当前公式不存在，请换一个可用的公式）"
        history = "\n".join(
            f"- 第 {h['attempt']} 次：公式 {h['formula']}，参数 {json.dumps(h['params'], ensure_ascii=False)}，错误：{h['error']}"
            + (f"；修改说明：{h['explanation']}" if h.get("explanation") else "")
            for h in ctx.history[:-1]
        ) or "（第一次修复）"
        prompt = REPAIR_PROMPT.format(
            step_id=step.id,
            attempt=ctx.attempt,
            step=json.dumps(
                {"name": step.name, "formula": step.formula, "params": step.params, "output": step.output, "description": step.description},
                ensure_ascii=False, indent=1,
            ),
            error=ctx.error,
            history=history,
            tables=ctx.tables_text,
            formula=spec_text,
            catalog="、".join(ctx.library.ids()),
            recipe=_recipe_outline(ctx),
        )
        result = await self.llm.complete_json(prompt, system=REPAIR_SYSTEM)
        if not isinstance(result, dict) or result.get("give_up"):
            return None
        formula = str(result.get("formula") or step.formula)
        if not ctx.library.has(formula):
            formula = step.formula
        params = result.get("params")
        if not isinstance(params, dict) or not params:
            return None
        explanation = str(result.get("explanation") or "调整了参数")
        return step.model_copy(update={"formula": formula, "params": params, "explanation": f"自动修复：{explanation}"}, deep=True)


def _formula_text(ctx: RepairContext, formula_id: str) -> str:
    spec = ctx.library.resolve(formula_id).spec
    lines = [f"{spec.id}（{spec.name}）：{spec.intent}"]
    for name, p in spec.params.items():
        flag = "必填" if p.required else "可选"
        extra = f"，可选值 {p.values}" if p.values else ""
        lines.append(f"- {name}（{p.type}，{flag}{extra}）：{p.desc}")
    return "\n".join(lines)


def _recipe_outline(ctx: RepairContext) -> str:
    return "\n".join(
        f"{s.id}. {s.name}：{s.formula} → {s.output}" + ("  ← 当前失败的步骤" if s.id == ctx.step.id else "")
        for s in ctx.recipe.ordered_steps()
    )
