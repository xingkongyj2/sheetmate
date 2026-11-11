"""Long-term recall, inspection and prompt projections over scoped repositories."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from sheetmate.agent.memory.long_term.formatting import format_memories
from sheetmate.agent.memory.long_term.policy import (
    MEMORY_CONTEXT_CHARS,
    REPAIR_EXPERIENCE_CHARS,
    REPAIR_EXPERIENCE_LIMIT,
    MemoryContextLimit,
    is_verified_repair_memory,
)
from sheetmate.agent.memory.long_term.retrieval.selector import retrieve_memories, search_memory_candidates
from sheetmate.core.errors import ServiceError
from sheetmate.services.memory.repository import (
    set_memory,
    memories_for,
    customer_memories,
    delete_memory,
    move_conversation_memories,
)
from sheetmate.services.memory.serialization import memory_dict, memory_write_result
from sheetmate.services.memory.field_mappings import (
    field_mapping_entries,
    field_mapping_for,
    upsert_field_mapping,
    replace_field_mappings,
    migrate_customer_context,
)


def repair_experience_text(db: Session, *, user_id: int, customer_id: str | None,
                           conversation_id: str | None, recipe_id: str | None = None,
                           query: str) -> str:
    """Recall a few verified repairs only within the exact current work scope."""
    if not query.strip() or not (customer_id or conversation_id):
        return ""
    items = memories_for(db, user_id=user_id, customer_id=customer_id,
                         conversation_id=conversation_id, recipe_id=recipe_id)
    scoped = []
    for item in items:
        if not is_verified_repair_memory(item) or item.get("recipe_id") != recipe_id:
            continue
        if customer_id:
            in_scope = item.get("customer_id") == customer_id and not item.get("conversation_id")
        else:
            in_scope = not item.get("customer_id") and item.get("conversation_id") == conversation_id
        if in_scope:
            scoped.append(item)
    selected = retrieve_memories(scoped, query, limit=REPAIR_EXPERIENCE_LIMIT,
                                 max_chars=REPAIR_EXPERIENCE_CHARS)["items"]
    return format_memories(selected)


def customer_visible_memories(db: Session, customer_id: str, *, recipe_id: str | None = None) -> list[dict[str, Any]]:
    """Customer UI: user-maintained rules and inspectable verified repairs."""
    return [item for item in customer_memories(db, customer_id, recipe_id=recipe_id)
            if item.get("source") == "human" or is_verified_repair_memory(item)]


def recall_memories(db: Session, *, user_id: int, customer_id: str | None, conversation_id: str | None,
                    query: str, recipe_id: str | None = None, limit: int = 30,
                    max_chars: int = MEMORY_CONTEXT_CHARS) -> dict[str, Any]:
    """Filter access scope before the configured scorer ranks candidates."""
    items = memories_for(db, user_id=user_id, customer_id=customer_id, conversation_id=conversation_id,
                         recipe_id=recipe_id)
    try:
        result = retrieve_memories(items, query, limit=limit, max_chars=max_chars)
    except MemoryContextLimit as exc:
        raise ServiceError(str(exc)) from exc
    result["metadata"]["scope_filter"] = "user_customer_conversation_recipe_before_ranking"
    return result


def inspect_memories(db: Session, *, user_id: int, customer_id: str | None, conversation_id: str | None,
                     query: str = "", memory_id: int | None = None, recipe_id: str | None = None,
                     offset: int = 0, limit: int | None = None) -> dict[str, Any]:
    """Browse or page original memory values after the normal scope filter."""
    items = memories_for(db, user_id=user_id, customer_id=customer_id,
                         conversation_id=conversation_id, recipe_id=recipe_id)
    offset = max(0, int(offset))
    if memory_id is not None:
        item = next((entry for entry in items if entry.get("id") == memory_id), None)
        if item is None:
            return {"found": False, "memory_id": memory_id}
        page_chars = max(100, min(int(limit or 4000), 8000))
        value = str(item.get("value") or "")
        end = min(len(value), offset + page_chars)
        return {"found": True, "memory_id": item["id"], "version": item.get("version"),
                "category": item["category"], "key": item["key"], "scope": item.get("scope_label"),
                "source": item.get("source"), "source_message_id": item.get("source_message_id"),
                "source_turn_id": item.get("source_turn_id"),
                "value": value[offset:end], "offset": offset, "offset_unit": "characters",
                "total_characters": len(value), "next_offset": end if end < len(value) else None,
                "complete": offset == 0 and end == len(value)}
    if len(str(query or "")) > 200:
        raise ServiceError("记忆检索关键词最多 200 字符，请缩小关键词后重试")
    if offset > 100_000:
        raise ServiceError("记忆检索翻页范围过大，请缩小检索范围")
    candidates = search_memory_candidates(items, str(query or "").strip())
    page_limit = max(1, min(int(limit or 8), 12))
    page = candidates[offset:offset + page_limit]
    matches = []
    for item in page:
        value = str(item.get("value") or "")
        match_at = value.lower().find(str(query or "").strip().lower()) if query else -1
        start = max(0, match_at - 80) if match_at >= 0 else 0
        excerpt = value[start:start + 320]
        matches.append({"memory_id": item.get("id"), "version": item.get("version"),
                        "category": item.get("category"), "key": item.get("key"),
                        "scope": item.get("scope_label"), "source": item.get("source"),
                        "source_message_id": item.get("source_message_id"),
                        "updated_at": item.get("updated_at"), "lexical_score": item.get("lexical_score"),
                        "characters": len(value), "excerpt": excerpt,
                        "excerpt_offset": start,
                        "excerpt_truncated": start > 0 or len(value) > start + len(excerpt)})
    next_offset = offset + len(page)
    return {"source": "scoped_memory", "matches": matches, "query": str(query or "").strip(),
            "offset": offset, "offset_unit": "memories", "total_matches": len(candidates),
            "next_offset": next_offset if next_offset < len(candidates) else None,
            "omitted_matches": max(0, len(candidates) - next_offset),
            "scope_filter": "user_customer_conversation_recipe_before_search",
            "instruction": "先按当前用户、客户、会话和流程范围过滤，再做词项检索。摘录仅供定位；需要完整原文时按 memory_id 分页读取。不同版本和作用域是独立记录。"}


def memory_text(db: Session, *, user_id: int, customer_id: str | None, conversation_id: str | None,
                query: str, recipe_id: str | None = None, max_chars: int = MEMORY_CONTEXT_CHARS) -> str:
    # Field mappings are applicable rules too. Reserve their complete rendered
    # cost before ranking AI context; never silently truncate a human mapping.
    mapping = field_mapping_for(db, customer_id, recipe_id)
    mapping_text = format_memories([], mapping)
    reserved = len(mapping_text) + (1 if mapping_text else 0) + 180
    if len(mapping_text) > max_chars:
        raise ServiceError("当前记忆与字段映射超出上下文预算，请精简重复映射或缩小流程范围；本轮未截断人工规则。")
    result = recall_memories(db, user_id=user_id, customer_id=customer_id, conversation_id=conversation_id,
                             recipe_id=recipe_id, query=query, max_chars=max(0, max_chars - reserved))
    text = format_memories(result["items"], mapping)
    metadata = result["metadata"]
    if metadata["available_count"]:
        method = "词项相关性+MMR+近期偏好" if metadata["method"] == "lexical_mmr_recent" else metadata["method"]
        text += (f"\n（记忆召回：{method}；已选 {metadata['selected_count']}/{metadata['available_count']} 条，"
                 f"同域原文重复 {metadata['duplicate_count']} 条；省略不代表不存在，需要时调用 inspect_saved_memories 按 ID 或关键词检索原文。）")
    if len(text) > max_chars:
        raise ServiceError("当前记忆与字段映射超出上下文预算；本轮未截断人工规则，请缩小范围。")
    return text
