"""Scoring boundary for lexical or future vector-backed memory retrieval.

Implementations receive only candidates filtered by the caller's access scope.
Scores must be finite and nonnegative; similarity is in [0, 1]. Retrieval policy
continues to enforce human rules, evidence, exact deduplication and budgets.
"""
from __future__ import annotations

from typing import Any, Protocol


class MemoryScorer(Protocol):
    method: str
    uses_embeddings: bool

    def relevance(self, item: dict[str, Any], query: str) -> float: ...

    def similarity(self, left: dict[str, Any], right: dict[str, Any]) -> float: ...
