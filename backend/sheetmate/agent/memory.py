"""记忆（沿用 TabClaw 的四类记忆）：抽取与筛选。存储由服务层（memories 表）负责。

- preferences       偏好（总是注入，例如「金额保留两位小数」）
- domain_knowledge  业务规则（例如「匹配不到归属的店铺记为新食研」）
- user_context      背景（总是注入，例如「用户是财务，每月 5 号对账」）
- history_insights  经验（例如「这个客户的运单号偶尔带空格」）

作用域：绑定客户的会话 → 记到客户；未绑定 → 记到会话（保存到客户时一起迁移）；用户级偏好对所有会话生效。
"""

from __future__ import annotations

import json
import re
from typing import Any

from sheetmate.agent.prompts import MEMORY_PROMPT

CATEGORIES = ["preferences", "domain_knowledge", "user_context", "history_insights"]
MEMORY_MESSAGE_CHARS = 8_000
MEMORY_ANSWER_CHARS = 2_000
MEMORY_CONTEXT_CHARS = 12_000


class MemoryContextLimit(ValueError):
    """Required human rules cannot fit without losing constraints."""


CATEGORY_LABELS = {
    "preferences": "偏好",
    "domain_knowledge": "业务规则",
    "user_context": "背景",
    "history_insights": "经验",
}


def _bounded_text(value: str, limit: int) -> str:
    """保留长文本的开头和结尾，避免长期要求写在末尾时被静默丢掉。"""
    text = str(value or "")
    if len(text) <= limit:
        return text
    marker = "\n…（中间内容已截断）…\n"
    available = max(0, limit - len(marker))
    head = int(available * 0.65)
    tail = available - head
    return text[:head] + marker + (text[-tail:] if tail else "")


_EPHEMERAL_RULE = re.compile(
    r"本次|这次|此次|这回|本轮|这轮|当前任务|临时|暂时|仅限|只限|一次性|试一下|试试看|例外|"
    r"\bthis\s+(?:time|run|task|request)\b|\bfor\s+now\b|\btemporar", re.IGNORECASE,
)
_NO_PERSIST = re.compile(
    r"(?:不要|别|无需|不用|不必|暂不).{0,12}(?:记住|记忆|保存|长期)|"
    r"(?:不作为|不是).{0,8}(?:长期|永久|默认)|\b(?:do not|don't)\s+(?:remember|save)\b", re.IGNORECASE,
)
_EXPLICIT_FUTURE = re.compile(r"以后|今后|往后|从此|永久|长期|\bfrom\s+now\s+on\b|\bpermanent", re.IGNORECASE)


def supported_memory_value(message: str, value: str, evidence_span: str) -> str | None:
    """Accept a quoted user fact, retaining its complete sentence and scope.

    A substring citation alone does not substantiate a rewritten conclusion.
    Persist the source sentence, including negation and conditions, instead of
    a model paraphrase. Task-local exceptions are never permanent rules.
    """
    value, quote = value.strip(), evidence_span.strip()
    if not value or not quote or value not in quote or quote not in message:
        return None
    start = message.find(quote)
    end = start + len(quote)
    boundaries = "。！？!?"
    left = max((message.rfind(char, 0, start) for char in boundaries), default=-1) + 1
    rights = [position for char in boundaries if (position := message.find(char, end)) >= 0]
    right = end if message[end - 1] in boundaries else min(rights) + 1 if rights else len(message)
    sentence = message[left:right].strip()
    # A business condition often sits on the preceding line ("直营渠道：\n金额...").
    # Keep the entire short paragraph when it spans lines, including conditions
    # separated by punctuation; never turn its last line into a universal rule.
    separators = list(re.finditer(r"\n[ \t\r]*\n", message))
    paragraph_left = max((part.end() for part in separators if part.end() <= start), default=0)
    paragraph_right = min((part.start() for part in separators if part.start() >= end), default=len(message))
    paragraph = message[paragraph_left:paragraph_right].strip()
    if "\n" in paragraph:
        sentence = paragraph
    if not sentence or len(sentence) > 500 or _EPHEMERAL_RULE.search(sentence) or _NO_PERSIST.search(sentence):
        return None
    # A scope qualifier can be in a preceding sentence ("本次例外。金额..."),
    # so absence of that word in the selected quote does not establish permanence.
    if (_EPHEMERAL_RULE.search(message) or _NO_PERSIST.search(message)) and not _EXPLICIT_FUTURE.search(sentence):
        return None
    return sentence


