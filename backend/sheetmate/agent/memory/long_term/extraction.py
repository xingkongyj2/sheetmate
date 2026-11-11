"""从用户原文抽取长期记忆，验证作用域、证据和明确修改授权。"""
from __future__ import annotations

import json
import re
from typing import Any

from sheetmate.agent.llm.prompts import MEMORY_PROMPT
from sheetmate.core.security.privacy import contains_sensitive_memory
from sheetmate.agent.memory.long_term.policy import (
    CATEGORIES,
    MEMORY_MESSAGE_CHARS,
    MEMORY_ANSWER_CHARS,
    MEMORY_EXTRACTION_TOKENS,
)


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
    r"本次|这次|此次|这回|本轮|这轮|当前任务|临时|暂时|一次性|试一下|试试看|例外|"
    r"(?:仅限|只限)(?:一次|此任务|这个任务)|"
    r"\bthis\s+(?:time|run|task|request)\b|\bfor\s+now\b|\btemporar", re.IGNORECASE,
)
_NO_PERSIST = re.compile(
    r"(?:不要|别|无需|不用|不必|暂不).{0,12}(?:记住|记忆|保存|长期)|"
    r"(?:不作为|不是).{0,8}(?:长期|永久|默认)|\b(?:do not|don't)\s+(?:remember|save)\b", re.IGNORECASE,
)
_EXPLICIT_FUTURE = re.compile(r"以后|今后|往后|从此|永久|长期|记住|\bfrom\s+now\s+on\b|\bpermanent|\bremember\b", re.IGNORECASE)
_SCOPE_PREFIX = re.compile(r"^(?:仅(?:当|在|对|限)?|只有|只对|只在|若|如果|当|对于|针对|除非|除了|不适用|适用于|在.+(?:时|下))")
_REPLACE_VERB = re.compile(r"修改为|更改为|调整为|替换为|更新为|改成|改为")
_REPLACE_DENIAL = re.compile(r"(?:不要|别|勿|无需|不用|不必|暂不|不能|不得).{0,16}(?:改|更新|替换|调整)|(?:如果|假如|假设|若).{0,20}(?:以后|今后|往后)")
_PARTIAL_REPLACEMENT = re.compile(r"只(?:把|将|改|修改|调整)|仅(?:把|将|改|修改|调整)|其中|这一项|这一个条件|其他.{0,8}不变|其余.{0,8}不变")
_RULE_CLAUSES = re.compile(r"[，,；;。\n]|并且|同时|且|(?<![合归兼])并(?![集列行发购重])")


def _is_scope_qualifier(text: str) -> bool:
    prefix = re.compile(r"^(?:(?:请)?记住|以后|今后|往后|从此|永久|长期)[，,：:\s]*")
    text = text.strip()
    while (match := prefix.match(text)):
        text = text[match.end():]
    text = re.sub(r"^(?:但是|不过|但)[，,：:\s]*", "", text)
    return bool(_SCOPE_PREFIX.search(text) or text.startswith("只限"))


def _outside_quotes(text: str) -> list[bool]:
    closing: list[str] = []
    pairs = {"“": "”", "‘": "’", "「": "」", "『": "』", '"': '"'}
    outside = []
    for char in text:
        outside.append(not closing)
        if closing and char == closing[-1]:
            closing.pop()
        elif char in pairs:
            closing.append(pairs[char])
    return outside


def _rule_units(text: str) -> list[tuple[int, int, str]]:
    outside = _outside_quotes(text)
    units = []
    start = 0
    for index, char in enumerate(text):
        if outside[index] and char in "。！？!?；;\n":
            if text[start:index + 1].strip():
                units.append((start, index + 1, text[start:index + 1]))
            start = index + 1
    if text[start:].strip():
        units.append((start, len(text), text[start:]))
    return units


