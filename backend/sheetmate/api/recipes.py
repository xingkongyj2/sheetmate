"""Recipe：列表、详情、版本、编辑（存为新版本）、状态、匹配输入、用新文件执行（SSE）、删除；公式目录。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlalchemy import delete
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user, sse_response
from sheetmate.db.database import get_db
from sheetmate.db.models import RecipeRecord, User
from sheetmate.engine.formulas import BUILTIN_FORMULAS
from sheetmate.recipe.schema import Recipe, check_recipe
from sheetmate.services import customers as customer_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services import replay as replay_service
from sheetmate.services import runs as run_service
from sheetmate.services.auth import require_customer
from sheetmate.services.errors import ServiceError

router = APIRouter(prefix="/api", tags=["recipes"])


class StatusBody(BaseModel):
    status: str


class MatchBody(BaseModel):
    file_ids: list[str]


class RunBody(BaseModel):
    file_ids: list[str]
    inputs: dict[str, str] | None = None
    allow_repair: bool = True
    version: int | None = None


def _check(db: Session, user: User, recipe: Recipe) -> None:
    if recipe.customer_id:
        require_customer(db, user, recipe.customer_id)


@router.get("/recipes")
def list_recipes(customer_id: str | None = None, conversation_id: str | None = None,
                 db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    if customer_id:
        require_customer(db, user, customer_id)
    records = recipe_service.latest_records(db, customer_id=customer_id, conversation_id=conversation_id, include_deprecated=True)
    return {"ok": True, "recipes": [recipe_service.recipe_brief(r) for r in records]}


@router.get("/recipes/{recipe_id}")
def get_recipe(recipe_id: str, version: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    recipe = recipe_service.get_recipe(db, recipe_id, version)
    _check(db, user, recipe)
    runs = run_service.list_runs(db, recipe_id=recipe_id, limit=20)
    return {"ok": True, "recipe": recipe.to_dict(), "flow": recipe.flow(), "versions": recipe_service.versions(db, recipe_id),
            "runs": [run_service.run_dict(r, with_steps=False) for r in runs]}


@router.put("/recipes/{recipe_id}")
def update_recipe(recipe_id: str, body: dict[str, Any], background: BackgroundTasks, db: Session = Depends(get_db),
                  user: User = Depends(current_user)) -> dict[str, Any]:
    """编辑流程（高级）：提交完整 Recipe JSON，结构检查通过后保存为新版本。"""
    current = recipe_service.get_recipe(db, recipe_id)
    _check(db, user, current)
    data = {**body, "id": current.id, "customer_id": current.customer_id, "created_by": "human"}
    try:
        recipe = Recipe(**data)
    except ValueError as exc:
        raise ServiceError(f"流程格式不对：{exc}") from exc
    problems = check_recipe(recipe, recipe_service.library_for(db, current.customer_id))
    if problems:
        raise ServiceError("；".join(problems))
    recipe.metadata["version_note"] = body.get("metadata", {}).get("version_note") or "手动编辑"
    record = recipe_service.save_recipe(db, recipe, status=body.get("status") or current.status)
    _refresh_later(db, background, current.customer_id)
    return {"ok": True, "recipe": recipe_service.recipe_brief(record)}


@router.post("/recipes/{recipe_id}/status")
def set_status(recipe_id: str, body: StatusBody, background: BackgroundTasks, db: Session = Depends(get_db),
               user: User = Depends(current_user)) -> dict[str, Any]:
    recipe = recipe_service.get_recipe(db, recipe_id)
    _check(db, user, recipe)
    record = recipe_service.set_status(db, recipe_id, body.status)
    _refresh_later(db, background, recipe.customer_id)
    return {"ok": True, "recipe": recipe_service.recipe_brief(record)}


@router.delete("/recipes/{recipe_id}")
def delete_recipe(recipe_id: str, background: BackgroundTasks, db: Session = Depends(get_db),
                  user: User = Depends(current_user)) -> dict[str, Any]:
    recipe = recipe_service.get_recipe(db, recipe_id)
    _check(db, user, recipe)
    db.execute(delete(RecipeRecord).where(RecipeRecord.recipe_id == recipe_id))
    _refresh_later(db, background, recipe.customer_id)
    return {"ok": True}


def _refresh_later(db: Session, background: BackgroundTasks, customer_id: str | None) -> None:
    """流程变了：提交后在后台更新客户的整体处理逻辑。"""
    if customer_id:
        db.commit()
        background.add_task(customer_service.auto_refresh_summary, customer_id)


@router.post("/recipes/{recipe_id}/match")
def match_inputs(recipe_id: str, body: MatchBody, user: User = Depends(current_user)) -> dict[str, Any]:
    return {"ok": True, **replay_service.match(user, recipe_id, body.file_ids)}


@router.post("/recipes/{recipe_id}/run")
async def run_recipe(recipe_id: str, body: RunBody, user: User = Depends(current_user)):
    """用新文件重复执行流程（SSE 推送每一步的进度）。"""
    job = replay_service.start_replay(user, recipe_id, file_ids=body.file_ids, inputs=body.inputs,
                                      allow_repair=body.allow_repair, version=body.version)
    return sse_response(job)


@router.get("/formulas")
def list_formulas(customer_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    customer = []
    if customer_id:
        require_customer(db, user, customer_id)
        customer = [recipe_service.formula_dict(f, "customer") for f in recipe_service.customer_formulas(db, customer_id)]
    builtin = [recipe_service.formula_dict(item.spec, "builtin") for item in BUILTIN_FORMULAS.values()]
    return {"ok": True, "builtin": builtin, "customer": customer}
