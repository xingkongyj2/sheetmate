from __future__ import annotations

import json
from typing import Any

from sheetmate.agent.llm import LLMError
from sheetmate.agent.prompts import INTENT_PROMPT
from sheetmate.agent.tools import AgentContext


async def infer_mode(llm: Any, ctx: AgentContext, message: str, history: list[dict[str, Any]],
                     plan_steps: list[dict[str, Any]] | None = None) -> str:
    context = {
        "message": message,
        "history": [{"role": item["role"], "content": str(item.get("content", ""))[-4000:]} for item in history[-8:]],
        "tables": [{"name": table.name, "role": table.role, "columns": table.column_names} for table in ctx.visible_tables()],
        "plan_steps": plan_steps or [],
    }
    result = await llm.complete_json(INTENT_PROMPT + "\n\n" + json.dumps(context, ensure_ascii=False))
    mode = result.get("mode") if isinstance(result, dict) else None
    if mode not in {"analyze", "modify"}:
        raise LLMError("暂时无法确定这次需求的处理方式，本次尚未处理文件，请重试。")
    return mode
