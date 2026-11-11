"""默认词项评分后端：中文 n-gram、英文词项与 Jaccard 多样性。"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any


def _chunks(text: str) -> list[str]:
    text = str(text)
    return [text[i : i + 2] for i in range(max(0, len(text) - 1))]


@lru_cache(maxsize=2048)
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


class LexicalMemoryScorer:
    """Pure default backend; no database, model client or service dependencies."""

    method = "lexical_mmr_recent"
    uses_embeddings = False

    def relevance(self, item: dict[str, Any], query: str) -> float:
        return _memory_relevance(item, _query_terms(query), query)

    def similarity(self, left: dict[str, Any], right: dict[str, Any]) -> float:
        first = _query_terms(str(left.get("key") or "") + " " + str(left.get("value") or ""))
        second = _query_terms(str(right.get("key") or "") + " " + str(right.get("value") or ""))
        combined = first | second
        return len(first & second) / len(combined) if combined else 0
