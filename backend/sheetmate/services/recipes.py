"""Recipe 与客户公式的存取。

- Recipe 按版本保存：同一个 recipe_id 改一次升一版，旧版本保留（历史运行可以追溯到当时的版本）
- 状态：draft（AI 生成未确认）→ active（验证通过或人工确认）→ deprecated
- 客户公式：scope=customer，同 id 覆盖内置公式
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sheetmate.db.models import FormulaRecord, RecipeRecord, now
from sheetmate.engine.formulas import BUILTIN_FORMULAS, Formula, FormulaLibrary
from sheetmate.recipe.schema import Recipe
from sheetmate.services.errors import NotFound, ServiceError

STATUS_LABELS = {"draft": "草稿", "active": "已启用", "deprecated": "已停用"}


# ---------------------------------------------------------------------------
# Recipe
# ---------------------------------------------------------------------------


def save_recipe(db: Session, recipe: Recipe, *, status: str | None = None, conversation_id: str | None = None) -> RecipeRecord:
    """保存为新版本（recipe_id 不存在时就是第 1 版）。"""
    latest = db.scalar(select(func.max(RecipeRecord.version)).where(RecipeRecord.recipe_id == recipe.id)) or 0
    recipe.version = latest + 1
    recipe.updated_at = now().isoformat()
    if status:
        recipe.status = status  # type: ignore[assignment]
    record = RecipeRecord(
        recipe_id=recipe.id, version=recipe.version, name=recipe.name, scope=recipe.scope, customer_id=recipe.customer_id,
        conversation_id=conversation_id or (recipe.learned_from or {}).get("conversation_id"), status=recipe.status,
        created_by=recipe.created_by, body=recipe.to_dict(),
    )
    db.add(record)
    db.flush()
    return record


def get_recipe(db: Session, recipe_id: str, version: int | None = None) -> Recipe:
    stmt = select(RecipeRecord).where(RecipeRecord.recipe_id == recipe_id)
    stmt = stmt.where(RecipeRecord.version == version) if version else stmt.order_by(RecipeRecord.version.desc())
    record = db.scalars(stmt.limit(1)).first()
    if record is None:
        raise NotFound("流程不存在")
    return record_to_recipe(record)


def get_record(db: Session, recipe_id: str) -> RecipeRecord:
    record = db.scalars(select(RecipeRecord).where(RecipeRecord.recipe_id == recipe_id).order_by(RecipeRecord.version.desc()).limit(1)).first()
    if record is None:
        raise NotFound("流程不存在")
    return record


def record_to_recipe(record: RecipeRecord) -> Recipe:
    data = dict(record.body or {})
    data.update({"id": record.recipe_id, "version": record.version, "status": record.status,
                 "customer_id": record.customer_id, "scope": record.scope})
    return Recipe(**data)


def latest_records(db: Session, *, customer_id: str | None = None, conversation_id: str | None = None,
                   include_deprecated: bool = False) -> list[RecipeRecord]:
    """每个 recipe_id 只取最新版本。"""
    sub = select(RecipeRecord.recipe_id, func.max(RecipeRecord.version).label("v")).group_by(RecipeRecord.recipe_id).subquery()
    stmt = select(RecipeRecord).join(sub, (RecipeRecord.recipe_id == sub.c.recipe_id) & (RecipeRecord.version == sub.c.v))
    if customer_id is not None:
        stmt = stmt.where(RecipeRecord.customer_id == customer_id)
    if conversation_id is not None:
        stmt = stmt.where(RecipeRecord.conversation_id == conversation_id)
    if not include_deprecated:
        stmt = stmt.where(RecipeRecord.status != "deprecated")
    return list(db.scalars(stmt.order_by(RecipeRecord.updated_at.desc())))


def versions(db: Session, recipe_id: str) -> list[dict[str, Any]]:
    records = db.scalars(select(RecipeRecord).where(RecipeRecord.recipe_id == recipe_id).order_by(RecipeRecord.version.desc()))
    return [
        {"version": r.version, "status": r.status, "status_label": STATUS_LABELS.get(r.status, r.status),
         "created_at": r.created_at.isoformat(), "steps": len((r.body or {}).get("steps", [])),
         "note": (r.body or {}).get("metadata", {}).get("version_note", "")}
        for r in records
    ]


def set_status(db: Session, recipe_id: str, status: str) -> RecipeRecord:
    if status not in STATUS_LABELS:
        raise ServiceError("状态只能是 draft / active / deprecated")
    record = get_record(db, recipe_id)
    record.status = status
    body = dict(record.body or {})
    body["status"] = status
    record.body = body
    return record


def assign_customer(db: Session, *, conversation_id: str, customer_id: str) -> int:
    """会话保存到客户时，会话里生成的流程一起归到客户名下（所有版本）。"""
    records = list(db.scalars(select(RecipeRecord).where(RecipeRecord.conversation_id == conversation_id)))
    for record in records:
        record.customer_id = customer_id
        body = dict(record.body or {})
        body["customer_id"] = customer_id
        record.body = body
    return len({r.recipe_id for r in records})


def recipe_brief(record: RecipeRecord) -> dict[str, Any]:
    body = record.body or {}
    steps = sorted((s for s in body.get("steps", []) if s.get("enabled", True)), key=lambda s: s.get("id", 0))
    meta = body.get("metadata", {})
    return {
        "recipe_id": record.recipe_id,
        "version": record.version,
        "name": record.name,
        "description": body.get("description", ""),
        "status": record.status,
        "status_label": STATUS_LABELS.get(record.status, record.status),
        "customer_id": record.customer_id,
        "conversation_id": record.conversation_id,
        "inputs": [i.get("alias") for i in body.get("inputs", [])],
        "input_patterns": [{"alias": i.get("alias"), "file_pattern": i.get("file_pattern"), "description": i.get("description")}
                           for i in body.get("inputs", [])],
        "outputs": [o.get("name") for o in body.get("output_spec", [])],
        "step_count": len(steps),
        "steps": [{"id": s.get("id"), "name": s.get("name"), "formula": s.get("formula"), "formula_kind": s.get("formula_kind"),
                   "description": s.get("description")} for s in steps],
        "verification": meta.get("verification"),
        "last_run": meta.get("last_run"),
        "created_by": record.created_by,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


# ---------------------------------------------------------------------------
# 公式库
# ---------------------------------------------------------------------------


def customer_formulas(db: Session, customer_id: str | None) -> list[Formula]:
    if not customer_id:
        return []
    records = db.scalars(select(FormulaRecord).where(FormulaRecord.customer_id == customer_id))
    formulas = []
    for record in records:
        try:
            formulas.append(Formula(**{**record.body, "version": record.version, "status": record.status, "scope": "customer",
                                       "customer_id": customer_id, "type": "sql"}))
        except ValueError:
            continue
    return formulas


def library_for(db: Session, customer_id: str | None) -> FormulaLibrary:
    return FormulaLibrary(customer_formulas(db, customer_id))


def save_formula(db: Session, customer_id: str, body: dict[str, Any], *, created_by: str = "human") -> FormulaRecord:
    formula = Formula(**{**body, "scope": "customer", "customer_id": customer_id, "type": "sql", "created_by": created_by})
    formula.check_definition()
    latest = db.scalar(select(func.max(FormulaRecord.version)).where(
        FormulaRecord.customer_id == customer_id, FormulaRecord.formula_id == formula.id)) or 0
    record = FormulaRecord(
        formula_id=formula.id, version=latest + 1, scope="customer", customer_id=customer_id, name=formula.name,
        status=formula.status, created_by=created_by, body=formula.model_dump(mode="json", by_alias=True),
    )
    db.add(record)
    db.flush()
    return record


def formula_dict(spec: Formula, kind: str) -> dict[str, Any]:
    return {
        "id": spec.id, "name": spec.name, "category": spec.category, "intent": spec.intent, "kind": kind,
        "kind_label": "客户独有" if kind == "customer" else "内置",
        "version": spec.version, "status": spec.status,
        "params": {k: {"type": p.type, "required": p.required, "default": p.default, "desc": p.desc, "values": p.values}
                   for k, p in spec.params.items()},
        "sql": spec.sql, "example": spec.example, "triggers": spec.triggers,
        "overrides_builtin": kind == "customer" and spec.id in BUILTIN_FORMULAS,
    }
