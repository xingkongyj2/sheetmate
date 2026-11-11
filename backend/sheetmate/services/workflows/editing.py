"""Validate the exact workflow node selected for a conversational revision."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sheetmate.core.errors import Conflict, Forbidden, NotFound, ServiceError
from sheetmate.db.models import Conversation, RecipeRecord, Run, User
from sheetmate.recipe.definition.schema import Recipe
from sheetmate.services.workflows import service
from sheetmate.services.workflows.contracts import RecipeEditBody


def assert_pending_base(pending: Recipe, saved: Recipe | None) -> None:
    """Saved versions and temporary revision numbers must not mask a fork."""
    same = (saved is not None and service._definition(pending) == service._definition(saved)
            and pending.learned_from.get("input_bindings") == saved.learned_from.get("input_bindings"))
    if same:
        return
    baseline = pending.metadata.get("revision_saved_version", pending.metadata.get("revision_base_version"))
    current = saved.version if saved is not None else 0
    if baseline is not None and baseline != current:
        raise Conflict("这个临时修订基于的正式流程已变化，请从流程页面打开最新版本后重新修改。")


def resolve_edit_recipe(db: Session, user: User, conv: Conversation, recipe_id: str) -> tuple[Recipe, RecipeRecord | None, str | None]:
    if conv.recipe_id and conv.recipe_id != recipe_id:
        raise Conflict("当前对话绑定了其他流程，请从目标流程打开对话修改。")
    record = db.scalars(select(RecipeRecord).where(RecipeRecord.recipe_id == recipe_id)
                        .order_by(RecipeRecord.version.desc()).limit(1)).first()
    saved = None
    if record is not None:
        if (record.customer_id != conv.customer_id or
                (not conv.customer_id and record.conversation_id != conv.id)):
            raise Forbidden("这个流程不属于当前客户或会话。")
        saved = service.record_to_recipe(record)
        service.require_recipe_access(db, user, saved)
    # Only completed runs may serve as the basis of another edit. A failed
    # adaptation must never replace the last usable workflow in the dialogue.
    for run in db.scalars(select(Run).where(Run.conversation_id == conv.id, Run.recipe_id == recipe_id,
                                            Run.status.in_(("success", "warning")))
                          .order_by(Run.finished_at.desc(), Run.created_at.desc())):
        validation = run.validation or {}
        snapshot = validation.get("recipe_snapshot")
        if (validation.get("recipe_deleted") or validation.get("workflow_save_error")
                or validation.get("revision_save_conflict") or not isinstance(snapshot, dict)):
            continue
        pending = Recipe.model_validate(snapshot)
        if pending.customer_id != conv.customer_id:
            continue
        if saved is None or pending.version >= saved.version:
            assert_pending_base(pending, saved)
            return pending, record, run.id
        break
    if saved is not None:
        return saved, record, None
    raise NotFound("流程不存在，或尚未成功运行。")


def validate_recipe_edit(db: Session, user: User, conv: Conversation, payload: dict[str, Any] | None) -> dict[str, Any]:
    if not payload:
        return {}
    # Service callers receive the same strict validation as HTTP callers.
    edit = RecipeEditBody.model_validate({key: payload[key] for key in ("recipe_id", "version", "target", "operation") if key in payload})
    data = edit.model_dump(exclude_none=True)
    recipe, record, run_id = resolve_edit_recipe(db, user, conv, edit.recipe_id)
    if recipe.version != edit.version:
        raise Conflict(f"流程已更新到 v{recipe.version}，请刷新流程后重新选择要修改的对象。")
    target = data["target"]
    kind = target["kind"]
    if kind == "workflow":
        if edit.operation != "modify" or set(target) != {"kind"}:
            raise ServiceError("整个流程请使用对话修改；新增或移除请选择具体输入、输出。")
    elif kind == "step":
        step = next((step for step in recipe.steps if step.id == target.get("step_id")), None)
        if step is None or edit.operation != "modify":
            raise ServiceError("所选步骤不存在，请刷新流程后重试。")
        target = {"kind": "step", "step_id": step.id, "name": step.name}
    elif kind == "input":
        if edit.operation == "add":
            target = {"kind": "input"}
        else:
            aliases = list(dict.fromkeys(target.get("aliases", [])))
            if not aliases or not set(aliases) <= set(recipe.input_aliases()):
                raise ServiceError("所选输入文件已变化，请刷新流程后重试。")
            inputs = [item for item in recipe.inputs if item.alias in aliases]
            target = {"kind": "input", "aliases": aliases,
                      "name": "、".join(dict.fromkeys(item.source_file or item.alias for item in inputs))}
    elif edit.operation == "add":
        target = {"kind": "output"}
    else:
        flow = recipe.flow()
        candidates = flow["output_files"] if target.get("sources") else flow["outputs"] + flow["output_files"]
        selectors = {key: target[key] for key in ("name", "source", "sources") if target.get(key)}
        matches = [item for item in candidates if selectors and all(
            (set(item.get(key, [])) == set(value) if key == "sources" else item.get(key) == value)
            for key, value in selectors.items())]
        if not target.get("sources") and any(item in flow["outputs"] for item in matches):
            matches = [item for item in matches if item in flow["outputs"]]
        if len(matches) != 1:
            raise ServiceError("所选输出结果已变化，请刷新流程后重试。")
        target = {"kind": "output", **{key: matches[0][key] for key in ("name", "source", "sources") if matches[0].get(key)}}
    saved = (record is not None and record.version == recipe.version
             and service._definition(service.record_to_recipe(record)) == service._definition(recipe)
             and (record.body or {}).get("learned_from", {}).get("input_bindings", {}) == recipe.learned_from.get("input_bindings", {}))
    return {**data, "target": target, "saved": saved, "saved_version": record.version if record else 0,
            "recipe_name": recipe.name, "base_run_id": run_id}
