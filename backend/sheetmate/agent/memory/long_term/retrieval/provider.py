"""Default retrieval adapter selected once at application composition time.

A future vector scorer can be installed here without importing vector SDKs in
Agent execution, tools or application services. Tests/callers may also pass a
scorer directly to selector functions, keeping individual requests independent.
"""
from __future__ import annotations

from sheetmate.agent.memory.long_term.retrieval.lexical import LexicalMemoryScorer
from sheetmate.agent.memory.long_term.retrieval.protocol import MemoryScorer

_default_scorer: MemoryScorer = LexicalMemoryScorer()


def get_memory_scorer() -> MemoryScorer:
    return _default_scorer


def set_memory_scorer(scorer: MemoryScorer | None) -> None:
    """Configure the process adapter; None restores the lexical default."""
    global _default_scorer
    _default_scorer = scorer if scorer is not None else LexicalMemoryScorer()
