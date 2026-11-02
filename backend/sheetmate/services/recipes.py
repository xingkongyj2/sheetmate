"""Recipe 与客户公式的存取。

- Recipe 按版本保存：同一个 recipe_id 改一次升一版，旧版本保留（历史运行可以追溯到当时的版本）
- 状态：draft（AI 生成未确认）→ active（验证通过或人工确认）→ deprecated
- 客户公式：scope=customer，同 id 覆盖内置公式
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from sheetmate.db.models import Conversation, FormulaRecord, RecipeRecord, Run, User, now
from sheetmate.engine.formulas import BUILTIN_FORMULAS, Formula, FormulaError, FormulaLibrary
from sheetmate.engine.script_references import bind_script_references, externalize_script_references
from sheetmate.recipe.schema import Recipe
from sheetmate.services.auth import require_customer
from sheetmate.services.errors import Forbidden, NotFound, ServiceError

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
    _bind_scripts(record)
    return record


def get_recipe(db: Session, recipe_id: str, version: int | None = None) -> Recipe:
    stmt = select(RecipeRecord).where(RecipeRecord.recipe_id == recipe_id)
    stmt = stmt.where(RecipeRecord.version == version) if version else stmt.order_by(RecipeRecord.version.desc())
    record = db.scalars(stmt.limit(1)).first()
    if record is None:
        raise NotFound("流程不存在")
    return record_to_recipe(record)


def get_record(db: Session, recipe_id: str, version: int | None = None) -> RecipeRecord:
    stmt = select(RecipeRecord).where(RecipeRecord.recipe_id == recipe_id)
    stmt = stmt.where(RecipeRecord.version == version) if version is not None else stmt.order_by(RecipeRecord.version.desc())
    record = db.scalars(stmt.limit(1)).first()
    if record is None:
        raise NotFound("流程不存在")
    return record


def record_to_recipe(record: RecipeRecord) -> Recipe:
    data = dict(record.body or {})
    data.update({"id": record.recipe_id, "version": record.version, "status": record.status,
                 "customer_id": record.customer_id, "scope": record.scope})
    recipe = Recipe(**data)
    body = recipe.to_dict()
    if record.body != body:
        record.body = body
    _bind_scripts(record)
    return recipe


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


def delete_recipe(db: Session, recipe_id: str, *, user: User) -> dict[str, Any]:
    """删除可复用流程全部版本；保留结果文件和可审计的历史运行。"""
    record = get_record(db, recipe_id)
    if record.customer_id:
        require_customer(db, user, record.customer_id)
    elif user.role != "admin":
        conversation_id = record.conversation_id or (record.body or {}).get("learned_from", {}).get("conversation_id")
        conversation = db.get(Conversation, conversation_id) if conversation_id else None
        if conversation is None or conversation.user_id != user.id:
            raise Forbidden("你没有权限删除这个流程")
    deleted = recipe_brief(record)
    for run in db.scalars(select(Run).where(Run.recipe_id == recipe_id)):
        run.validation = {**(run.validation or {}), "recipe_deleted": True}
    db.execute(delete(RecipeRecord).where(RecipeRecord.recipe_id == recipe_id))
    return deleted


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
    body = externalize_script_references(record.body or {})
    if body != record.body:
        record.body = body
    _bind_scripts(record)
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
        "input_patterns": [{"alias": i.get("alias"), "kind": i.get("kind", "table"), "file_pattern": i.get("file_pattern"), "description": i.get("description")}
                           for i in body.get("inputs", [])],
        "outputs": [o.get("name") for o in body.get("output_spec", [])],
        "step_count": len(steps),
        "steps": [{"id": s.get("id"), "name": s.get("name"), "kind": s.get("kind") or ("sql" if s.get("formula") == "custom_sql" else "python" if s.get("formula") == "python_script" else "formula"), "formula": s.get("formula"), "formula_kind": s.get("formula_kind"),
                   "description": s.get("description"),
                   **{key: s.get("params", {}).get(key) for key in ("script_id", "script_title", "script_filename")
                      if s.get("params", {}).get(key)}} for s in steps],
        "verification": meta.get("verification"),
        "last_run": meta.get("last_run"),
        "created_by": record.created_by,
        "updated_at": record.updated_at.isoformat() if record.updated_at else None,
    }


def _bind_scripts(record: RecipeRecord) -> None:
    bind_script_references(record.body, customer_id=record.customer_id,
                           conversation_id=record.conversation_id or (record.body or {}).get("learned_from", {}).get("conversation_id"),
                           global_access=record.scope == "global")


# ---------------------------------------------------------------------------
# 公式库
# ---------------------------------------------------------------------------


def latest_formula_records(db: Session, customer_id: str | None) -> list[FormulaRecord]:
    """客户的每个公式 id 只取最新版本（已停用的也在）。"""
    if not customer_id:
        return []
    sub = (select(FormulaRecord.formula_id, func.max(FormulaRecord.version).label("v"))
           .where(FormulaRecord.customer_id == customer_id).group_by(FormulaRecord.formula_id).subquery())
    stmt = (select(FormulaRecord).join(sub, (FormulaRecord.formula_id == sub.c.formula_id) & (FormulaRecord.version == sub.c.v))
            .where(FormulaRecord.customer_id == customer_id).order_by(FormulaRecord.formula_id))
    return list(db.scalars(stmt))


def record_to_formula(record: FormulaRecord) -> Formula | None:
    """库里的记录 → 公式；存的 JSON 不合法（比如被手动改坏）时返回 None。"""
    try:
        return Formula(**{**record.body, "version": record.version, "status": record.status, "scope": "customer",
                          "customer_id": record.customer_id, "type": "sql"})
    except ValueError:
        return None


def customer_formulas(db: Session, customer_id: str | None) -> list[Formula]:
    """客户公式，每个 id 只有最新版本（可能是已停用的，公式库会自己跳过）。"""
    return [f for f in (record_to_formula(r) for r in latest_formula_records(db, customer_id)) if f is not None]


def library_for(db: Session, customer_id: str | None) -> FormulaLibrary:
    return FormulaLibrary(customer_formulas(db, customer_id))


def reserved_tool_names() -> set[str]:
    """Agent 的固定工具名。公式会被注册成同名工具，所以客户公式的 id 不能和它们重名。"""
    from sheetmate.agent.tools import reserved_tool_names as names  # 放在函数里导入，避免服务层和 Agent 层循环依赖

    return names()


_VALIDATION_HINTS = {"missing": "缺少这一项", "string_too_short": "不能为空", "string_too_long": "太长了"}


def _validation_message(exc: ValidationError) -> str:
    """pydantic 的英文校验错误 → 一句人话（接口直接被调用时才会走到，表单会先在前端校验）。"""
    parts = []
    for err in exc.errors()[:3]:
        where = ".".join(str(p) for p in err["loc"])
        if err["type"] in _VALIDATION_HINTS:
            text = _VALIDATION_HINTS[err["type"]]
        elif err["type"] == "literal_error":
            text = f"不支持「{err.get('input')}」，可选：{(err.get('ctx') or {}).get('expected', '')}"
        else:
            text = str(err["msg"]).removeprefix("Value error, ")
        parts.append(f"{where}：{text}" if where else text)
    return "公式格式不对：" + "；".join(parts)


def build_formula(customer_id: str, body: dict[str, Any], *, created_by: str = "human") -> Formula:
    """把提交的公式 JSON 变成客户公式并做完整的定义检查（保存、试运行、实时检查共用）。不合法时抛 FormulaError，消息是人话。"""
    if not isinstance(body, dict):
        raise FormulaError("公式格式不对：应该是一个对象")
    try:
        formula = Formula(**{**body, "scope": "customer", "customer_id": customer_id, "type": "sql", "created_by": created_by})
    except ValidationError as exc:
        raise FormulaError(_validation_message(exc)) from exc
    formula.check_definition()
    if formula.id in reserved_tool_names():
        raise FormulaError(f"公式 id「{formula.id}」和系统工具重名，请换一个")
    return formula


def save_formula(db: Session, customer_id: str, body: dict[str, Any], *, created_by: str = "human") -> FormulaRecord:
    """新增 / 升级客户公式：同一个 id 每保存一次升一个版本，旧版本保留。"""
    formula = build_formula(customer_id, body, created_by=created_by)
    latest = db.scalar(select(func.max(FormulaRecord.version)).where(
        FormulaRecord.customer_id == customer_id, FormulaRecord.formula_id == formula.id)) or 0
    formula = formula.model_copy(update={"version": latest + 1})
    record = FormulaRecord(
        formula_id=formula.id, version=formula.version, scope="customer", customer_id=customer_id, name=formula.name,
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
        "params": {k: {"type": p.type, "required": p.required, "default": p.default, "desc": p.desc, "values": p.values,
                       "of": p.of, "item_type": p.item_type}
                   for k, p in spec.params.items()},
        "sql": spec.sql, "example": spec.example, "triggers": spec.triggers,
        "overrides_builtin": kind == "customer" and spec.id in BUILTIN_FORMULAS,
    }
