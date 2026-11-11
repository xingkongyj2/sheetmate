"""长期记忆的分类、预算与执行证据约束。"""
from __future__ import annotations

from typing import Any

CATEGORIES = ["preferences", "domain_knowledge", "user_context", "history_insights"]
MEMORY_MESSAGE_CHARS = 8_000
MEMORY_ANSWER_CHARS = 2_000
MEMORY_CONTEXT_CHARS = 12_000
MEMORY_EXTRACTION_TOKENS = 2_400
REPAIR_EXPERIENCE_LIMIT = 3
REPAIR_EXPERIENCE_CHARS = 2_200
REPAIR_FAILURE_KINDS = frozenset({"missing_column", "missing_table", "date_parse", "type_mismatch", "merged_cell"})


class MemoryContextLimit(ValueError):
    """Required human rules cannot fit without losing constraints."""


CATEGORY_LABELS = {
    "preferences": "偏好",
    "domain_knowledge": "业务规则",
    "user_context": "背景",
    "history_insights": "经验",
}


def is_verified_repair_memory(item: dict[str, Any]) -> bool:
    """Recognize executor-backed technical experience, never chat extraction."""
    evidence = item.get("execution_evidence")
    if (item.get("category") != "history_insights" or item.get("source") != "ai"
            or not isinstance(evidence, dict)):
        return False
    failed, repaired = evidence.get("failed_evidence_id"), evidence.get("repaired_evidence_id")
    return bool(
        evidence.get("kind") == "verified_repair" and evidence.get("verified") is True
        and evidence.get("failure_kind") in REPAIR_FAILURE_KINDS
        and isinstance(evidence.get("execution_id"), str) and evidence["execution_id"]
        and isinstance(failed, str) and failed
        and isinstance(repaired, str) and repaired and failed != repaired
        and evidence.get("verification") in {"completion_check", "python_checks", "recipe_verification"}
    )