def _has_global_no_persist(message: str) -> bool:
    local_exception = False
    previous_end = 0
    for start, end, part in _rule_units(message):
        if "\n\n" in message[previous_end:start + 1]:
            local_exception = False
        if _EPHEMERAL_RULE.search(part):
            local_exception = True
        elif _EXPLICIT_FUTURE.search(part) and not _NO_PERSIST.search(part):
            local_exception = False
        if _NO_PERSIST.search(part) and not local_exception:
            return True
        previous_end = end
    return False


def _supported_span(message: str, start: int, end: int) -> str | None:
    separators = list(re.finditer(r"\n[ \t\r]*\n", message))
    paragraph_left = max((part.end() for part in separators if part.end() <= start), default=0)
    paragraph_right = min((part.start() for part in separators if part.start() >= end), default=len(message))
    units = [(paragraph_left + left, paragraph_left + right, part)
             for left, right, part in _rule_units(message[paragraph_left:paragraph_right])]
    covered = [index for index, (left, right, _) in enumerate(units) if left < end and right > start]
    if not covered:
        return None
    first, last = covered[0], covered[-1]
    # A newline can separate a heading/condition from the actual rule. Carry
    # those prefixes forward, but keep a following task-local exception out of
    # an independently stated permanent rule.
    while first:
        _, _, previous = units[first - 1]
        if previous.rstrip().endswith(tuple("。！？!?；;")) and not _is_scope_qualifier(previous):
            break
        first -= 1
    while last + 1 < len(units):
        following = units[last + 1][2]
        if (_EPHEMERAL_RULE.search(following) or _NO_PERSIST.search(following)
                or not (_is_scope_qualifier(following) or units[last][2].rstrip().endswith(("：", ":")))):
            break
        last += 1
    sentence = message[units[first][0]:units[last][1]].strip()
    if not sentence or len(sentence) > 500 or _EPHEMERAL_RULE.search(sentence) or _NO_PERSIST.search(sentence):
        return None
    # No-save instructions apply globally unless a preceding local exception
    # clearly owns them ("本次例外…；不要把这条当成长期规则").
    if _has_global_no_persist(message):
        return None
    if (_EPHEMERAL_RULE.search(message) or _NO_PERSIST.search(message)) and not _EXPLICIT_FUTURE.search(sentence):
        return None
    return sentence


def supported_memory_value(message: str, value: str, evidence_span: str) -> str | None:
    """Accept a quoted user fact, retaining its complete sentence and scope.

    A substring citation alone does not substantiate a rewritten conclusion.
    Persist the source sentence, including negation and conditions, instead of
    a model paraphrase. Task-local exceptions are never permanent rules.
    """
    value, quote = value.strip(), evidence_span.strip()
    if not value or not quote or value not in quote or quote not in message:
        return None
    starts = [match.start() for match in re.finditer(re.escape(quote), message)]
    supported = [_supported_span(message, start, start + len(quote)) for start in starts]
    # Repeated citations with different qualifiers are ambiguous; selecting
    # only the first occurrence could persist a task-local instruction.
    return supported[0] if supported and supported[0] and all(span == supported[0] for span in supported) else None


def _scope_identity(item: dict[str, Any]) -> tuple[Any, ...]:
    if item.get("customer_id"):
        return ("customer", None, item["customer_id"], None, item.get("recipe_id"))
    if item.get("conversation_id"):
        return ("conversation", None, None, item["conversation_id"], item.get("recipe_id"))
    return ("personal", item.get("user_id"), None, None, item.get("recipe_id"))


def _same_scope(item: dict[str, Any], target_scope: dict[str, Any] | None) -> bool:
    if target_scope is None:
        # Compatibility for old callers that supplied an already-filtered
        # context without identity fields. Replacement always requires scope.
        return not any(field in item for field in ("scope", "user_id", "customer_id", "conversation_id", "recipe_id"))
    if not any(field in item for field in ("user_id", "customer_id", "conversation_id")):
        return False
    return _scope_identity(item) == _scope_identity(target_scope)