async def extract_memories(llm: Any, message: str, answer: str, *,
                           existing: list[dict[str, Any]] | None = None) -> list[dict[str, str]]:
    """对话结束后轻量抽取（最多 3 条）。失败不影响主流程。"""
    known = [{"category": item["category"], "key": item["key"], "value": str(item.get("value") or "")[:200]}
             for item in (existing or [])[:12]]
    prompt = MEMORY_PROMPT.format(
            message=_bounded_text(message, MEMORY_MESSAGE_CHARS),
            answer=_bounded_text(answer, MEMORY_ANSWER_CHARS),
        )
    prompt += ("\n\n每条 items 还必须包含 evidence_span：逐字引用当前用户消息中支持该记忆的连续原文，"
               "不得引用助手回答或仅引用旧记忆。没有用户原文证据就不提取。"
               "value 必须逐字摘自 evidence_span，保留完整句子、否定词、条件和适用范围，不得自行改写结论。"
               "本次、临时、例外口径以及用户要求不记住的内容绝不能抽取。"
               "history_insights 需要程序验证的执行证据，本抽取器没有此证据，不提取该类别。"
               "相同业务口径沿用下列已有记录的 category 和 key，并以 canonical_key 返回该 key；"
               "旧记忆只用于去重，不代表用户本轮重新授权改变它。\n已有相关记忆："
               + json.dumps(known, ensure_ascii=False, separators=(",", ":")))
    try:
        result = await llm.complete_json(prompt, max_tokens=900, schema_name="memory_extraction")
    except Exception:
        return []
    items = result.get("items", []) if isinstance(result, dict) else result if isinstance(result, list) else []
    if not isinstance(items, list):
        return []
    clean = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", ""))
        key, value = str(item.get("key", "")).strip(), str(item.get("value", "")).strip()
        canonical = str(item.get("canonical_key") or "").strip()
        if any(old["category"] == category and old["key"] == canonical for old in known):
            key = canonical
        evidence = str(item.get("evidence_span") or "").strip()
        # Legacy providers may omit the new field. Accept those responses only
        # when the complete proposed value is itself a verbatim user quote.
        if not evidence and value and value in message:
            evidence = value
        supported_value = supported_memory_value(message, value, evidence)
        if (category in CATEGORIES and category != "history_insights" and key and len(key) <= 120
                and supported_value and len(evidence) <= MEMORY_MESSAGE_CHARS):
            clean.append({"category": category, "key": key, "value": supported_value, "evidence_span": supported_value})
    return clean


