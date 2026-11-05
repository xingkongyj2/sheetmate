"""规划与澄清（移植自 TabClaw agent/planner.py）。

- generate_plan：生成结构化步骤（用户可以在界面上修改，确认后按步执行）
- check_clarification：判断需求是否有真正的歧义，有就给选项
"""

from __future__ import annotations

from typing import Any

from sheetmate.agent.llm import LLMError
from sheetmate.agent.prompts import CLARIFY_PROMPT, PLAN_PROMPT
from sheetmate.agent.tools import AgentContext


def _tables_text(ctx: AgentContext, max_cols: int = 10) -> str:
    lines = []
    for t in ctx.visible_tables():
        cols = ", ".join(t.column_names[:max_cols]) + (" …" if len(t.columns) > max_cols else "")
        lines.append(f"- `{t.name}`（{t.label}）{t.rows} 行 | 列：{cols}")
    return "\n".join(lines) or "（还没有表）"


async def generate_plan(llm: Any, ctx: AgentContext, message: str) -> dict[str, Any]:
    prompt = PLAN_PROMPT.format(tables=_tables_text(ctx), memory=ctx.host.memory_text(message) or "（无）", message=message)
    try:
        image_kwargs = {"image_parts": ctx.image_parts} if ctx.image_parts else {}
        plan = await llm.complete_json(prompt, **image_kwargs)
        steps = [s for s in plan.get("steps", []) if isinstance(s, dict) and s.get("description")]
        if not steps:
            raise ValueError("没有步骤")
        for i, step in enumerate(steps, start=1):
            step["id"] = i
        return {"title": str(plan.get("title") or "执行计划"), "steps": steps}
    except Exception as exc:
        if ctx.image_parts and isinstance(exc, LLMError):
            # Do not silently offer a plan based on an image the model could not read.
            raise
        # 兜底计划（与 TabClaw 一致）
        return {
            "title": "执行需求",
            "steps": [
                {"id": 1, "description": "理解需求并确定表格结构" if ctx.mode == "create" or not ctx.visible_tables() else "查看相关表的结构和数据"},
                {"id": 2, "description": f"执行需求：{message}"},
                {"id": 3, "description": "整理并说明结果"},
            ],
        }


async def check_clarification(llm: Any, ctx: AgentContext, message: str) -> dict[str, Any]:
    if not ctx.visible_tables():
        return {"needs_clarification": False}
    try:
        result = await llm.complete_json(CLARIFY_PROMPT.format(message=message, tables=_tables_text(ctx, 6)))
        if not isinstance(result.get("needs_clarification"), bool):
            return {"needs_clarification": False}
        options = [str(o) for o in result.get("options") or [] if str(o).strip()]
        if result["needs_clarification"] and len(options) < 2:
            return {"needs_clarification": False}
        return {"needs_clarification": result["needs_clarification"], "question": result.get("question", ""), "options": options[:4]}
    except Exception:
        return {"needs_clarification": False}