def _replacement_parts(text: str) -> tuple[str, str] | None:
    outside = _outside_quotes(text)
    verb = next((match for match in _REPLACE_VERB.finditer(text) if outside[match.start()]), None)
    if not verb:
        return None
    before, after = text[:verb.start()], text[verb.end():].strip(" ：:‘’“”\"。\n")
    unquoted = "".join(char if outside[index] else " " for index, char in enumerate(text))
    if not _EXPLICIT_FUTURE.search(before) or _REPLACE_DENIAL.search(unquoted) or not after:
        return None
    return before, after


def _contains_rule_change(text: str) -> bool:
    outside = _outside_quotes(text)
    return any(outside[match.start()] for match in _REPLACE_VERB.finditer(text))


def _references_rule(before: str, old: dict[str, Any]) -> bool:
    key, value = str(old.get("key") or ""), str(old.get("value") or "")
    if value and value in before:
        return True
    if not key or key not in before:
        return False
    # Exact key references are usable even when the stored value contains
    # conversational wording. Topic similarity alone is never an identity.
    left = before[:before.rfind(key)]
    return bool(re.search(r"(?:把|将|规则|记忆|口径|偏好|[‘’“”\"])[^。！？!?；;]{0,20}$", left)
                or key.endswith(("规则", "口径", "偏好")))


def _complete_replacement(before: str, after: str, old_value: str) -> bool:
    if _PARTIAL_REPLACEMENT.search(before + after):
        return False
    clauses = [re.sub(r"^(?:以后|今后|往后|从此)", "", part.strip(" ：:‘’“”\""))
               for part in _RULE_CLAUSES.split(old_value)]
    clauses = [part for part in clauses if len(part) > 2]
    if len(clauses) <= 1:
        return True
    if re.search(r"(?:整条|完整|全部|整体).{0,8}(?:规则|口径|记忆)|(?:规则|口径|记忆).{0,8}(?:整条|完整|全部|整体)", before):
        return True
    new_clauses = [part.strip(" ：:‘’“”\"") for part in _RULE_CLAUSES.split(after)]
    new_clauses = [part for part in new_clauses if len(part) > 2]
    # For a compound rule, a single changed clause cannot erase the others.
    # Require the replacement to restate them, or an explicit whole-rule edit.
    unchanged = sum(clause in after for clause in clauses)
    return len(new_clauses) >= len(clauses) and unchanged >= len(clauses) - 1


