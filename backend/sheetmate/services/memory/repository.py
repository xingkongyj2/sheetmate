"""SQL repository for scoped long-term memory records and CAS writes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, insert, literal, or_, select, update
from sqlalchemy.orm import Session

from sheetmate.agent.memory.long_term.policy import CATEGORIES
from sheetmate.core.security.privacy import contains_sensitive_memory
from sheetmate.db.models import Memory, advance_memory_id, now
from sheetmate.core.errors import NotFound, ServiceError
from sheetmate.services.memory.serialization import memory_dict

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
