"""内层 ReAct 修复器：Recipe 某一步出错时，把结构化错误上下文交给 LLM，拿回修好的步骤。

外层是 Plan-and-Execute（先生成整个 Recipe 再逐步执行），只有出错时才切进来「观察错误 → 推理原因 → 修复 → 再执行」，
重试次数由 RecipeRunner 控制（默认 3 次），超过次数生成人话错误报告。
"""

from __future__ import annotations

import json
from typing import Any

from sheetmate.agent.prompts import REPAIR_PROMPT, REPAIR_SYSTEM
from sheetmate.engine.script_store import resolve_script_code
from sheetmate.recipe.executor import RepairContext
from sheetmate.recipe.schema import RecipeStep


class LLMStepRepairer:
    def __init__(self, llm: Any):
        self.llm = llm

    async def repair(self, ctx: RepairContext) -> RecipeStep | None:
        step = ctx.step
        params = dict(step.params)
        if step.kind == "python":
            # 只在修复上下文按需读取代码，运行记录和节点仍保存脚本文件引用。
            try:
                params["code"] = resolve_script_code(params)
            except (OSError, ValueError):
                pass
        try:
            spec_text = _formula_text(ctx, step.formula)
        except ValueError:
            spec_text = "（当前公式不存在，请换一个可用的公式）"
        history = "\n".join(
            f"- 第 {h['attempt']} 次：公式 {h['formula']}，参数 {json.dumps(h['params'], ensure_ascii=False)}，错误：{h['error']}"
            + (f"\nPython 错误栈：\n{h['stderr']}" if h.get("stderr") else "")
            + (f"；修改说明：{h['explanation']}" if h.get("explanation") else "")
            for h in ctx.history[:-1]
        ) or "（第一次修复）"
        prompt = REPAIR_PROMPT.format(
            step_id=step.id,
            attempt=ctx.attempt,
            step=json.dumps(
                {"name": step.name, "kind": step.kind, "formula": step.formula, "params": params, "output": step.output, "description": step.description},
                ensure_ascii=False, indent=1,
            ),
            error=ctx.error + (f"\n\nPython 错误栈（末尾）：\n{ctx.script_stderr[-8000:]}" if ctx.script_stderr else ""),
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
        if result.get("kind") == "python":
            formula = "python_script"
        elif result.get("kind") == "sql":
            formula = "custom_sql"
        if formula != "python_script" and not ctx.library.has(formula):
            formula = step.formula
        params = result.get("params")
        if not isinstance(params, dict) or not params:
            return None
        explanation = str(result.get("explanation") or "本次修正未提供具体修改说明，将以重新执行与校验结果确认是否生效。")
        return RecipeStep.model_validate({**step.model_dump(),
            "formula": formula, "kind": "python" if formula == "python_script" else "sql" if formula == "custom_sql" else "formula",
            "formula_kind": ctx.library.resolve(formula).kind if ctx.library.has(formula) else "builtin",
            "params": params, "explanation": f"修正方案：{explanation}",
        })


def _formula_text(ctx: RepairContext, formula_id: str) -> str:
    if formula_id == "python_script":
        return (
            "python_script（Python 脚本）：params.code 为完整脚本；input_tables 为真实表名列表；"
            "input_files 为脚本文件别名到流程输入别名或前序步骤output/文件名的映射；"
            "output_tables 为 result_tables 键到工作区表名的映射，纯文件步骤使用空对象。"
            "已提供 pd、np、openpyxl、tables、files、OUTPUT_DIR、result_tables、deliver_files、result、check。"
            "files 是文件别名到 Path 的字典，例如 files['source']；deliver_files 是默认空列表，"
            "文件保存到 OUTPUT_DIR 后使用 deliver_files = [OUTPUT_DIR / '结果.xlsx'] 或 deliver_files.append(path)，"
            "不能按文件名给 deliver_files 下标赋值。params.output_files 是流程输出声明，不是脚本交付变量。"
            "根据错误栈中的具体脚本行修复；不要仅凭 list indices 报错就更改 input_files。保留业务 check 校验和输入输出契约。"
        )
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
