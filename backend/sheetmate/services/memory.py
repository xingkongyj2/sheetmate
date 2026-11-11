"""记忆与字段映射：流程隔离，人工维护的规则优先于 AI 参考。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, insert, literal, or_, select, update
from sqlalchemy.orm import Session

from sheetmate.agent.memory import (
    CATEGORIES, CATEGORY_LABELS, MEMORY_CONTEXT_CHARS, REPAIR_EXPERIENCE_CHARS,
    REPAIR_EXPERIENCE_LIMIT, MemoryContextLimit, format_memories, is_verified_repair_memory,
    retrieve_memories, search_memory_candidates,
)
from sheetmate.agent.privacy import contains_sensitive_memory
from sheetmate.db.models import FieldMapping, Memory, RecipeFieldMapping, advance_memory_id, now
from sheetmate.services.errors import NotFound, ServiceError


# Python str.strip() whitespace, shared with SQLite trim() for legacy rows.
_MEMORY_EDGE_WHITESPACE = (
    "\t\n\v\f\r\x1c\x1d\x1e\x1f \x85\xa0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000"
)


def set_memory(db: Session, *, category: str, key: str, value: str, user_id: int | None = None,
               customer_id: str | None = None, conversation_id: str | None = None,
               recipe_id: str | None = None, source: str = "ai",
               source_message_id: int | None = None, source_turn_id: str | None = None,
               evidence_span: str = "", replaces_memory_id: int | None = None,
               replaces_value: str | None = None) -> Memory:
    """同域原文去重；明确修改 AI 规则时校验目标及完整旧值，人工规则优先。"""
    category = category if category in CATEGORIES else "domain_knowledge"
    key, value = key.strip(), value.replace("\r\n", "\n").replace("\r", "\n").strip(_MEMORY_EDGE_WHITESPACE)
    if not key or not value:
        raise ServiceError("记忆内容不能为空")
    if source != "human" and any(contains_sensitive_memory(part)
                                  for part in (key, value, evidence_span)):
        raise ServiceError("这条内容包含敏感信息，未自动保存为长期记忆；原始消息仍保留。")
    scope = dict(user_id=user_id if not (customer_id or conversation_id) else None,
                 customer_id=customer_id or None,
                 conversation_id=(conversation_id or None) if not customer_id else None,
                 recipe_id=recipe_id)
    scoped = select(Memory).where(Memory.category == category,
                                   *(getattr(Memory, field) == val for field, val in scope.items()))
    replacing = replaces_memory_id is not None or replaces_value is not None

    def finish(item: Memory, status: str, reason: str = "") -> Memory:
        item._write_status = status
        item._write_actual_value = item.value
        item._write_actual_source = item.source
        item._write_reason = reason
        return item

    def blocked(reason: str) -> Memory:
        # No in-scope target exists to return. Keep the receipt transient and
        # avoid exposing an unrelated row's ID, content, or provenance.
        item = finish(Memory(category=category, key=key, value="", source=source,
                             version=1, **scope), "blocked", reason)
        item._write_actual_value = None
        item._write_actual_source = None
        return item

    if replacing and (source != "ai" or not isinstance(replaces_memory_id, int)
                      or isinstance(replaces_memory_id, bool) or replaces_memory_id <= 0
                      or not isinstance(replaces_value, str)):
        return blocked("规则更新缺少有效目标或完整旧值，本次未保存。")
    stmt = scoped.where(Memory.id == replaces_memory_id) if replacing else scoped.where(Memory.key == key)
    # Normalize only line endings and surrounding whitespace. Conditions,
    # units, and exceptions inside the user's wording remain significant.
    normalized_value = func.trim(func.replace(func.replace(Memory.value, "\r\n", "\n"), "\r", "\n"),
                                 _MEMORY_EDGE_WHITESPACE)
    duplicate_stmt = scoped.where(normalized_value == value)
    ordering = ((Memory.source == "human").desc(), Memory.updated_at.desc(), Memory.id.desc())
    # Flush caller-owned changes first; do not silently discard a direct human
    # edit when the CAS retry refreshes an existing identity-map object.
    db.flush()
    ordered = stmt.order_by(*ordering).limit(1)

    def confirm(item: Memory, match_stmt) -> bool:
        # A later user confirmation protects this wording against older
        # background extractions, without changing its content version.
        if (item.source != "ai" or source_message_id is None
                or (item.source_message_id is not None and source_message_id <= item.source_message_id)):
            return True
        confirmed = db.execute(update(Memory).where(
            match_stmt.whereclause, Memory.id == item.id, Memory.version == item.version,
            Memory.source == item.source, Memory.source_message_id == item.source_message_id,
            Memory.value == item.value,
        ).values(source_message_id=source_message_id, source_turn_id=source_turn_id,
                 evidence_span=evidence_span, updated_at=now())
          .execution_options(synchronize_session=False))
        if confirmed.rowcount == 1:
            db.refresh(item)
            return True
        db.expire(item)
        return False

    confirmation_target_id = None
    for _attempt in range(5):
        current_stmt = scoped.where(Memory.id == confirmation_target_id) if confirmation_target_id else stmt
        item = db.scalars(current_stmt.order_by(*ordering).limit(1)
                          .execution_options(populate_existing=True)).first()
        if item is None:
            if replacing or confirmation_target_id:
                return blocked("待更新规则不存在或不属于当前作用域，本次未保存。")
            if source == "ai":
                duplicate = db.scalars(duplicate_stmt.order_by(*ordering).limit(1)
                                       .execution_options(populate_existing=True)).first()
                if duplicate is not None:
                    if confirm(duplicate, duplicate_stmt):
                        return finish(duplicate, "unchanged")
                    # Retry the selected rule itself. A competing change must
                    # not turn this confirmation into a new-key insertion.
                    confirmation_target_id = duplicate.id
                    continue
            created_at = now()
            values = dict(id=advance_memory_id(db.connection()), category=category, key=key, value=value,
                          **scope, source=source, source_message_id=source_message_id,
                          source_turn_id=source_turn_id, evidence_span=evidence_span, version=1,
                          created_at=created_at, updated_at=created_at)
            # SQLite serializes writers. Checking existence inside the INSERT
            # makes concurrent creation atomic without retroactively adding a
            # unique constraint or deleting duplicate records in older DBs.
            exists = or_(stmt.exists(), duplicate_stmt.exists()) if source == "ai" else stmt.exists()
            inserted = db.execute(insert(Memory).from_select(
                list(values), select(*(literal(val) for val in values.values())).where(~exists),
                include_defaults=False,
            ))
            if inserted.rowcount == 1:
                item = db.scalars(ordered.execution_options(populate_existing=True)).one()
                status = "created"
                break
            continue
        if source != "human" and item.source == "human":
            return finish(item, "blocked", "人工维护的规则不可由 AI 自动修改。")
        if replacing and (item.source != "ai" or item.value != replaces_value):
            return finish(item, "blocked", "待更新规则已发生变化，本次未覆盖；请重新确认当前规则。")
        same_value = item.value.replace("\r\n", "\n").replace("\r", "\n").strip(_MEMORY_EDGE_WHITESPACE) == value
        if confirmation_target_id and not same_value:
            return finish(item, "blocked", "重复确认期间规则已发生变化，本次未覆盖。")
        stale = (source == "ai" and item.source == "ai" and item.source_message_id is not None
                 and (source_message_id is None or source_message_id < item.source_message_id))
        if stale:
            status = "unchanged"
            break
        if item.source == source and same_value:
            if source != "ai" or confirm(item, current_stmt):
                status = "unchanged"
                break
            confirmation_target_id = item.id
            continue
        collision = duplicate_stmt.where(Memory.id != item.id).correlate(None).exists()
        if source == "ai" and db.scalar(select(collision)):
            return finish(item, "blocked", "新内容已属于另一条记忆，本次未覆盖当前规则。")
        updated = db.execute(update(Memory).where(
            stmt.whereclause, Memory.id == item.id, Memory.version == item.version,
            Memory.source == item.source, Memory.source_message_id == item.source_message_id,
            Memory.value == item.value, *([~collision] if source == "ai" else []),
        ).values(value=value, source=source, source_message_id=source_message_id,
                 source_turn_id=source_turn_id, evidence_span=evidence_span,
                 execution_evidence={},
                 version=int(item.version or 0) + 1, updated_at=now())
          .execution_options(synchronize_session=False))
        if updated.rowcount == 1:
            db.refresh(item)
            status = "updated"
            break
        # No rows changed: re-read the committed winner, then apply human/source
        # ordering again. The losing request must not report a successful save.
        db.expire(item)
    else:
        raise ServiceError("记忆正被其他请求修改，本次未保存；请重试。")
    # The ORM object remains the public return value for compatibility. The
    # transient fields let callers report the actual write outcome without
    # changing existing code that reads Memory.value/source.
    return finish(item, status)


def memories_for(db: Session, *, user_id: int, customer_id: str | None, conversation_id: str | None,
                 recipe_id: str | None = None) -> list[dict[str, Any]]:
    """当前流程记忆 + 当前会话记忆 + 用户个人偏好，不串入其他流程。"""
    conds = [(Memory.user_id == user_id) & Memory.customer_id.is_(None) & Memory.conversation_id.is_(None)]
    if customer_id:
        # Only explicitly unbound customer background/preferences are shared.
        # Do not reinterpret recipe-bound preferences from older versions:
        # they may describe a different recipe's calculation precision.
        shared = Memory.recipe_id.is_(None) & Memory.category.in_(("preferences", "user_context"))
        recipe_scope = ((Memory.recipe_id == recipe_id) | shared) if recipe_id else Memory.recipe_id.is_(None)
        conds.append((Memory.customer_id == customer_id) & recipe_scope)
    if conversation_id:
        conds.append((Memory.conversation_id == conversation_id) & Memory.customer_id.is_(None)
                     & ((Memory.recipe_id == recipe_id) | Memory.recipe_id.is_(None)))
    items = db.scalars(select(Memory).where(or_(*conds))
                       .order_by((Memory.source == "human").desc(), Memory.updated_at.desc(), Memory.id.desc()))
    return [memory_dict(m) for m in items]


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


def customer_memories(db: Session, customer_id: str, *, recipe_id: str | None = None,
                      source: str | None = None) -> list[dict[str, Any]]:
    if recipe_id:
        shared = Memory.recipe_id.is_(None) & Memory.category.in_(("preferences", "user_context"))
        stmt = select(Memory).where(Memory.customer_id == customer_id,
                                    ((Memory.recipe_id == recipe_id) | shared))
    else:
        stmt = select(Memory).where(Memory.customer_id == customer_id, Memory.recipe_id.is_(None))
    if source is not None:
        stmt = stmt.where(Memory.source == source)
    return [memory_dict(m) for m in db.scalars(stmt.order_by(Memory.updated_at.desc(), Memory.id.desc()))]


def customer_visible_memories(db: Session, customer_id: str, *, recipe_id: str | None = None) -> list[dict[str, Any]]:
    """Customer UI: user-maintained rules and inspectable verified repairs."""
    return [item for item in customer_memories(db, customer_id, recipe_id=recipe_id)
            if item.get("source") == "human" or is_verified_repair_memory(item)]


def delete_memory(db: Session, memory_id: int) -> Memory:
    item = db.get(Memory, memory_id)
    if item is None:
        raise NotFound("记忆不存在")
    db.delete(item)
    return item


def move_conversation_memories(db: Session, conversation_id: str, customer_id: str,
                               recipe_id: str | None = None) -> int:
    items = list(db.scalars(select(Memory).where(Memory.conversation_id == conversation_id, Memory.customer_id.is_(None))))
    for item in items:
        # 已有流程归属优先；会话当前选择只补齐旧的未归属记录。
        # Customer background and preferences are shared facts; operational
        # rules/experience remain attached to the selected recipe.
        target_recipe = item.recipe_id or (None if item.category in {"preferences", "user_context"} else recipe_id)
        existing = db.scalars(select(Memory).where(Memory.customer_id == customer_id,
                                                   Memory.recipe_id == target_recipe,
                                                   Memory.category == item.category, Memory.key == item.key)
                              .order_by((Memory.source == "human").desc()).limit(1)).first()
        if existing:
            human_update = item.source == "human" and (
                existing.source != "human" or item.updated_at >= existing.updated_at)
            if item.source_message_id is not None and existing.source_message_id is not None:
                ai_is_newer = item.source_message_id > existing.source_message_id
            elif existing.source_message_id is not None:
                # A legacy row without provenance cannot displace a sourced rule.
                ai_is_newer = False
            else:
                ai_is_newer = item.updated_at >= existing.updated_at
            ai_update = existing.source != "human" and item.source != "human" and ai_is_newer
            if human_update or ai_update:
                existing.value, existing.source = item.value, item.source
                existing.source_message_id, existing.source_turn_id = item.source_message_id, item.source_turn_id
                existing.evidence_span = item.evidence_span
                existing.execution_evidence = getattr(item, "execution_evidence", None) or {}
                existing.version = int(existing.version or 0) + 1
            db.delete(item)
        else:
            item.customer_id, item.conversation_id, item.recipe_id = customer_id, None, target_recipe
    return len(items)


def recall_memories(db: Session, *, user_id: int, customer_id: str | None, conversation_id: str | None,
                    query: str, recipe_id: str | None = None, limit: int = 30,
                    max_chars: int = MEMORY_CONTEXT_CHARS) -> dict[str, Any]:
    """Filter access scope before lexical ranking and return an auditable subset."""
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
        text += (f"\n（记忆召回：词项相关性+MMR+近期偏好；已选 {metadata['selected_count']}/{metadata['available_count']} 条，"
                 f"同域原文重复 {metadata['duplicate_count']} 条；省略不代表不存在，需要时调用 inspect_saved_memories 按 ID 或关键词检索原文。）")
    if len(text) > max_chars:
        raise ServiceError("当前记忆与字段映射超出上下文预算；本轮未截断人工规则，请缩小范围。")
    return text


def _mapping_rows(db: Session, customer_id: str, recipe_id: str | None):
    model = RecipeFieldMapping if recipe_id else FieldMapping
    stmt = select(model).where(model.customer_id == customer_id)
    if recipe_id:
        stmt = stmt.where(model.recipe_id == recipe_id)
    return list(db.scalars(stmt.order_by(model.id)))


def field_mapping_entries(db: Session, customer_id: str | None,
                          recipe_id: str | None = None) -> list[dict[str, Any]]:
    if not customer_id:
        return []
    return [{"standard": r.standard, "aliases": list(r.aliases or []), "source": r.source}
            for r in _mapping_rows(db, customer_id, recipe_id) if r.confirmed]


def field_mapping_for(db: Session, customer_id: str | None, recipe_id: str | None = None) -> dict[str, list[str]]:
    entries = field_mapping_entries(db, customer_id, recipe_id)
    # 人工指定的字段归属优先，AI 不得将同一别名改指向另一个标准字段。
    reserved = {name for e in entries if e["source"] == "human" for name in [e["standard"], *e["aliases"]]}
    result: dict[str, list[str]] = {}
    for entry in sorted(entries, key=lambda e: e["source"] == "human"):
        aliases = entry["aliases"]
        if entry["source"] != "human":
            aliases = [a for a in aliases if a not in reserved]
            if entry["standard"] in reserved:
                continue
        if aliases:
            result[entry["standard"]] = aliases
    return result


def _clean_mapping(standard: str, aliases: list[str]) -> tuple[str, list[str]]:
    standard = standard.strip()
    clean = list(dict.fromkeys(str(a).strip() for a in aliases if str(a).strip() and str(a).strip() != standard))
    if not standard or not clean:
        raise ServiceError("标准字段名和别名都不能为空")
    return standard, clean


def upsert_field_mapping(db: Session, customer_id: str, standard: str, aliases: list[str], source: str = "human",
                          recipe_id: str | None = None) -> FieldMapping | RecipeFieldMapping:
    standard, clean = _clean_mapping(standard, aliases)
    rows = _mapping_rows(db, customer_id, recipe_id)
    row = next((r for r in rows if r.standard == standard), None)
    if row is None:
        model = RecipeFieldMapping if recipe_id else FieldMapping
        data = {"customer_id": customer_id, "standard": standard, "aliases": clean,
                "source": source, "confirmed": True}
        if recipe_id:
            data["recipe_id"] = recipe_id
        row = model(**data)
        db.add(row)
    elif source == "human" or row.source != "human":
        row.aliases = list(dict.fromkeys([*(row.aliases or []), *clean]))
        row.source = source
    db.flush()
    return row


def replace_field_mappings(db: Session, customer_id: str, mapping: dict[str, list[str]],
                            recipe_id: str | None = None) -> dict[str, list[str]]:
    """表单保存：未修改的 AI 映射保留来源，新建或修改的标为人工。"""
    clean = {}
    for standard, aliases in mapping.items():
        if standard.strip() and aliases:
            key, values = _clean_mapping(standard, aliases)
            clean[key] = values
    rows = _mapping_rows(db, customer_id, recipe_id)
    existing = {r.standard: r for r in rows}
    for row in rows:
        if row.standard not in clean:
            db.delete(row)
    db.flush()
    for standard, aliases in clean.items():
        row = existing.get(standard)
        if row is None:
            upsert_field_mapping(db, customer_id, standard, aliases, recipe_id=recipe_id)
        elif set(row.aliases or []) != set(aliases):
            row.aliases, row.source = aliases, "human"
    db.flush()
    return field_mapping_for(db, customer_id, recipe_id)


def migrate_customer_context(db: Session, customer_id: str, recipe_id: str) -> None:
    """旧客户级资料只归入最初主流程一次，避免复制到其他流程。"""
    # Preserve customer-level background/preferences, while retaining the old
    # one-time assignment for operational rules and experience. Previously
    # recipe-bound records are not widened automatically.
    for item in db.scalars(select(Memory).where(Memory.customer_id == customer_id, Memory.recipe_id.is_(None),
                                               Memory.category.notin_(("preferences", "user_context")))):
        item.recipe_id = recipe_id
    for row in _mapping_rows(db, customer_id, None):
        target = next((item for item in _mapping_rows(db, customer_id, recipe_id)
                       if item.standard == row.standard), None)
        if target is None:
            target = RecipeFieldMapping(customer_id=customer_id, recipe_id=recipe_id,
                                        standard=row.standard, aliases=list(row.aliases or []),
                                        source=row.source, confirmed=row.confirmed,
                                        created_at=row.created_at, updated_at=row.updated_at)
            db.add(target)
        elif row.source == "human" and target.source != "human":
            target.aliases, target.source, target.confirmed = list(row.aliases or []), "human", row.confirmed
        db.delete(row)
    db.flush()