async def extract_memories(llm: Any, message: str, answer: str, *,
                           existing: list[dict[str, Any]] | None = None,
                           target_scope: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """对话结束后轻量抽取（最多 3 条）。失败不影响主流程。"""
    existing = [item for item in (existing or []) if isinstance(item, dict)]
    known = []
    known_chars = 0
    for old in existing:
        candidate = {field: old.get(field) for field in
                     ("id", "category", "key", "value", "source", "scope", "user_id", "customer_id", "conversation_id", "recipe_id")}
        chars = len(json.dumps(candidate, ensure_ascii=False, separators=(",", ":")))
        if chars > 2_000 or known_chars + chars > 6_000:
            continue
        known.append(candidate)
        known_chars += chars
        if len(known) == 12:
            break
    prompt = MEMORY_PROMPT.format(
            message=_bounded_text(message, MEMORY_MESSAGE_CHARS),
            answer=_bounded_text(answer, MEMORY_ANSWER_CHARS),
        )
    prompt += ("\n\n每条 items 还必须包含 evidence_span：逐字引用当前用户消息中支持该记忆的连续原文，"
               "不得引用助手回答或仅引用旧记忆。没有用户原文证据就不提取。"
               "value 必须逐字摘自 evidence_span，保留完整句子、否定词、条件和适用范围，不得自行改写结论。"
               "本次、临时、例外口径以及用户要求不记住的内容绝不能抽取。"
               "history_insights 需要程序验证的执行证据，本抽取器没有此证据，不提取该类别。"
               "相同原文沿用下列同作用域已有记录的 category 和 key，并以 canonical_key 返回该 key；"
               "同主题但内容不同不能作为相同规则。旧记忆不代表用户本轮重新授权改变它。"
               "仅当用户明确说以后把某条旧规则改为完整新规则，且旧 key 或原文唯一定位同作用域 AI 记录时，"
               "才可沿用该条旧 key，程序会校验目标及完整旧值；人工记录不可自动修改。"
               "复合规则只修改其中一项、其他条款未重述时不要提取，不能另起 key 保存冲突规则。"
               "长期要求后面的本次例外用换行或分号分开时，只提取前面的长期要求及它前置的适用条件。"
               "\n目标作用域：" + json.dumps(target_scope, ensure_ascii=False, separators=(",", ":"))
               + "\n已有相关记忆："
               + json.dumps(known, ensure_ascii=False, separators=(",", ":")))
    try:
        result = await llm.complete_json(prompt, max_tokens=MEMORY_EXTRACTION_TOKENS, schema_name="memory_extraction")
    except Exception:
        return []
    items = result.get("items", []) if isinstance(result, dict) else result if isinstance(result, list) else []
    return validate_memory_candidates(message, items, existing=existing, target_scope=target_scope)


def validate_memory_candidates(message: str, items: Any, *,
                               existing: list[dict[str, Any]] | None = None,
                               target_scope: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Apply the same source, deduplication and edit checks to every AI write."""
    if not isinstance(items, list):
        return []
    existing = [item for item in (existing or []) if isinstance(item, dict)]
    clean: list[dict[str, Any]] = []
    seen_values: set[tuple[str, str]] = set()
    seen_keys: set[tuple[str, str]] = set()
    for item in items[:12]:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", ""))
        key, value = str(item.get("key", "")).strip(), str(item.get("value", "")).strip()
        canonical = str(item.get("canonical_key") or "").strip()
        evidence = str(item.get("evidence_span") or "").strip()
        # Legacy providers may omit the new field. Accept those responses only
        # when the complete proposed value is itself a verbatim user quote.
        if not evidence and value and value in message:
            evidence = value
        supported_value = supported_memory_value(message, value, evidence)
        if (category in CATEGORIES and category != "history_insights" and key and len(key) <= 120
                and supported_value and not contains_sensitive_memory(key)
                and not contains_sensitive_memory(supported_value) and not contains_sensitive_memory(evidence)
                and len(evidence) <= MEMORY_MESSAGE_CHARS):
            if (category, supported_value) in seen_values:
                continue
            scoped = [old for old in existing if old.get("category") == category and _same_scope(old, target_scope)]
            same_value = [old for old in scoped if old.get("value") == supported_value]
            replacement = None
            parts = _replacement_parts(supported_value)
            if not same_value and _contains_rule_change(supported_value) and not parts:
                # A denied, hypothetical or non-permanent edit cannot bypass
                # update checks by asking the model to invent a different key.
                continue
            referenced = [old for old in scoped if parts and _references_rule(parts[0], old)]
            if same_value:
                key = str(next((old for old in same_value if old.get("key") == canonical), same_value[0])["key"])
            elif referenced:
                if (target_scope is None or len(referenced) != 1 or referenced[0].get("source") != "ai"
                        or not isinstance(referenced[0].get("id"), int)
                        or not _complete_replacement(*parts, str(referenced[0].get("value") or ""))):
                    continue
                replacement = referenced[0]
                key = str(replacement["key"])
            elif any(old.get("key") in {key, canonical} for old in scoped):
                # A model-selected canonical key is not permission to update a
                # different value, and generating a new key must not bypass it.
                continue
            elif parts:
                # An unresolved/foreign-scope modification is not a new rule.
                continue
            if (category, key) in seen_keys:
                continue
            # Canonicalization/replacement can select a key from an older row;
            # run the privacy boundary again on the exact final persisted data.
            if contains_sensitive_memory(key) or contains_sensitive_memory(supported_value) \
                    or contains_sensitive_memory(evidence):
                continue
            result_item = {"category": category, "key": key, "value": supported_value, "evidence_span": supported_value}
            if replacement:
                result_item.update(replaces_memory_id=replacement["id"], replaces_value=replacement["value"])
            clean.append(result_item)
            seen_values.add((category, supported_value))
            seen_keys.add((category, key))
            if len(clean) == 3:
                break
    return clean
