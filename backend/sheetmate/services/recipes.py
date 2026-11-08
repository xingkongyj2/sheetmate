"""Recipe 与客户公式的存取。

- Recipe 按版本保存：同一个 recipe_id 改一次升一版，旧版本保留（历史运行可以追溯到当时的版本）
- 客户公式：scope=customer，同 id 覆盖内置公式
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import String, cast, delete, func, select, update
from sqlalchemy.orm import Session

from sheetmate.db.models import Conversation, Customer, FormulaRecord, Message, RecipeProfile, RecipeRecord, Run, User, now
from sheetmate.engine.formulas import BUILTIN_FORMULAS, Formula, FormulaError, FormulaLibrary
from sheetmate.engine.script_references import bind_script_references, externalize_script_references
from sheetmate.recipe.schema import Recipe
from sheetmate.services.auth import require_customer
from sheetmate.services.errors import Forbidden, NotFound


# ---------------------------------------------------------------------------
# Recipe
# ---------------------------------------------------------------------------


def save_recipe(db: Session, recipe: Recipe, *, conversation_id: str | None = None) -> RecipeRecord:
    """保存为新版本（recipe_id 不存在时就是第 1 版）。"""
    latest = db.scalar(select(func.max(RecipeRecord.version)).where(RecipeRecord.recipe_id == recipe.id)) or 0
    previous_mapping = (get_record(db, recipe.id).body or {}).get("field_mapping", {}) if latest else {}
    recipe.version = latest + 1
    recipe.updated_at = now().isoformat()
    record = RecipeRecord(
        recipe_id=recipe.id, version=recipe.version, name=recipe.name, scope=recipe.scope, customer_id=recipe.customer_id,
        conversation_id=conversation_id or (recipe.learned_from or {}).get("conversation_id"),
        created_by=recipe.created_by, body=recipe.to_dict(),
    )
    db.add(record)
    db.flush()
    if record.customer_id:
        customer = db.get(Customer, record.customer_id)
        if customer is not None:
            ensure_primary_recipe(db, customer)
            profile = ensure_profile(db, record)
            if latest:
                from sheetmate.services.memory import upsert_field_mapping
                for standard, aliases in recipe.field_mapping.items():
                    additions = [alias for alias in aliases if alias not in previous_mapping.get(standard, [])]
                    if additions:
                        upsert_field_mapping(db, record.customer_id, standard, additions,
                                             source="ai", recipe_id=record.recipe_id)
            if profile.summary_by != "human":
                profile.summary = ""
                profile.summary_updated_at = None
    _bind_scripts(record)
    return record


def _definition(recipe: Recipe) -> dict[str, Any]:
    """忽略运行时间和校验摘要，比较用户保存的处理定义。"""
    body = recipe.to_dict()
    definition = {key: body.get(key) for key in (
        "name", "description", "scope", "customer_id", "inputs", "field_mapping", "steps", "output_spec", "validation_rules",
    )}
    for step in definition["steps"]:
        for key in ("sql", "confidence", "explanation"):
            step.pop(key, None)
    for rule in definition["validation_rules"]:
        rule.pop("id", None)
    definition["execution"] = {key: body["metadata"].get(key) for key in (
        "explicit_outputs", "has_expected", "validation_checkpoints",
    )}
    return definition


def save_recipe_if_changed(db: Session, recipe: Recipe, *, conversation_id: str) -> RecipeRecord:
    """同一会话重复保存相同流程时复用记录，实际修改才增加版本。"""
    definition = _definition(recipe)
    candidates = latest_records(db, conversation_id=conversation_id)
    same_id = next((record for record in candidates if record.recipe_id == recipe.id), None)
    # 客户的既有流程可以在另一个会话中更新，但不与其他流程按名称合并。
    if same_id is None:
        same_id = db.scalars(select(RecipeRecord).where(RecipeRecord.recipe_id == recipe.id)
                             .order_by(RecipeRecord.version.desc()).limit(1)).first()
    matches = [same_id] if same_id is not None else [record for record in candidates if record.name == recipe.name]
    for record in matches:
        saved = record_to_recipe(record)
        if same_id is None and saved.learned_from.get("input_bindings") != recipe.learned_from.get("input_bindings"):
            continue
        if _definition(saved) == definition:
            recipe.id, recipe.version = record.recipe_id, record.version
            recipe.updated_at = (record.body or {}).get("updated_at", recipe.updated_at)
            return record
    return save_recipe(db, recipe, conversation_id=conversation_id)


def strip_recipe_statuses(value: Any, *, recipe: bool = False) -> Any:
    """移除旧流程快照和消息事件中的状态，保留公式、步骤和执行状态。"""
    if isinstance(value, list):
        return [strip_recipe_statuses(item, recipe=recipe) for item in value]
    if not isinstance(value, dict):
        return value
    item = {key: strip_recipe_statuses(field, recipe=key in {"recipe", "recipe_snapshot"})
            for key, field in value.items()}
    is_recipe = recipe or item.get("type") in {"recipe_saved", "recipe_view"} or (
        "steps" in item and "inputs" in item and "output_spec" in item)
    if is_recipe:
        item.pop("status", None)
        item.pop("status_label", None)
    return item


def migrate_legacy_recipe_statuses(db: Session) -> None:
    """清理历史流程状态，不创建流程或版本；重复启动可安全执行。"""
    for record in db.scalars(select(RecipeRecord).where(cast(RecipeRecord.body, String).contains('"status'))):
        body = strip_recipe_statuses(record.body, recipe=True)
        if body != record.body:
            # 字段清理不代表用户修改流程，保留原更新时间和列表顺序。
            db.execute(update(RecipeRecord).where(RecipeRecord.id == record.id)
                       .values(body=body, updated_at=record.updated_at))
    for run in db.scalars(select(Run).where(cast(Run.validation, String).contains('"status'))):
        validation = strip_recipe_statuses(run.validation)
        if validation != run.validation:
            run.validation = validation
    for message in db.scalars(select(Message).where(cast(Message.events, String).contains('"status'))):
        events = strip_recipe_statuses(message.events)
        if events != message.events:
            message.events = events


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
    data = strip_recipe_statuses(record.body or {}, recipe=True)
    data.update({"id": record.recipe_id, "version": record.version,
                 "customer_id": record.customer_id, "scope": record.scope})
    recipe = Recipe(**data)
    body = recipe.to_dict()
    if record.body != body:
        record.body = body
    _bind_scripts(record)
    return recipe


def latest_records(db: Session, *, customer_id: str | None = None, conversation_id: str | None = None) -> list[RecipeRecord]:
    """每个 recipe_id 只取最新版本。"""
    sub = select(RecipeRecord.recipe_id, func.max(RecipeRecord.version).label("v")).group_by(RecipeRecord.recipe_id).subquery()
    stmt = select(RecipeRecord).join(sub, (RecipeRecord.recipe_id == sub.c.recipe_id) & (RecipeRecord.version == sub.c.v))
    if customer_id is not None:
        stmt = stmt.where(RecipeRecord.customer_id == customer_id)
    if conversation_id is not None:
        stmt = stmt.where(RecipeRecord.conversation_id == conversation_id)
    return list(db.scalars(stmt.order_by(RecipeRecord.updated_at.desc())))


def ensure_primary_recipe(db: Session, customer: Customer) -> str | None:
    """旧客户也按首次创建顺序选主流程，修改版本不会改变选择。"""
    ids = list(db.scalars(select(RecipeRecord.recipe_id).where(RecipeRecord.customer_id == customer.id)
                         .group_by(RecipeRecord.recipe_id)
                         .order_by(func.min(RecipeRecord.created_at), func.min(RecipeRecord.id))))
    if customer.primary_recipe_id not in ids:
        customer.primary_recipe_id = ids[0] if ids else None
        db.flush()
    if customer.primary_recipe_id:
        from sheetmate.services.memory import migrate_customer_context
        migrate_customer_context(db, customer.id, customer.primary_recipe_id)
    return customer.primary_recipe_id


def resolve_customer_recipe(db: Session, customer_id: str, recipe_id: str | None = None) -> RecipeRecord | None:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise NotFound("客户不存在")
    primary = ensure_primary_recipe(db, customer)
    selected = recipe_id or primary
    if not selected:
        return None
    record = get_record(db, selected)
    if record.customer_id != customer_id:
        raise NotFound("这个流程不属于当前客户")
    return record


def ensure_profile(db: Session, record: RecipeRecord) -> RecipeProfile:
    profile = db.get(RecipeProfile, record.recipe_id)
    if profile is None:
        profile = RecipeProfile(recipe_id=record.recipe_id, customer_id=record.customer_id,
                                summary="", summary_by="ai")
        db.add(profile)
        db.flush()
        from sheetmate.services.memory import upsert_field_mapping
        for standard, aliases in (record.body or {}).get("field_mapping", {}).items():
            if isinstance(aliases, list) and standard.strip() and aliases:
                upsert_field_mapping(db, record.customer_id, standard, aliases,
                                     source="ai", recipe_id=record.recipe_id)
        # 客户只有一个流程时，旧说明可以原样归入该流程。
        customer = db.get(Customer, record.customer_id)
        count = db.scalar(select(func.count(func.distinct(RecipeRecord.recipe_id)))
                          .where(RecipeRecord.customer_id == record.customer_id))
        if customer and count == 1 and customer.summary:
            profile.summary = customer.summary
            profile.summary_by = customer.summary_by
            profile.summary_policy_version = customer.summary_policy_version
            profile.summary_updated_at = customer.summary_updated_at
    elif profile.customer_id != record.customer_id:
        profile.customer_id = record.customer_id
    return profile


def versions(db: Session, recipe_id: str) -> list[dict[str, Any]]:
    records = db.scalars(select(RecipeRecord).where(RecipeRecord.recipe_id == recipe_id).order_by(RecipeRecord.version.desc()))
    return [
        {"version": r.version,
         "created_at": r.created_at.isoformat(), "steps": len((r.body or {}).get("steps", [])),
         "note": (r.body or {}).get("metadata", {}).get("version_note", "")}
        for r in records
    ]


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
    if record.customer_id:
        customer = db.get(Customer, record.customer_id)
        if customer is not None:
            ensure_primary_recipe(db, customer)
            db.execute(update(Conversation).where(Conversation.customer_id == record.customer_id,
                                                   Conversation.recipe_id == recipe_id)
                       .values(recipe_id=customer.primary_recipe_id))
    return deleted


def assign_customer(db: Session, *, conversation_id: str, customer_id: str) -> int:
    """按流程身份整体迁移，版本、资料、记忆和执行历史保持在同一客户下。"""
    from sheetmate.db.models import FileRecipe, FileRecord, Memory, RecipeFieldMapping
    from sheetmate.services import files as file_service

    recipe_ids = set(db.scalars(select(RecipeRecord.recipe_id).where(RecipeRecord.conversation_id == conversation_id)))
    if not recipe_ids:
        return 0
    records = list(db.scalars(select(RecipeRecord).where(RecipeRecord.recipe_id.in_(recipe_ids))))
    previous_customers = {record.customer_id for record in records if record.customer_id and record.customer_id != customer_id}
    file_ids: set[str] = set()
    for previous_id in previous_customers:
        previous = db.get(Customer, previous_id)
        if previous is not None:
            ensure_primary_recipe(db, previous)
            db.execute(update(Conversation).where(Conversation.customer_id == previous_id,
                                                   Conversation.recipe_id.in_(recipe_ids))
                       .values(recipe_id=previous.primary_recipe_id))
        for recipe_id in recipe_ids:
            file_ids.update(file_service._recipe_file_ids(db, previous_id, recipe_id))
    for link in db.scalars(select(FileRecipe).where(FileRecipe.recipe_id.in_(recipe_ids))):
        file_ids.add(link.file_id)
        link.customer_id = customer_id
    transferred_runs = list(db.scalars(select(Run).where(Run.recipe_id.in_(recipe_ids))))
    for run in transferred_runs:
        run.customer_id = customer_id
        file_ids.update(run.input_file_ids or [])
        if run.output_file_id:
            file_ids.add(run.output_file_id)
        snapshot = (run.validation or {}).get("recipe_snapshot")
        if isinstance(snapshot, dict):
            run.validation = {**run.validation, "recipe_snapshot": {**snapshot, "customer_id": customer_id}}
    if transferred_runs:
        file_ids.update(db.scalars(select(FileRecord.id).where(FileRecord.run_id.in_([run.id for run in transferred_runs]))))
    for model in (Memory, RecipeFieldMapping):
        for item in db.scalars(select(model).where(model.recipe_id.in_(recipe_ids))):
            item.customer_id = customer_id
    for file in db.scalars(select(FileRecord).where(FileRecord.id.in_(file_ids))):
        file_service.retain_for_customer(db, file, customer_id)
    for record in records:
        record.customer_id = customer_id
        body = dict(record.body or {})
        body["customer_id"] = customer_id
        record.body = body
        ensure_profile(db, record)
    db.flush()
    customer = db.get(Customer, customer_id)
    if customer is not None:
        ensure_primary_recipe(db, customer)
    for previous_id in previous_customers:
        previous = db.get(Customer, previous_id)
        if previous is not None:
            ensure_primary_recipe(db, previous)
    return len(recipe_ids)


def recipe_brief(record: RecipeRecord) -> dict[str, Any]:
    body = strip_recipe_statuses(externalize_script_references(record.body or {}), recipe=True)
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
