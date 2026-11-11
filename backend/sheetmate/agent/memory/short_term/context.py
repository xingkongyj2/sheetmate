"""Source-aware, stage-specific prompt projections over durable source data.

Required instructions and business constraints are never truncated. Reference
material can be omitted with an explicit source/retrieval notice; no projection
writes back into messages, memories, tables or evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sheetmate.agent.memory.short_term.errors import ContextOverflow

from sheetmate.agent.memory.short_term.tokens import estimate_tokens, fit_text, history_tokens
from sheetmate.core.observability.conversation_log import record_event


@dataclass(frozen=True)
class ContextBlock:
    source: str
    text: str
    required: bool = False
    priority: int = 0
    scope: str = "conversation"
    references: tuple[str, ...] = ()
    retrieve: str = ""


SYSTEM_TOKEN_BUDGETS = {"execution": 22_000, "planning": 16_000, "specialist": 16_000, "chat": 10_000}
HISTORY_TOKEN_BUDGETS = {"execution": 12_000, "planning": 8_000, "specialist": 8_000,
                        "chat": 8_000, "request_intent": 8_000, "recipe_persistence": 8_000}


def assemble_context(blocks: list[ContextBlock], *, stage: str = "execution",
                     token_budget: int | None = None) -> str:
    budget = SYSTEM_TOKEN_BUDGETS.get(stage, SYSTEM_TOKEN_BUDGETS["execution"]) if token_budget is None else token_budget
    available = max(0, budget)
    rendered: dict[int, str] = {}
    metrics = []
    # Mandatory material is admitted first even when an optional block appears
    # earlier in the desired presentation order.
    order = sorted(range(len(blocks)), key=lambda i: (not blocks[i].required, -blocks[i].priority, i))
    for index in order:
        block = blocks[index]
        if not block.text.strip():
            continue
        refs = ",".join(block.references[:20])
        label = f"[context source={block.source} scope={block.scope}" + (f" refs={refs}" if refs else "") + "]"
        full = f"{label}\n{block.text}"
        size = estimate_tokens(full) + 2
        selected = full
        if size > available:
            if block.required:
                raise ContextOverflow(f"当前{stage}阶段的必要规则超出上下文预算，无法在不丢失约束的情况下继续；请缩小处理范围。")
            notice = f"{label}\n[参考内容未完整注入；原始依据仍保留。{block.retrieve or '需要具体细节时先读取对应原始来源。'}]"
            # Select complete lines only. Truncating a JSON object or a numeric
            # row midway can change its meaning.
            lines = []
            used = estimate_tokens(notice) + 4
            for line in block.text.splitlines():
                cost = estimate_tokens(line) + 1
                if used + cost > available:
                    break
                lines.append(line)
                used += cost
            selected = label + "\n" + "\n".join(lines) + "\n" + notice.split("\n", 1)[1]
            if estimate_tokens(selected) + 2 > available:
                selected = ""
        if selected:
            rendered[index] = selected
            available -= estimate_tokens(selected) + 2
        metrics.append({"block_index": index, "source": block.source, "scope": block.scope, "required": block.required,
                        "input_tokens_estimate": size,
                        "selected_tokens_estimate": estimate_tokens(selected) + 2 if selected else 0,
                        "projected": selected != full, "reference_count": len(block.references)})
    # Reconcile the receipt after any eviction: otherwise a previously fitted
    # source could disappear without being listed among the omitted sources.
    while True:
        for item in metrics:
            index = item["block_index"]
            if index not in rendered:
                item.update(selected_tokens_estimate=0, projected=True)
        omitted = [item["source"] for item in metrics if item["projected"]]
        text = "\n\n".join(rendered[i] for i in sorted(rendered))
        notice = ("\n\n[部分参考来源因阶段预算未完整展示：" + ", ".join(omitted)
                  + "；不能据此推断源信息不存在，具体数据须按来源回查。]") if omitted else ""
        if estimate_tokens(text + notice) <= budget:
            text += notice
            break
        optional = [i for i in rendered if not blocks[i].required]
        if not optional:
            raise ContextOverflow("必要上下文及来源省略提示超出阶段预算，请缩小处理范围。")
        del rendered[min(optional, key=lambda i: (blocks[i].priority, -i))]
    record_event("context.assembled", "sheetmate.agent.memory.short_term.context.assemble_context",
                 {"stage": stage, "budget_tokens_estimate": budget,
                  "selected_tokens_estimate": estimate_tokens(text), "sources": metrics})
    return text


def stage_history(history: list[dict[str, Any]], stage: str, *, token_budget: int | None = None) -> list[dict[str, Any]]:
    """Keep primers, summary and recents within a shared stage policy.

    Returned objects are request-local copies. Image parts follow only selected
    messages; omitted history can be recovered by source message ID.
    """
    budget = HISTORY_TOKEN_BUDGETS.get(stage, 8_000) if token_budget is None else token_budget
    source_history = history
    # Shared user goals cross the branch boundary; global assistant answers and
    # their summaries can contain conclusions about another specialist's table.
    history = [item for item in history if item.get("role") == "user"] if stage == "specialist" else history
    user_indexes = [i for i, item in enumerate(history) if item.get("role") == "user"]
    image_indexes = [i for i, item in enumerate(history) if _image_count(item)]
    # Current-turn images are supplied separately; only the latest historical
    # bundle is retained. Its captions travel with its images.
    if _history_tokens(history) <= budget and len(image_indexes) <= 1:
        return [dict(item) for item in history]
    base_notice = ("[专员历史窗口：只共享用户目标，部分原文或历史图片未注入；仅用分配的表核验事实，缺少其他表依据时向协调器说明。]"
                   if stage == "specialist" else
                   "[阶段历史窗口：部分原文或历史图片未注入；摘要只作参考。旧决定/数值用 inspect_conversation_history 按消息 ID 或关键词回查；图片按原文件查阅。旧回答不作本轮执行证据。]")
    notice = {"role": "assistant", "content": base_notice, "context_summary": True, "context_limited": True}
    remaining = budget - _history_tokens([notice]) - 64  # room for omitted-source IDs in the final receipt
    if remaining < 100:
        raise ContextOverflow("阶段历史预算不足以保留来源提示和用户目标，请增加预算或缩小范围。")
    picked: dict[int, dict[str, Any]] = {}
    anchors = list(dict.fromkeys([*user_indexes[:1], *user_indexes[-1:]]))
    # Reserve both user endpoints before summaries, primers, images or assistant
    # narration. A fixed minimum per all anchors could skip every user turn in
    # small windows and then admit a large trailing assistant answer instead.
    if anchors:
        per_anchor = remaining // len(anchors)
        required = {}
        for index in anchors:
            item = dict(history[index])
            item.pop("image_parts", None)
            overhead = _history_tokens([{**item, "content": ""}])
            cap = 2_000 if index == user_indexes[-1] else 1_000
            allowance = min(cap, max(0, per_anchor - overhead))
            item["content"] = fit_text(str(item.get("content") or ""), allowance)
            required[index] = item
        required_cost = _history_tokens(list(required.values()))
        if required_cost > remaining:
            raise ContextOverflow("阶段历史预算不足以保留首条和最新用户要求，请增加预算或缩小范围。")
        picked.update(required)
        remaining -= required_cost
    secondary = list(dict.fromkeys([
        *[i for i, item in enumerate(history) if item.get("context_primer")],
        *[i for i, item in enumerate(history) if item.get("context_summary")],
        *user_indexes[-2:-1],
    ]))
    secondary = [i for i in secondary if i not in picked]
    for position, index in enumerate(secondary):
        allowance = min(1_000, max(0, remaining // (len(secondary) - position)))
        if allowance < 40:
            continue
        item = dict(history[index])
        item.pop("image_parts", None)
        overhead = _history_tokens([{**item, "content": ""}])
        item["content"] = fit_text(str(item.get("content") or ""), max(0, allowance - overhead))
        needed = _history_tokens([item])
        if needed <= remaining:
            picked[index] = item
            remaining -= needed
    # Images have a separate conservative allowance, matching the outgoing
    # request estimator. Select the newest historical bundle only.
    if image_indexes:
        index = image_indexes[-1]
        cost = _image_count(history[index]) * 4096 + _text_part_tokens(history[index])
        if index not in picked and cost + 200 <= remaining:
            item = dict(history[index])
            item.pop("image_parts", None)
            text_allowance = min(1000, remaining - cost - _history_tokens([{**item, "content": ""}]))
            item["content"] = fit_text(str(item.get("content") or ""), text_allowance)
            picked[index] = item
            remaining -= _history_tokens([item])
        if index in picked and cost <= remaining:
            picked[index]["image_parts"] = list(history[index].get("image_parts") or [])
            remaining -= cost
    # Fill remaining room with recent history; do not let one large assistant
    # turn displace already reserved user constraints.
    for index in reversed(range(len(history))):
        if index in picked or remaining < 100:
            continue
        item = dict(history[index])
        item.pop("image_parts", None)
        allowance = remaining - 8
        item["content"] = fit_text(str(item.get("content") or ""), allowance)
        picked[index] = item
        remaining -= _history_tokens([item])
    omitted = [item for i, item in enumerate(history) if i not in picked]
    omitted_ids = [item.get("message_id", item.get("id")) for item in omitted
                   if item.get("message_id", item.get("id")) is not None]
    receipt = ("\n省略来源消息 ID：" + ", ".join(str(value) for value in omitted_ids[:8])
               + (f" 等（共 {len(omitted_ids)} 条）" if len(omitted_ids) > 8 else "")
               + "。需要原文时按 ID 回查。") if omitted_ids else ""
    notice["content"] = base_notice + receipt
    selected = [notice, *(picked[i] for i in sorted(picked))]
    while _history_tokens(selected) > budget:
        removable = [i for i in picked if i not in anchors]
        if removable:
            picked.pop(min(removable))
        else:
            # Shorten optional text from the selected history before removing
            # a user/summary anchor; full source remains available by ID.
            candidates = [i for i in anchors if i in picked and len(picked[i].get("content", "")) > 160]
            if not candidates:
                raise ContextOverflow("阶段历史及来源 ID 提示超出预算，请缩小范围或增加预算。")
            index = candidates[-1]
            original = str(picked[index].get("content") or "")
            projected = fit_text(original, max(80, estimate_tokens(original) - (_history_tokens(selected) - budget) - 8))
            if projected == original:
                raise ContextOverflow("阶段历史及来源 ID 提示超出预算，请缩小范围或增加预算。")
            picked[index]["content"] = projected
        omitted = [item for i, item in enumerate(history) if i not in picked]
        omitted_ids = [item.get("message_id", item.get("id")) for item in omitted
                       if item.get("message_id", item.get("id")) is not None]
        receipt = ("\n省略来源消息 ID：" + ", ".join(str(value) for value in omitted_ids[:8])
                   + (f" 等（共 {len(omitted_ids)} 条）" if len(omitted_ids) > 8 else "")
                   + "。需要原文时按 ID 回查。") if omitted_ids else ""
        notice["content"] = base_notice + receipt
        selected = [notice, *(picked[i] for i in sorted(picked))]
    record_event("context.history_selected", "sheetmate.agent.memory.short_term.context.stage_history",
                 {"stage": stage, "input_messages": len(history), "selected_messages": len(picked),
                  "input_tokens_estimate": _history_tokens(source_history), "selected_tokens_estimate": _history_tokens(selected),
                  "isolated_messages": len(source_history) - len(history),
                  "input_images": sum(len(item.get("image_parts") or []) for item in source_history),
                  "selected_images": sum(len(item.get("image_parts") or []) for item in selected),
                  "source_message_ids": [item.get("message_id", item.get("id")) for item in selected]})
    return selected


def _history_tokens(history: list[dict[str, Any]]) -> int:
    source_labels = sum(
        estimate_tokens(f"[history source=conversation_message message_id={item.get('message_id', item.get('id'))}]\n")
        for item in history if item.get("message_id", item.get("id")) is not None
    )
    return history_tokens(history) + source_labels + sum(
        _image_count(item) * 4096 + _text_part_tokens(item) for item in history)


def _image_count(item: dict[str, Any]) -> int:
    return sum(1 for part in item.get("image_parts") or [] if isinstance(part, dict)
               and part.get("type") in {"image_url", "input_image"})


def _text_part_tokens(item: dict[str, Any]) -> int:
    return sum(estimate_tokens(str(part.get("text") or "")) for part in item.get("image_parts") or []
               if isinstance(part, dict) and part.get("type") == "text")
