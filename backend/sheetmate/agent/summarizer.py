"""会话标题、历史压缩（TabClaw 的 auto-compact）、客户「整体处理逻辑」总结。"""

from __future__ import annotations

import re
from typing import Any

from sheetmate.agent.prompts import COMPACT_PROMPT, CUSTOMER_SUMMARY_PROMPT, TITLE_PROMPT

COMPACT_THRESHOLD = 24  # 历史消息超过这个数就先压缩


async def make_title(llm: Any, message: str, answer: str) -> str:
    try:
        msg = await llm.chat([{"role": "user", "content": TITLE_PROMPT.format(message=message[:500], answer=answer[:300])}], max_tokens=30)
        title = re.sub(r"[\"'“”‘’《》。！!？?]", "", (msg.content or "").strip()).strip()
        return title[:24] or fallback_title(message)
    except Exception:
        return fallback_title(message)


def fallback_title(message: str) -> str:
    text = re.sub(r"\s+", " ", message).strip()
    return (text[:18] + "…") if len(text) > 18 else (text or "新对话")


async def compact_history(llm: Any, history: list[dict[str, Any]]) -> str | None:
    lines = [f"[{m['role']}] {str(m.get('content') or '')[:400]}" for m in history if m.get("content")]
    if not lines:
        return None
    try:
        msg = await llm.chat([{"role": "user", "content": COMPACT_PROMPT.format(history="\n\n".join(lines))}])
        return (msg.content or "").strip() or None
    except Exception:
        return None


async def customer_summary(llm: Any, customer: str, files: list[dict[str, Any]], recipes: list[Any],
                           memory_text: str) -> str:
    prompt = CUSTOMER_SUMMARY_PROMPT.format(
        customer=customer,
        files=_files_text(files),
        recipes=_recipes_text(recipes),
        memory=memory_text or "（无）",
    )
    msg = await llm.chat([{"role": "user", "content": prompt}])
    return (msg.content or "").strip()


def fallback_customer_summary(customer: str, files: list[dict[str, Any]], recipes: list[Any], memory_text: str) -> str:
    """没有配置模型时的确定性总结：直接由 Recipe 结构生成，保证客户页始终能看懂。"""
    lines = ["### 要处理的表"]
    if files:
        for f in files[:8]:
            sheets = "、".join(f"{s['sheet']}（{s['rows']} 行）" for s in f.get("sheets", [])[:3])
            lines.append(f"- **{f['filename']}**：{sheets or '已上传'}")
    else:
        lines.append("- 还没有上传文件")
    if not recipes:
        lines += ["", "### 整体处理逻辑", "- 还没有沉淀处理流程。可以新建对话，绑定这个客户，上传文件后让 AI 生成流程。"]
    for recipe in recipes[:3]:
        lines += ["", f"### 整体处理逻辑 · {recipe.name}", recipe.description or "（无说明）", "", "### 执行步骤"]
        for step in recipe.ordered_steps():
            kind = "客户独有" if step.formula_kind == "customer" or step.formula == "custom_sql" else "内置公式"
            lines.append(f"{step.id}. **{step.name}**（{kind}）：{step.description}")
        lines += ["", "### 交付结果"]
        for out in recipe.output_spec:
            lines.append(f"- sheet「{out.name}」：{out.description or '来自 ' + out.source}")
    if memory_text:
        lines += ["", "### 注意事项", memory_text]
    return "\n".join(lines)


def _files_text(files: list[dict[str, Any]]) -> str:
    if not files:
        return "（无）"
    lines = []
    for f in files[:12]:
        sheets = "；".join(
            f"{s['sheet']}：{s['rows']} 行，列 {[c['name'] for c in s.get('columns', [])][:12]}" for s in f.get("sheets", [])[:3]
        )
        lines.append(f"- {f['filename']}（{f.get('kind_label', '')}）{sheets}")
    return "\n".join(lines)


def _recipes_text(recipes: list[Any]) -> str:
    if not recipes:
        return "（还没有流程）"
    blocks = []
    for r in recipes[:5]:
        steps = "\n".join(
            f"  {s.id}. {s.name}［{'客户独有' if s.formula_kind == 'customer' or s.formula == 'custom_sql' else '内置公式'}·{s.formula}］{s.description}"
            for s in r.ordered_steps()
        )
        inputs = "、".join(f"{i.alias}（{i.file_pattern or i.source_file}）" for i in r.inputs)
        outputs = "、".join(o.name for o in r.output_spec)
        blocks.append(f"- 「{r.name}」v{r.version}：{r.description}\n  输入：{inputs}\n  输出：{outputs}\n{steps}")
    return "\n".join(blocks)
