"""在已授权候选集上执行召回，评分后端不能绕过人工规则和预算。"""
from __future__ import annotations

import re
from typing import Any

from sheetmate.agent.memory.long_term.formatting import format_memories
from sheetmate.agent.memory.long_term.policy import (
    MEMORY_CONTEXT_CHARS,
    REPAIR_EXPERIENCE_CHARS,
    REPAIR_EXPERIENCE_LIMIT,
    REPAIR_FAILURE_KINDS,
    MemoryContextLimit,
    is_verified_repair_memory,
)
from sheetmate.agent.memory.long_term.retrieval.protocol import MemoryScorer
from sheetmate.agent.memory.long_term.retrieval.provider import get_memory_scorer


def select_relevant(items: list[dict[str, Any]], query: str, limit: int = 30,
                    max_chars: int = MEMORY_CONTEXT_CHARS) -> list[dict[str, Any]]:
    """Return selected items; scope filtering remains the caller's responsibility."""
    return retrieve_memories(items, query, limit=limit, max_chars=max_chars)["items"]


def _memory_identity(item: dict[str, Any]) -> tuple[Any, ...]:
    # Exact wording only: different numbers, negations, conditions and scopes
    # remain independent facts. Never merge merely similar business rules.
    value = str(item.get("value") or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    scope = tuple(item.get(key) for key in ("user_id", "customer_id", "conversation_id", "recipe_id"))
    if not any(part is not None for part in scope):
        scope += (item.get("scope"), item.get("scope_label"))
    return (*scope, item.get("category"), value)


def retrieve_memories(items: list[dict[str, Any]], query: str, limit: int = 30,
                      max_chars: int = MEMORY_CONTEXT_CHARS, *,
                      scorer: MemoryScorer | None = None) -> dict[str, Any]:
    """Select already-scoped memories using an injectable scorer and MMR.

    The default scorer uses lexical terms, without an embedding model. Service
    order is newest first. Every human rule survives or raises explicitly. Only
    same-scope, exact-value duplicates are removed, regardless of scorer.
    """
    scorer = scorer if scorer is not None else get_memory_scorer()
    queried_failures = set(re.findall(r"[a-z0-9_]+", str(query or "").lower())) & REPAIR_FAILURE_KINDS
    scored = [(index, item, scorer.relevance(item, query)) for index, item in enumerate(items)]
    human = sorted((row for row in scored if row[1].get("source") == "human"),
                   key=lambda row: (-row[2], row[0]))
    chosen = [item for _, item, _ in human]
    selection = [{"memory_id": item.get("id"), "reason": "human_required", "lexical_score": score}
                 for _, item, score in human]
    if len(format_memories(chosen)) > max_chars:
        raise MemoryContextLimit(
            f"当前适用的 {len(human)} 条人工记忆超出上下文预算（{max_chars} 字符）。"
            "请精简重复规则或缩小流程范围后继续；本轮未截断或忽略人工规则。"
        )

    seen = {_memory_identity(item) for _, item, _ in human}
    candidates = []
    duplicate_count = unrelated_count = unverified_experience_count = 0
    for index, item, score in scored:
        if item.get("source") == "human":
            continue
        if item.get("category") == "history_insights" and not is_verified_repair_memory(item):
            # Legacy experience stays browsable, but a model's old guess must
            # not become an automatically applied repair or business rule.
            unverified_experience_count += 1
            continue
        if (item.get("category") == "history_insights" and queried_failures
                and item["execution_evidence"]["failure_kind"] not in queried_failures):
            unrelated_count += 1
            continue
        identity = _memory_identity(item)
        if identity in seen:
            duplicate_count += 1
            continue
        seen.add(identity)
        if score or item.get("category") in {"preferences", "user_context"}:
            candidates.append((index, item, score))
        else:
            unrelated_count += 1

    ai_limit = max(0, limit)
    relevant = sorted((row for row in candidates if row[2]), key=lambda row: (-row[2], row[0]))
    # Generic business rules/experience are not standing context. Keep only a
    # small recent baseline of preferences and background beside query matches.
    standing = []
    for category in ("preferences", "user_context"):
        standing.extend(row for row in candidates if not row[2] and row[1].get("category") == category)
    standing = sorted(standing, key=lambda row: row[0])
    pool = relevant[:ai_limit * 3] + standing[:min(10, ai_limit)] if ai_limit else []
    highest = max((row[2] for row in pool), default=1) or 1

    def rank(row: tuple[int, dict[str, Any], float]) -> tuple[bool, float, int]:
        index, item, score = row
        similarity = max((scorer.similarity(item, old) for old in chosen), default=0)
        mmr = 0.7 * score / highest - 0.3 * similarity + 0.05 / (index + 1)
        return bool(score), mmr, -index

    experiences = []
    while pool and len(chosen) - len(human) < ai_limit:
        row = max(pool, key=rank)
        pool.remove(row)
        index, item, score = row
        is_experience = item.get("category") == "history_insights"
        if is_experience and (len(experiences) >= REPAIR_EXPERIENCE_LIMIT
                              or len(format_memories([*experiences, item])) > REPAIR_EXPERIENCE_CHARS):
            continue
        if len(format_memories([*chosen, item])) > max_chars:
            continue
        chosen.append(item)
        if is_experience:
            experiences.append(item)
        selection.append({"memory_id": item.get("id"), "reason": "lexical_match" if score else "recent_standing",
                          "lexical_score": score})

    selected_ids = [item["id"] for item in chosen if item.get("id") is not None]
    return {"items": chosen, "metadata": {
        "method": scorer.method, "embedding": scorer.uses_embeddings, "mmr_lambda": 0.7,
        "scope_filter": "caller_required_before_ranking",
        "available_count": len(items), "candidate_count": len(candidates),
        "selected_count": len(chosen), "required_human_count": len(human),
        "duplicate_count": duplicate_count, "unrelated_count": unrelated_count,
        "unverified_experience_count": unverified_experience_count,
        "selected_experience_count": len(experiences),
        "experience_limit": REPAIR_EXPERIENCE_LIMIT, "experience_max_chars": REPAIR_EXPERIENCE_CHARS,
        "omitted_count": len(items) - len(chosen), "selected_memory_ids": selected_ids,
        "selection": selection,
        "max_chars": max_chars, "used_chars": len(format_memories(chosen)),
        "instruction": "召回结果是范围内的参考子集；省略项仍保留，可按记忆 ID 或关键词按需检索原文。",
    }}


def search_memory_candidates(items: list[dict[str, Any]], query: str = "", *,
                             scorer: MemoryScorer | None = None) -> list[dict[str, Any]]:
    """Return ranked scoped matches for explicit memory inspection."""
    scorer = scorer if scorer is not None else get_memory_scorer()
    ranked = [(index, item, scorer.relevance(item, query))
              for index, item in enumerate(items)]
    if query.strip():
        ranked = [row for row in ranked if row[2] > 0]
    ranked.sort(key=lambda row: (-row[2], row[0]))
    return [{**item, "lexical_score": score} for _, item, score in ranked]
