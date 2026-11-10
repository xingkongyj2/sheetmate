"""记忆与字段映射：流程隔离，人工维护的规则优先于 AI 参考。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import insert, literal, or_, select, update
from sqlalchemy.orm import Session

from sheetmate.agent.memory import (
    CATEGORIES, CATEGORY_LABELS, MEMORY_CONTEXT_CHARS, MemoryContextLimit, format_memories, select_relevant,
)
from sheetmate.db.models import FieldMapping, Memory, RecipeFieldMapping, now
from sheetmate.services.errors import NotFound, ServiceError


def set_memory(db: Session, *, category: str, key: str, value: str, user_id: int | None = None,
               customer_id: str | None = None, conversation_id: str | None = None,
               recipe_id: str | None = None, source: str = "ai",
               source_message_id: int | None = None, source_turn_id: str | None = None,
               evidence_span: str = "") -> Memory:
    """同一流程、类别和 key 更新；AI 不得覆盖人工维护的内容。"""
    category = category if category in CATEGORIES else "domain_knowledge"
    key, value = key.strip(), value.strip()
    if not key or not value:
        raise ServiceError("记忆内容不能为空")
    stmt = select(Memory).where(Memory.category == category, Memory.key == key,
                                Memory.recipe_id == recipe_id)
    if customer_id:
        stmt = stmt.where(Memory.customer_id == customer_id)
    elif conversation_id:
        stmt = stmt.where(Memory.conversation_id == conversation_id, Memory.customer_id.is_(None))
    else:
        stmt = stmt.where(Memory.user_id == user_id, Memory.customer_id.is_(None), Memory.conversation_id.is_(None))
    # Flush caller-owned changes first; do not silently discard a direct human
    # edit when the CAS retry refreshes an existing identity-map object.
    db.flush()
    ordered = stmt.order_by((Memory.source == "human").desc(), Memory.updated_at.desc()).limit(1)
    for _attempt in range(5):
        item = db.scalars(ordered.execution_options(populate_existing=True)).first()
        if item is None:
            created_at = now()
            values = dict(category=category, key=key, value=value,
                          user_id=user_id if not (customer_id or conversation_id) else None,
                          customer_id=customer_id, conversation_id=None if customer_id else conversation_id,
                          recipe_id=recipe_id, source=source, source_message_id=source_message_id,
                          source_turn_id=source_turn_id, evidence_span=evidence_span, version=1,
                          created_at=created_at, updated_at=created_at)
            # SQLite serializes writers. Checking existence inside the INSERT
            # makes concurrent creation atomic without retroactively adding a
            # unique constraint or deleting duplicate records in older DBs.
            inserted = db.execute(insert(Memory).from_select(
                list(values), select(*(literal(val) for val in values.values())).where(~stmt.exists()),
                include_defaults=False,
            ))
            if inserted.rowcount == 1:
                item = db.scalars(ordered.execution_options(populate_existing=True)).one()
                status = "created"
                break
            continue
        if source != "human" and item.source == "human":
            status = "blocked"
            break
        stale = (source == "ai" and item.source == "ai" and item.source_message_id is not None
                 and (source_message_id is None or source_message_id < item.source_message_id))
        if stale or (item.source == source and item.value == value and item.source_message_id == source_message_id):
            status = "unchanged"
            break
        updated = db.execute(update(Memory).where(
            stmt.whereclause, Memory.id == item.id, Memory.version == item.version,
            Memory.source == item.source, Memory.source_message_id == item.source_message_id,
            Memory.value == item.value,
        ).values(value=value, source=source, source_message_id=source_message_id,
                 source_turn_id=source_turn_id, evidence_span=evidence_span,
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
    item._write_status = status
    item._write_actual_value = item.value
    item._write_actual_source = item.source
    return item


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
            "source": m.source, "source_message_id": m.source_message_id,
            "source_turn_id": m.source_turn_id, "evidence_span": m.evidence_span or "", "version": m.version or 1,
            "updated_at": m.updated_at.isoformat()}


def memory_write_result(item: Memory) -> dict[str, Any]:
    """Return the actual persistence outcome attached by set_memory."""
    return {
        "status": getattr(item, "_write_status", "updated"),
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
                existing.version = int(existing.version or 0) + 1
            db.delete(item)
        else:
            item.customer_id, item.conversation_id, item.recipe_id = customer_id, None, target_recipe
    return len(items)


def memory_text(db: Session, *, user_id: int, customer_id: str | None, conversation_id: str | None,
                query: str, recipe_id: str | None = None) -> str:
    items = memories_for(db, user_id=user_id, customer_id=customer_id, conversation_id=conversation_id,
                         recipe_id=recipe_id)
    try:
        selected = select_relevant(items, query)
    except MemoryContextLimit as exc:
        raise ServiceError(str(exc)) from exc
    text = format_memories(selected, field_mapping_for(db, customer_id, recipe_id))
    omitted = len(items) - len(selected)
    if omitted:
        text += f"\n（另有 {omitted} 条 AI 参考记忆未纳入本次上下文，不能据此假设这些记忆不存在。）"
    if len(text) > MEMORY_CONTEXT_CHARS + 2_000:
        raise ServiceError("当前记忆与字段映射超出上下文预算，请精简重复映射或缩小流程范围；本轮未截断人工规则。")
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
