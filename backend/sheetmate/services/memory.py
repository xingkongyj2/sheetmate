"""记忆与字段映射：流程隔离，人工维护的规则优先于 AI 参考。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from sheetmate.agent.memory import CATEGORIES, CATEGORY_LABELS, format_memories, select_relevant
from sheetmate.db.models import FieldMapping, Memory, RecipeFieldMapping
from sheetmate.services.errors import NotFound, ServiceError


def set_memory(db: Session, *, category: str, key: str, value: str, user_id: int | None = None,
               customer_id: str | None = None, conversation_id: str | None = None,
               recipe_id: str | None = None, source: str = "ai") -> Memory:
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
    item = db.scalars(stmt.order_by((Memory.source == "human").desc(), Memory.updated_at.desc()).limit(1)).first()
    if item is None:
        item = Memory(category=category, key=key, value=value, user_id=user_id if not (customer_id or conversation_id) else None,
                      customer_id=customer_id, conversation_id=None if customer_id else conversation_id,
                      recipe_id=recipe_id, source=source)
        db.add(item)
    elif source == "human" or item.source != "human":
        item.value = value
        item.source = source
    db.flush()
    return item


def memories_for(db: Session, *, user_id: int, customer_id: str | None, conversation_id: str | None,
                 recipe_id: str | None = None) -> list[dict[str, Any]]:
    """当前流程记忆 + 当前会话记忆 + 用户个人偏好，不串入其他流程。"""
    conds = [(Memory.user_id == user_id) & Memory.customer_id.is_(None) & Memory.conversation_id.is_(None)]
    if customer_id:
        conds.append((Memory.customer_id == customer_id) & (Memory.recipe_id == recipe_id))
    if conversation_id:
        conds.append((Memory.conversation_id == conversation_id) & Memory.customer_id.is_(None)
                     & (Memory.recipe_id == recipe_id))
    items = db.scalars(select(Memory).where(or_(*conds))
                       .order_by((Memory.source == "human").desc(), Memory.updated_at.desc(), Memory.id.desc()))
    return [memory_dict(m) for m in items]


def memory_dict(m: Memory) -> dict[str, Any]:
    scope = "流程" if m.recipe_id else "客户" if m.customer_id else "会话" if m.conversation_id else "个人"
    return {"id": m.id, "category": m.category, "category_label": CATEGORY_LABELS.get(m.category, m.category),
            "key": m.key, "value": m.value, "recipe_id": m.recipe_id, "scope_label": scope,
            "source": m.source, "updated_at": m.updated_at.isoformat()}


def customer_memories(db: Session, customer_id: str, *, recipe_id: str | None = None,
                      source: str | None = None) -> list[dict[str, Any]]:
    stmt = select(Memory).where(Memory.customer_id == customer_id, Memory.recipe_id == recipe_id)
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
        target_recipe = item.recipe_id or recipe_id
        existing = db.scalars(select(Memory).where(Memory.customer_id == customer_id,
                                                   Memory.recipe_id == target_recipe,
                                                   Memory.category == item.category, Memory.key == item.key)
                              .order_by((Memory.source == "human").desc()).limit(1)).first()
        if existing:
            human_update = item.source == "human" and (
                existing.source != "human" or item.updated_at >= existing.updated_at)
            if human_update or existing.source != "human" and item.source != "human":
                existing.value, existing.source = item.value, item.source
            db.delete(item)
        else:
            item.customer_id, item.conversation_id, item.recipe_id = customer_id, None, target_recipe
    return len(items)


def memory_text(db: Session, *, user_id: int, customer_id: str | None, conversation_id: str | None,
                query: str, recipe_id: str | None = None) -> str:
    items = memories_for(db, user_id=user_id, customer_id=customer_id, conversation_id=conversation_id,
                         recipe_id=recipe_id)
    return format_memories(select_relevant(items, query), field_mapping_for(db, customer_id, recipe_id))


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
    for item in db.scalars(select(Memory).where(Memory.customer_id == customer_id, Memory.recipe_id.is_(None))):
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
