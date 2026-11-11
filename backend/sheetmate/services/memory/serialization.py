"""Bounded public memory values, provenance and persistence receipts."""
from __future__ import annotations

from typing import Any

from sheetmate.agent.memory.long_term.policy import CATEGORY_LABELS
from sheetmate.db.models import Memory


def memory_dict(m: Memory) -> dict[str, Any]:
    scope = "流程" if m.recipe_id else "客户" if m.customer_id else "会话" if m.conversation_id else "个人"
    return {"id": m.id, "category": m.category, "category_label": CATEGORY_LABELS.get(m.category, m.category),
            "key": m.key, "value": m.value, "recipe_id": m.recipe_id, "scope_label": scope,
            "scope": "customer" if m.customer_id else "conversation" if m.conversation_id else "personal",
            "user_id": m.user_id, "customer_id": m.customer_id, "conversation_id": m.conversation_id,
            "source": m.source, "source_message_id": m.source_message_id,
            "source_turn_id": m.source_turn_id, "evidence_span": m.evidence_span or "", "version": m.version or 1,
            "execution_evidence": _execution_evidence_summary(getattr(m, "execution_evidence", None)),
            "updated_at": m.updated_at.isoformat()}


def _execution_evidence_summary(value: Any) -> dict[str, Any]:
    """Expose bounded provenance identifiers, without arguments or result data."""
    if not isinstance(value, dict):
        return {}
    summary = {}
    for key in ("kind", "failure_kind", "tool", "conversation_id", "execution_id", "failed_call_id",
                "failed_evidence_id", "repaired_call_id", "repaired_evidence_id", "completion_check_id",
                "verification"):
        part = value.get(key)
        if isinstance(part, str) and part:
            summary[key] = part[:160]
    if isinstance(value.get("verified"), bool):
        summary["verified"] = value["verified"]
    for key in ("request_message_id", "assistant_message_id"):
        part = value.get(key)
        if isinstance(part, int) and not isinstance(part, bool) and part > 0:
            summary[key] = part
    versions = []
    for item in value.get("source_versions", [])[:6] if isinstance(value.get("source_versions"), list) else []:
        if not isinstance(item, dict):
            continue
        version = {}
        for key in ("table", "revision", "file_id", "file_version"):
            part = item.get(key)
            if isinstance(part, str) and part:
                version[key] = part[:160]
            elif isinstance(part, int) and not isinstance(part, bool):
                version[key] = part
        if version:
            versions.append(version)
    if versions:
        summary["source_versions"] = versions
    return summary


def memory_write_result(item: Memory) -> dict[str, Any]:
    """Return the actual persistence outcome attached by set_memory."""
    return {
        "status": getattr(item, "_write_status", "updated"),
        "reason": getattr(item, "_write_reason", ""),
        "memory_id": item.id,
        "category": item.category,
        "key": item.key,
        "value": getattr(item, "_write_actual_value", item.value),
        "source": getattr(item, "_write_actual_source", item.source),
        "version": item.version or 1,
        "source_message_id": item.source_message_id,
        "source_turn_id": item.source_turn_id,
        "evidence_span": item.evidence_span or "",
    }
