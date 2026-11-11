"""Customer and recipe field mapping persistence."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sheetmate.db.models import FieldMapping, Memory, RecipeFieldMapping
from sheetmate.core.errors import ServiceError


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
