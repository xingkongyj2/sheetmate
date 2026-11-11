"""Memory extension boundaries preserve required rules and scoped provenance."""
from __future__ import annotations

from copy import deepcopy

import pytest

from sheetmate.agent.memory.long_term.formatting import format_memories
from sheetmate.agent.memory.long_term.policy import MemoryContextLimit
from sheetmate.agent.memory.long_term.retrieval.provider import get_memory_scorer, set_memory_scorer
from sheetmate.agent.memory.long_term.retrieval.selector import retrieve_memories, search_memory_candidates


def memory(identifier, value, *, source="ai", category="domain_knowledge", **extra):
    return {"id": identifier, "version": 1, "source": source, "category": category,
            "key": f"规则{identifier}", "value": value, "customer_id": "customer",
            "conversation_id": None, "recipe_id": "recipe", **extra}


class StubSemanticScorer:
    method = "test_semantic_mmr_recent"
    uses_embeddings = True

    def relevance(self, item, query):
        # No shared lexical terms. Selection can depend on an external scorer.
        return {1: 0, 2: 10, 3: 9, 4: 8, 5: 100, 6: 100}.get(item["id"], 0)

    def similarity(self, left, right):
        return 0


def test_custom_scorer_cannot_bypass_human_rules_evidence_or_exact_deduplication():
    required = memory(1, "人工金额口径不得改写", source="human")
    selected = memory(2, "红色标记需要检查")
    same_scope_duplicate = memory(3, selected["value"])
    other_scope_record = memory(4, selected["value"], recipe_id="other_recipe")
    unverified_experience = memory(5, "未经程序验证的推断", category="history_insights")
    oversized = memory(6, "很长的参考资料" * 1000)
    items = [required, selected, same_scope_duplicate, other_scope_record, unverified_experience, oversized]
    before = deepcopy(items)

    result = retrieve_memories(items, "no-overlapping-terms", limit=3, max_chars=1500,
                               scorer=StubSemanticScorer())

    assert [item["id"] for item in result["items"]] == [1, 2, 4]
    assert result["metadata"]["method"] == StubSemanticScorer.method
    assert result["metadata"]["embedding"] is True
    assert result["metadata"]["duplicate_count"] == 1
    assert result["metadata"]["unverified_experience_count"] == 1
    assert len(format_memories(result["items"])) <= 1500
    assert items == before


def test_custom_scorer_cannot_silently_truncate_required_human_rules():
    items = [memory(1, "必须保留的人工完整规则" * 100, source="human")]
    with pytest.raises(MemoryContextLimit):
        retrieve_memories(items, "query", max_chars=100, scorer=StubSemanticScorer())


def test_process_scorer_configuration_is_used_by_recall_and_inspection():
    previous = get_memory_scorer()
    scorer = StubSemanticScorer()
    items = [memory(1, "无匹配词项"), memory(2, "另一条无匹配词项")]
    try:
        set_memory_scorer(scorer)
        assert get_memory_scorer() is scorer
        assert [item["id"] for item in retrieve_memories(items, "query")["items"]] == [2]
        assert [item["id"] for item in search_memory_candidates(items, "query")] == [2]
    finally:
        set_memory_scorer(previous)
    assert get_memory_scorer() is previous


def test_repository_scope_filter_runs_before_injected_scorer():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from sheetmate.db.session import Base
    from sheetmate.services.memory.long_term import recall_memories
    from sheetmate.services.memory.repository import set_memory

    class RecordingScorer:
        method = "test_scoped_scorer"
        uses_embeddings = True

        def __init__(self):
            self.seen = []

        def relevance(self, item, query):
            self.seen.append(item["id"])
            return 1

        def similarity(self, left, right):
            return 0

    engine = create_engine("sqlite://")
    scorer = RecordingScorer()
    previous = get_memory_scorer()
    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as db:
            matching = set_memory(db, category="domain_knowledge", key="本流程规则", value="只统计已签收订单",
                                  customer_id="customer", recipe_id="recipe", source="human")
            personal = set_memory(db, category="preferences", key="个人偏好", value="使用中文回答",
                                  user_id=1, source="human")
            unrelated = set_memory(db, category="domain_knowledge", key="其他流程规则", value="其他计算口径",
                                   customer_id="customer", recipe_id="other_recipe", source="human")
            other_user = set_memory(db, category="preferences", key="他人偏好", value="使用另一种语言",
                                    user_id=2, source="human")
            set_memory_scorer(scorer)
            result = recall_memories(db, user_id=1, customer_id="customer", conversation_id=None,
                                     recipe_id="recipe", query="订单")
            assert set(scorer.seen) == {matching.id, personal.id}
            assert {item["id"] for item in result["items"]} == {matching.id, personal.id}
            assert unrelated.id not in scorer.seen and other_user.id not in scorer.seen
            assert result["metadata"]["scope_filter"] == "user_customer_conversation_recipe_before_ranking"
    finally:
        set_memory_scorer(previous)
        engine.dispose()
