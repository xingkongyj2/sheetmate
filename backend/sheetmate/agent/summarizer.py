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
                           memory_text: str, previous_summary: str = "") -> str:
    prompt = CUSTOMER_SUMMARY_PROMPT.format(
        customer=customer,
        files=_files_text(files),
        recipes=_recipes_text(recipes),
        memory="\n".join(f"- {line}" for line in _business_memory_lines(memory_text)) or "（无）",
        previous_summary=previous_summary[:3000] or "（无）",
    )
    msg = await llm.chat([{"role": "user", "content": prompt}])
    return (msg.content or "").strip()


def fallback_customer_summary(customer: str, files: list[dict[str, Any]], recipes: list[Any], memory_text: str) -> str:
    """模型不可用时仍只展示已有的业务要求，不把实现信息当作客户口径。"""
    descriptions = [_business_text(r.description) for r in recipes[:3]]
    descriptions = [text for text in descriptions if text]
    lines = ["### 这项工作做什么"]
    lines.extend(descriptions or ["具体要整理成什么结果，还需要补充说明。"])
    lines += ["", "### 需要哪些文件"]
    if files:
        for f in files[:8]:
            purpose = "用于核对结果" if f.get("kind") == "expected" else "作为本次整理的资料"
            lines.append(f"- **{f['filename']}**：{purpose}。")
    else:
        lines.append("- 还没有提供表格资料。")
    lines += ["", "### 具体怎么处理"]
    step_number = 0
    for recipe in recipes[:3]:
        for step in recipe.ordered_steps():
            description = _business_text(step.description)
            title = _business_text(step.name)
            if not description and not title:
                continue
            step_number += 1
            lines.append(f"{step_number}. **{title or '整理相关内容'}**" + (f"：{description}" if description else ""))
    if not step_number:
        lines.append("收到具体处理要求后，再按业务顺序列出做法。")
    lines += ["", "### 最后得到什么"]
    outputs = []
    for recipe in recipes[:3]:
        for out in recipe.output_spec:
            description = _business_text(out.description)
            outputs.append(f"- **{out.name}**" + (f"：{description}" if description else ""))
    lines.extend(outputs or ["最终结果的内容还需要补充说明。"])
    lines += ["", "### 需要留意什么"]
    notes = _business_memory_lines(memory_text)
    lines.extend([f"- {line}" for line in notes] or ["目前没有其他已明确的特殊口径。"])
    return "\n".join(lines)


# 老记忆可能收录过助手的失败说明；它们不是用户的财务/业务规则。
_TECHNICAL_DETAIL = re.compile(
    r"工具链|工具能力|公式库|内置公式|SQL能力|SQL引擎|SQL语法|DuckDB|TemplatePreservationError|"
    r"Python|API\b|run_id|file_id|(?:会话|删除|执行|系统).{0,8}(?:授权|权限|拦截)|"
    r"(?:授权|权限|技术限制)|保真保护|引擎|无法.{0,12}(?:导出|输出|新建|生成|合并)|"
    r"(?:沉淀|固化|绑定).{0,5}(?:流程|客户)|\b(?:SELECT|WITH|JOIN)\b|\b[a-z]+_[a-z_]+\b",
    re.IGNORECASE,
)


def _business_text(value: str | None) -> str:
    """轻量改写常见实现称谓；无法可靠解释的代码片段宁缺毋滥。"""
    text = (value or "").strip()
    text = re.sub(r"[（(\[［](?:内置公式|客户独有(?:逻辑)?)(?:[^）)\]］]*)[）)\]］]", "", text)
    text = re.sub(r"(?:使用|通过|用)\s*(?:SQL|内置公式|客户独有逻辑)\s*(?:来|进行)?", "", text, flags=re.I)
    for old, new in (("宽转长", "逐条整理记录"), ("长转宽", "按项目整理明细"),
                     ("分组汇总", "分类统计"), ("筛选行", "筛选记录"), ("计算列", "计算所需内容")):
        text = text.replace(old, new)
    text = re.sub(r"\bsheets?\b", "工作表", text, flags=re.I).replace("`", "")
    parts = re.split(r"(?<=[。；;\n])", text)
    return "".join(part for part in parts if not _TECHNICAL_DETAIL.search(part)).strip()


def _business_memory_lines(memory_text: str) -> list[str]:
    lines = []
    for raw in memory_text.splitlines():
        line = re.sub(r"^\s*[-*]\s*", "", raw).strip()
        if not line or _TECHNICAL_DETAIL.search(line):
            continue
        category = re.match(r"^(?:业务规则|偏好|背景|经验|领域知识|用户背景|历史经验)\s*[:：]\s*", line)
        if category:
            line = line[category.end():]
            if " = " in line:
                line = line.split(" = ", 1)[1]
        mapping = re.match(r"^字段映射\s*[:：]\s*(.+?)\s*←\s*(.+)$", line)
        if mapping:
            line = f"「{mapping[1]}」也可能写作「{mapping[2]}」。"
        line = _business_text(line)
        if line and line not in lines:
            lines.append(line)
    return lines


def _files_text(files: list[dict[str, Any]]) -> str:
    if not files:
        return "（无）"
    lines = []
    for f in files[:12]:
        sheets = "；".join(
            f"工作表「{s['sheet']}」，包括 {'、'.join(c['name'] for c in s.get('columns', [])[:12])}" for s in f.get("sheets", [])[:3]
        )
        lines.append(f"- {f['filename']}（{f.get('kind_label', '')}）{sheets}")
    return "\n".join(lines)


def _recipes_text(recipes: list[Any]) -> str:
    if not recipes:
        return "（还没有流程）"
    blocks = []
    for r in recipes[:5]:
        steps = "\n".join(
            f"  {index}. {_business_text(s.name)}：{_business_text(s.description)}"
            for index, s in enumerate(r.ordered_steps(), 1)
        )
        inputs = "、".join(_business_text(getattr(i, "description", "")) or i.file_pattern or i.source_file or i.alias for i in r.inputs)
        outputs = "、".join(o.name + (f"（{_business_text(o.description)}）" if o.description else "") for o in r.output_spec)
        blocks.append(f"- 「{_business_text(r.name)}」：{_business_text(r.description)}\n  所需资料：{inputs}\n  结果：{outputs}\n{steps}")
    return "\n".join(blocks)
