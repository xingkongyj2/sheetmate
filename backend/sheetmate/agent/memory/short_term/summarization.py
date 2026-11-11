"""会话标题、历史压缩（TabClaw 的 auto-compact）、客户「整体处理逻辑」总结。"""

from __future__ import annotations

import re
from typing import Any

from sheetmate.agent.memory.short_term.tokens import estimate_tokens, history_tokens, fit_text

from sheetmate.core.security.privacy import redact_derived_text
from sheetmate.agent.llm.prompts import COMPACT_PROMPT, CUSTOMER_SUMMARY_PROMPT, TITLE_PROMPT
from sheetmate.core.observability.conversation_log import record_event

COMPACT_THRESHOLD = 24  # 旧调用方兼容；服务层按估算 token 预算触发压缩
HISTORY_TOKEN_BUDGET = 12_000
RECENT_HISTORY_TOKENS = 4_000
COMPACT_INPUT_TOKENS = 18_000
SUMMARY_TOKEN_BUDGET = 3_000
# Some providers count hidden reasoning against max_tokens even when the
# visible summary is short. Keep the persisted text budget separate from the
# provider output headroom so a complete summary is not cut off mid-response.
COMPACTION_MODEL_MAX_TOKENS = 6_000
# A short title can still consume hidden reasoning tokens before visible text.
TITLE_MODEL_MAX_TOKENS = 2_048
RECENT_USER_ANCHORS = 2
USER_ANCHOR_TOKEN_BUDGET = 600
PRIMER_TOKEN_BUDGET = 600
COMPACT_SOURCE_TOKENS = COMPACT_INPUT_TOKENS - SUMMARY_TOKEN_BUDGET - 1_000
_PRIMER_RE = re.compile(r"(?ms)^【对话开场目标；source_message_id=.*?^【/对话开场目标】\s*$")


