"""后端全局规划与必要的业务澄清（基于 TabClaw agent/planner.py）。

- generate_plan：结合完整任务上下文生成结构化步骤，后端直接逐步执行
- check_clarification：判断需求是否有真正的歧义，有就给选项
"""

from __future__ import annotations

import json
from typing import Any

from sheetmate.agent.llm import LLMError, LLMNotConfigured
from sheetmate.agent.prompts import CLARIFY_PROMPT, PLAN_PROMPT
from sheetmate.agent.tools import AgentContext


def _tables_text(ctx: AgentContext, max_cols: int = 10) -> str:
    lines = []
    for t in ctx.visible_tables():
        cols = ", ".join(t.column_names[:max_cols]) + (" …" if len(t.columns) > max_cols else "")
        lines.append(f"- `{t.name}`（{t.label}）{t.rows} 行 | 列：{cols}")
    return "\n".join(lines) or "（还没有表）"


def _context_text(ctx: AgentContext, history: list[dict[str, Any]], files: list[dict[str, Any]]) -> str:
    """只传文本历史和文件元信息；图片走多模态参数，避免把 base64 放入提示词。"""
    return json.dumps({
        "mode": ctx.mode,
        "history": [{"role": item["role"], "content": str(item.get("content", ""))[-8000:]}
                    for item in history[-12:]],
        "files": [{key: file.get(key) for key in ("file_id", "filename", "kind", "scope_label", "tables", "media_type")}
                  | {"sheets": [sheet.get("sheet") for sheet in file.get("sheets", [])]}
                  for file in files],
    }, ensure_ascii=False)


def _image_kwargs(ctx: AgentContext, history: list[dict[str, Any]]) -> dict[str, Any]:
    parts = [part for item in history[-12:] for part in item.get("image_parts", [])] + ctx.image_parts
    return {"image_parts": parts} if parts else {}


async def generate_plan(llm: Any, ctx: AgentContext, message: str, *,
                        history: list[dict[str, Any]] | None = None,
                        files: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    history = history or []
    prompt = PLAN_PROMPT.format(tables=_tables_text(ctx), memory=ctx.host.memory_text(message) or "（无）",
                              message=message, context=_context_text(ctx, history, files or []))
    try:
        plan = await llm.complete_json(prompt, **_image_kwargs(ctx, history))
    except LLMError:
        raise
    except Exception as exc:
        raise LLMError("自动规划失败，本次尚未执行处理，请重试。") from exc
    steps = plan.get("steps") if isinstance(plan, dict) else None
    if (not isinstance(steps, list) or not 1 <= len(steps) <= 8
            or any(not isinstance(step, dict) or not isinstance(step.get("description"), str)
                   or not step["description"].strip() for step in steps)):
        raise LLMError("自动规划未生成有效的执行步骤，本次尚未执行处理，请重试。")
    return {
        "title": str(plan.get("title") or "执行计划").strip() or "执行计划",
        "steps": [{"id": index, "description": step["description"].strip()}
                  for index, step in enumerate(steps, start=1)],
    }


async def check_clarification(llm: Any, ctx: AgentContext, message: str, *,
                              history: list[dict[str, Any]] | None = None,
                              files: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    history = history or []
    try:
        prompt = CLARIFY_PROMPT.format(message=message, tables=_tables_text(ctx, 6),
                                     context=_context_text(ctx, history, files or []))
        result = await llm.complete_json(prompt, **_image_kwargs(ctx, history))
        if not isinstance(result, dict) or result.get("needs_clarification") is not True:
            return {"needs_clarification": False}
        options = result.get("options")
        question = result.get("question")
        if (not isinstance(question, str) or not question.strip() or not isinstance(options, list)
                or any(not isinstance(option, str) or not option.strip() for option in options)):
            return {"needs_clarification": False}
        options = list(dict.fromkeys(option.strip() for option in options))[:4]
        if len(options) < 2:
            return {"needs_clarification": False}
        return {"needs_clarification": True, "question": question.strip(), "options": options}
    except LLMNotConfigured:
        raise
    except Exception:
        # 澄清检查不可用不制造额外等待；全局规划仍必须成功，执行中也可以 ask_user。
        return {"needs_clarification": False}