def select_relevant(items: list[dict[str, Any]], query: str, limit: int = 30,
                    max_chars: int = MEMORY_CONTEXT_CHARS) -> list[dict[str, Any]]:
    """按相关性和字符预算选记忆，避免常驻 AI 条目挤掉任务相关经验。"""
    terms = _query_terms(query)
    human_relevant: list[tuple[int, int, dict[str, Any]]] = []
    human_other: list[tuple[int, dict[str, Any]]] = []
    relevant: list[tuple[int, int, dict[str, Any]]] = []
    standing: dict[str, list[tuple[int, dict[str, Any]]]] = {
        "preferences": [], "user_context": [], "domain_knowledge": []
    }
    for index, item in enumerate(items):
        score = _memory_relevance(item, terms, query)
        if item.get("source") == "human":
            if score:
                human_relevant.append((score, index, item))
            else:
                human_other.append((index, item))
        elif score:
            relevant.append((score, index, item))
        elif item.get("category") in standing:
            standing[item["category"]].append((index, item))

    # Store order is newest first; preserve it for equally relevant memories.
    human_relevant.sort(key=lambda row: (-row[0], row[1]))
    relevant.sort(key=lambda row: (-row[0], row[1]))

    def cost(item: dict[str, Any]) -> int:
        return len(str(item.get("key") or "")) + len(str(item.get("value") or "")) + 32

    human = [*(row[2] for row in human_relevant), *(row[1] for row in human_other)]
    if sum(cost(item) for item in human) > max_chars:
        raise MemoryContextLimit(
            f"当前适用的 {len(human)} 条人工记忆超出上下文预算（{max_chars} 字符）。"
            "请精简重复规则或缩小流程范围后继续；本轮未截断或忽略人工规则。"
        )

    # Match every category before adding generic standing context. A matched
    # business rule must not be displaced by unrelated preferences or history.
    ai_limit = max(0, limit)
    relevant_items = [row[2] for row in relevant[:ai_limit]]
    remaining = max(0, ai_limit - len(relevant_items))
    standing_items: list[dict[str, Any]] = []
    taken = dict.fromkeys(standing, 0)
    for offset in range(10):
        for category, cap in (("preferences", 5), ("domain_knowledge", 10), ("user_context", 5)):
            if remaining and offset < min(cap, len(standing[category])):
                standing_items.append(standing[category][offset][1])
                taken[category] += 1
                remaining -= 1
    # Quotas reserve room for each category; unused room remains available to
    # other standing memories in their original (newest-first) order.
    overflow = sorted(row for category, rows in standing.items() for row in rows[taken[category]:])
    standing_items.extend(row[1] for row in overflow[:remaining])

    ordered = [*human, *relevant_items, *standing_items]
    chosen: list[dict[str, Any]] = []
    used = 0
    for item in ordered:
        item_cost = cost(item)
        if used + item_cost > max_chars:
            continue
        chosen.append(item)
        used += item_cost
    return chosen


def _chunks(text: str) -> list[str]:
    text = str(text)
    return [text[i : i + 2] for i in range(max(0, len(text) - 1))]


def _query_terms(query: str) -> set[str]:
    """Extract lightweight English words and Chinese bigrams/trigrams."""
    text = str(query or "").lower()
    terms = set(re.findall(r"[a-z0-9_]+", text))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        terms.update(_chunks(run))
        terms.update(run[i:i + 3] for i in range(max(0, len(run) - 2)))
    return terms


def _memory_relevance(item: dict[str, Any], terms: set[str], query: str) -> int:
    if not terms:
        return 0
    key_terms = _query_terms(str(item.get("key") or ""))
    value_terms = _query_terms(str(item.get("value") or ""))
    key_hits = terms & key_terms
    value_hits = terms & value_terms
    score = 4 * len(key_hits) + len(value_hits)
    key = str(item.get("key") or "").lower()
    if len(key) >= 2 and key in str(query or "").lower():
        score += 20
    return score


def format_memories(items: list[dict[str, Any]], field_mapping: dict[str, list[str]] | None = None) -> str:
    lines = []
    human = [i for i in items if i.get("source") == "human"]
    auxiliary = [i for i in items if i.get("source") != "human"]
    if human:
        lines.append("用户维护的记忆（最高优先级）：处理本流程时必须遵守；与 AI 参考冲突时以这些内容为准，AI 不得改写。")
        for item in human:
            text = item['value'] if item['key'].startswith("note_") else f"{item['key']}：{item['value']}"
            lines.append(f"- [{item.get('scope_label', '')}] {text}")
    if auxiliary:
        lines.append("AI 参考记忆（辅助参考，不得覆盖用户维护的记忆）：")
        lines.extend(f"- [{CATEGORY_LABELS.get(i['category'], i['category'])}·{i.get('scope_label', '')}] {i['key']}：{i['value']}"
                     for i in auxiliary)
    if field_mapping:
        lines.append("- [字段映射] " + "；".join(f"{std} ← {'/'.join(aliases)}" for std, aliases in field_mapping.items()))
    return "\n".join(lines)
