"""记忆存取：memories 表 + 客户字段映射（field_mappings 表）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from sheetmate.agent.memory import CATEGORIES, CATEGORY_LABELS, format_memories, select_relevant
from sheetmate.db.models import FieldMapping, Memory
from sheetmate.services.errors import NotFound, ServiceError


def set_memory(db: Session, *, category: str, key: str, value: str, user_id: int | None = None, customer_id: str | None = None,
               conversation_id: str | None = None, source: str = "ai") -> Memory:
    """写入或更新一条记忆（同一作用域下同 key 覆盖）。"""
    if category not in CATEGORIES:
        category = "domain_knowledge"
    stmt = select(Memory).where(Memory.category == category, Memory.key == key)
    if customer_id:
        stmt = stmt.where(Memory.customer_id == customer_id)
    elif conversation_id:
        stmt = stmt.where(Memory.conversation_id == conversation_id, Memory.customer_id.is_(None))
    else:
        stmt = stmt.where(Memory.user_id == user_id, Memory.customer_id.is_(None), Memory.conversation_id.is_(None))
    item = db.scalars(stmt.limit(1)).first()
    if item is None:
        item = Memory(category=category, key=key, value=value, user_id=user_id if not (customer_id or conversation_id) else None,
                      customer_id=customer_id, conversation_id=None if customer_id else conversation_id, source=source)
        db.add(item)
    else:
        item.value = value
        item.source = source
    db.flush()
    return item


def memories_for(db: Session, *, user_id: int, customer_id: str | None, conversation_id: str | None) -> list[dict[str, Any]]:
    """当前对话能看到的记忆：客户的（或会话的）+ 用户个人偏好。"""
    conds = [(Memory.user_id == user_id) & Memory.customer_id.is_(None) & Memory.conversation_id.is_(None)]
    if customer_id:
        conds.append(Memory.customer_id == customer_id)
    if conversation_id:
        conds.append((Memory.conversation_id == conversation_id) & Memory.customer_id.is_(None))
    items = db.scalars(select(Memory).where(or_(*conds)).order_by(Memory.updated_at.desc()))
    return [memory_dict(m) for m in items]


def memory_dict(m: Memory) -> dict[str, Any]:
    scope = "客户" if m.customer_id else "会话" if m.conversation_id else "个人"
    return {"id": m.id, "category": m.category, "category_label": CATEGORY_LABELS.get(m.category, m.category), "key": m.key,
            "value": m.value, "scope_label": scope, "source": m.source, "updated_at": m.updated_at.isoformat()}


def customer_memories(db: Session, customer_id: str) -> list[dict[str, Any]]:
    return [memory_dict(m) for m in db.scalars(select(Memory).where(Memory.customer_id == customer_id).order_by(Memory.updated_at.desc()))]


def delete_memory(db: Session, memory_id: int) -> Memory:
    item = db.get(Memory, memory_id)
    if item is None:
        raise NotFound("记忆不存在")
    db.delete(item)
    return item


def move_conversation_memories(db: Session, conversation_id: str, customer_id: str) -> int:
    items = list(db.scalars(select(Memory).where(Memory.conversation_id == conversation_id, Memory.customer_id.is_(None))))
    for item in items:
        existing = db.scalars(select(Memory).where(Memory.customer_id == customer_id, Memory.category == item.category,
                                                   Memory.key == item.key).limit(1)).first()
        if existing:
            existing.value = item.value
            db.delete(item)
        else:
            item.customer_id = customer_id
            item.conversation_id = None
    return len(items)


def memory_text(db: Session, *, user_id: int, customer_id: str | None, conversation_id: str | None, query: str) -> str:
    items = memories_for(db, user_id=user_id, customer_id=customer_id, conversation_id=conversation_id)
    return format_memories(select_relevant(items, query), field_mapping_for(db, customer_id))


# ---------------------------------------------------------------------------
# 字段映射
# ---------------------------------------------------------------------------


def field_mapping_for(db: Session, customer_id: str | None) -> dict[str, list[str]]:
    if not customer_id:
        return {}
    rows = db.scalars(select(FieldMapping).where(FieldMapping.customer_id == customer_id, FieldMapping.confirmed.is_(True)))
    return {r.standard: list(r.aliases or []) for r in rows}


def upsert_field_mapping(db: Session, customer_id: str, standard: str, aliases: list[str], source: str = "human") -> FieldMapping:
    standard = standard.strip()
    clean = []
    for a in aliases:
        a = str(a).strip()
        if a and a != standard and a not in clean:
            clean.append(a)
    if not standard or not clean:
        raise ServiceError("标准字段名和别名都不能为空")
    row = db.scalars(select(FieldMapping).where(FieldMapping.customer_id == customer_id, FieldMapping.standard == standard).limit(1)).first()
    if row is None:
        row = FieldMapping(customer_id=customer_id, standard=standard, aliases=clean, source=source, confirmed=True)
        db.add(row)
    else:
        row.aliases = list(dict.fromkeys([*(row.aliases or []), *clean]))
        row.source = source
    db.flush()
    return row


def replace_field_mappings(db: Session, customer_id: str, mapping: dict[str, list[str]]) -> dict[str, list[str]]:
    for row in db.scalars(select(FieldMapping).where(FieldMapping.customer_id == customer_id)):
        db.delete(row)
    db.flush()
    for standard, aliases in mapping.items():
        if standard.strip() and aliases:
            upsert_field_mapping(db, customer_id, standard, aliases)
    return field_mapping_for(db, customer_id)