def bounded_history(history: list[dict[str, Any]], budget: int = HISTORY_TOKEN_BUDGET) -> list[dict[str, Any]]:
    """Temporary fallback: retain summary, original user goal and recent turns.

    Never changes the persisted compaction cursor. Omitted messages remain
    available for a later successful summary.
    """
    if history_tokens(history) <= budget:
        return list(history)
    base_notice = "（上下文预算提示：部分历史未纳入本次请求，原文仍保留；不要声称已完整阅读。以当前用户要求为准，缺少必要口径时补充确认。）"
    notice = {"role": "assistant", "content": base_notice,
              "context_summary": True, "context_limited": True}
    remaining = max(0, budget - history_tokens([notice]) - 64)
    picked: dict[int, dict[str, Any]] = {}
    user_indexes = [i for i, item in enumerate(history) if item.get("role") == "user"]
    required_anchors = list(dict.fromkeys([*user_indexes[:1], *user_indexes[-1:]]))
    if required_anchors:
        from sheetmate.agent.memory.short_term.errors import ContextOverflow

        per_anchor = remaining // len(required_anchors)
        required: dict[int, dict[str, Any]] = {}
        for index in required_anchors:
            source = history[index]
            cap = 2_000 if index == user_indexes[-1] else 1_000
            item = {**source, "content": fit_text(str(source.get("content") or ""), min(cap, max(0, per_anchor - 8)))}
            required[index] = item
        required_cost = sum(history_tokens([item]) for item in required.values())
        if required_cost > remaining:
            raise ContextOverflow("阶段历史预算不足以保留首条和最新用户要求，请增加预算或缩小范围。")
        picked.update(required)
        remaining -= required_cost

    # Durable summaries, the primer and the previous user turn are secondary
    # anchors. They may use only the room left after the opening and latest
    # user requirements have been reserved.
    secondary_anchors = [i for i, item in enumerate(history)
                         if i not in picked and (item.get("context_summary") or item.get("context_primer"))]
    secondary_anchors.extend(i for i in user_indexes[-2:-1] if i not in picked)
    secondary_anchors = list(dict.fromkeys(secondary_anchors))
    for position, index in enumerate(secondary_anchors):
        allowance = min(1_000, max(0, (remaining - 64) // (len(secondary_anchors) - position)))
        if allowance < 40:
            continue
        item = {**history[index], "content": fit_text(str(history[index].get("content") or ""), allowance - 8)}
        needed = history_tokens([item])
        if needed > remaining - 64:
            continue
        picked[index] = item
        remaining -= needed
    for index in reversed(range(len(history))):
        if index in picked:
            continue
        if remaining < 100:
            break
        item = history[index]
        needed = history_tokens([item])
        if needed > remaining:
            item = {**item, "content": fit_text(str(item.get("content") or ""), max(0, remaining - 8))}
        picked[index] = item
        remaining -= history_tokens([item])
    omitted_ids = [item.get("message_id", item.get("id")) for i, item in enumerate(history)
                   if i not in picked and item.get("message_id", item.get("id")) is not None]
    if omitted_ids:
        notice["content"] = (base_notice + "\n省略来源消息 ID：" + ", ".join(str(value) for value in omitted_ids[:8])
                              + (f" 等（共 {len(omitted_ids)} 条）" if len(omitted_ids) > 8 else "")
                              + "；需要时按 ID 调用 inspect_conversation_history 回查。")
    selected = [notice, *(picked[index] for index in sorted(picked))]
    while history_tokens(selected) > budget:
        removable = [index for index in picked if index not in required_anchors]
        if not removable:
            from sheetmate.agent.memory.short_term.errors import ContextOverflow

            raise ContextOverflow("阶段历史及来源 ID 提示超出预算，请增加预算或缩小范围。")
        picked.pop(min(removable))
        omitted_ids = [item.get("message_id", item.get("id")) for i, item in enumerate(history)
                       if i not in picked and item.get("message_id", item.get("id")) is not None]
        notice["content"] = (base_notice + ("\n省略来源消息 ID：" + ", ".join(str(value) for value in omitted_ids[:8])
                              + (f" 等（共 {len(omitted_ids)} 条）" if len(omitted_ids) > 8 else "")
                              + "；需要时按 ID 调用 inspect_conversation_history 回查。" if omitted_ids else ""))
        selected = [notice, *(picked[index] for index in sorted(picked))]
    return selected


async def make_title(llm: Any, message: str, answer: str) -> str:
    try:
        msg = await llm.chat([{"role": "user", "content": TITLE_PROMPT.format(
            message=redact_derived_text(message)[:500], answer=redact_derived_text(answer)[:300])}],
                             max_tokens=TITLE_MODEL_MAX_TOKENS, _stage="title", _auxiliary=True)
        title = re.sub(r"[\"'“”‘’《》。！!？?]", "", redact_derived_text(msg.content or "").strip()).strip()
        return title[:24] or fallback_title(message)
    except Exception:
        return fallback_title(message)


def fallback_title(message: str) -> str:
    text = re.sub(r"\s+", " ", redact_derived_text(message)).strip()
    return (text[:18] + "…") if len(text) > 18 else (text or "新对话")


def compaction_batch(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Select a prefix of complete messages; never advance through a fragment.

    A single oversized message is returned whole and split only inside
    ``compact_history``. Its caller may commit the message cursor only after
    every one of those source chunks has been summarised successfully.
    """
    batch: list[dict[str, Any]] = []
    used = 0
    for item in items:
        needed = history_tokens([item])
        if batch and used + needed > COMPACT_SOURCE_TOKENS:
            break
        batch.append(item)
        used += needed
    return batch


def primer_block(primer: dict[str, Any] | None) -> str:
    if not primer:
        return ""
    message_id = primer.get("id", primer.get("message_id", "unknown"))
    original = redact_derived_text(str(primer.get("content") or "")).strip()
    if not original:
        return ""
    content = fit_text(original, PRIMER_TOKEN_BUDGET)
    suffix = "（开场目标原文片段；需要完整措辞时按消息 ID 回查）" if content != original else ""
    return (f"【对话开场目标；source_message_id={message_id}】\n{content}{suffix}\n"
            "【/对话开场目标】")


def summary_without_primer(summary: str) -> str:
    """Render the pinned opening goal separately without duplicating its block."""
    return _PRIMER_RE.sub("", redact_derived_text(summary)).strip()


def _source_chunks(item: dict[str, Any], *, budget: int = COMPACT_SOURCE_TOKENS) -> list[str]:
    """Split an oversized source into exact contiguous, labelled text pages."""
    text = redact_derived_text(str(item.get("content") or ""))
    chunks = []
    offset = 0
    # Leave space for the source label and the running summary in every call.
    text_budget = max(1, budget - 120)
    while offset < len(text):
        low, high = 1, len(text) - offset
        while low < high:
            count = (low + high + 1) // 2
            if estimate_tokens(text[offset:offset + count]) <= text_budget:
                low = count
            else:
                high = count - 1
        end = offset + low
        source = item.get("id", item.get("message_id", "unknown"))
        chunks.append(f"[{item.get('role', 'assistant')}; source_message_id={source}; redacted_chars={offset}:{end}/{len(text)}]\n{text[offset:end]}")
        offset = end
    return chunks


async def compact_history(llm: Any, history: list[dict[str, Any]], *,
                          user_anchors: list[dict[str, Any]] | None = None,
                          primer: dict[str, Any] | None = None) -> str | None:
    """Summarise all supplied source text, including oversized single messages.

    Intermediate summaries are temporary. Any failure invalidates the entire
    supplied batch, so a persistence cursor never claims an unread fragment.
    """
    prior_parts = [redact_derived_text(str(item.get("content") or ""))
                   for item in history if item.get("context_summary")]
    existing_primers = []
    for part in prior_parts:
        existing_primers.extend(match.group(0).strip() for match in _PRIMER_RE.finditer(part))
    previous = "\n\n".join(_PRIMER_RE.sub("", part).strip() for part in prior_parts).strip()
    primer_text = primer_block(primer) or (existing_primers[0] if existing_primers else "")
    sources = [item for item in history if not item.get("context_summary") and item.get("content")]
    if not sources:
        return None
    anchors = _recent_user_anchors(history, user_anchors, primer=primer)
    anchor_tokens = estimate_tokens(anchors) if anchors else 0
    primer_tokens = estimate_tokens(primer_text) if primer_text else 0
    identifiers = [str(item.get("id", item.get("message_id", "unknown"))) for item in sources]
    source_range = (f"本批摘要来源消息 ID：{identifiers[0]}–{identifiers[-1]}；"
                    "需要精确条件、数字或旧任务原文时，调用 inspect_conversation_history 按 ID 回查。")
    provenance_tokens = estimate_tokens(source_range)
    summary_tokens = SUMMARY_TOKEN_BUDGET - anchor_tokens - primer_tokens - provenance_tokens - 120
    if summary_tokens < 300:
        return None
    # Reserve the complete prior summary and the actual prompt overhead on
    # every page. A legacy oversized summary is itself paged before new text;
    # it is never silently sliced away to make the request fit.
    overhead = estimate_tokens(COMPACT_PROMPT.format(history="", summary_budget=summary_tokens)) + 80
    source_budget = min(COMPACT_SOURCE_TOKENS, COMPACT_INPUT_TOKENS - SUMMARY_TOKEN_BUDGET - overhead)
    if source_budget < 200:
        return None
    chunks = []
    if estimate_tokens(previous) > SUMMARY_TOKEN_BUDGET:
        chunks.extend(_source_chunks({"role": "已有摘要（派生资料）", "content": previous}, budget=source_budget))
        previous = ""
    chunks.extend(chunk for item in sources for chunk in _source_chunks(item, budget=source_budget))
    batches: list[str] = []
    pages: list[str] = []
    used = 0
    for chunk in chunks:
        size = estimate_tokens(chunk)
        if pages and used + size + estimate_tokens("\n\n") > source_budget:
            batches.append("\n\n".join(pages))
            pages = []
            used = 0
        pages.append(chunk)
        used += size + estimate_tokens("\n\n")
    if pages:
        batches.append("\n\n".join(pages))
    try:
        result = previous
        for page, batch in enumerate(batches, 1):
            context = ("[已有摘要，仅保留有来源且仍有效的事实]\n" + result + "\n\n") if result else ""
            prompt = COMPACT_PROMPT.format(history=context + batch, summary_budget=summary_tokens)
            if estimate_tokens(prompt) > COMPACT_INPUT_TOKENS:
                return None
            record_event("history.compaction.page", "sheetmate.agent.memory.short_term.summarization.compact_history", {
                "page": page, "page_count": len(batches), "input_tokens": estimate_tokens(prompt),
                "summary_token_budget": summary_tokens, "source_count": len(sources),
                "source_first_id": identifiers[0], "source_last_id": identifiers[-1],
            })
            msg = await llm.chat([{"role": "user", "content": prompt}],
                                 max_tokens=COMPACTION_MODEL_MAX_TOKENS,
                                 _stage="history_compaction", _auxiliary=True)
            result = redact_derived_text(msg.content or "").strip()
            finish = getattr(msg, "_sheetmate_finish_reason", getattr(msg, "finish_reason", None))
            if (not result or finish not in {None, "stop"}
                    or estimate_tokens(result) > summary_tokens):
                return None
        result += "\n\n" + source_range
        if primer_text:
            result += "\n\n" + primer_text
        if anchors:
            result = f"{result}\n\n{anchors}"
        if estimate_tokens(result) > SUMMARY_TOKEN_BUDGET:
            return None
        return redact_derived_text(result)
    except Exception:
        return None


def _recent_user_anchors(history: list[dict[str, Any]],
                         user_anchors: list[dict[str, Any]] | None = None,
                         *, primer: dict[str, Any] | None = None) -> str:
    """Keep the latest saved user wording beside the lossy model summary."""
    users = user_anchors if user_anchors is not None else [
        item for item in history if item.get("role") == "user"
    ]
    users = [item for item in users if str(item.get("content") or "").strip()]
    primer_id = primer.get("id", primer.get("message_id")) if primer else None
    if primer_id is not None:
        users = [item for item in users if item.get("id", item.get("message_id")) != primer_id]
    if not users:
        return ""
    selected = users[-RECENT_USER_ANCHORS:]
    lines = ["最近用户要求（系统从会话原始消息确定性附加；长消息可能只保留片段；不将本次例外视为长期规则）："]
    for item in selected:
        message_id = item.get("id", item.get("message_id", "unknown"))
        content = redact_derived_text(str(item.get("content") or "")).strip()
        bounded = fit_text(content, USER_ANCHOR_TOKEN_BUDGET)
        suffix = "（原文片段，中间内容省略；完整消息仍保存在会话记录中）" if bounded != content else ""
        lines.append(f"- [消息 ID {message_id}] {bounded}{suffix}")
    return "\n".join(lines)


async def customer_summary(llm: Any, customer: str, files: list[dict[str, Any]], recipes: list[Any],
                           memory_text: str, previous_summary: str = "") -> str:
    prompt = CUSTOMER_SUMMARY_PROMPT.format(
        customer=customer,
        files=_files_text(files),
        recipes=_recipes_text(recipes),
        memory="\n".join(f"- {line}" for line in _business_memory_lines(memory_text)) or "（无）",
        previous_summary=previous_summary[:3000] or "（无）",
    )
    msg = await llm.chat([{"role": "user", "content": redact_derived_text(prompt)}], max_tokens=4096,
                         _stage="customer_summary", _auxiliary=True)
    return redact_derived_text(msg.content or "").strip()


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
    return redact_derived_text("\n".join(lines))


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
