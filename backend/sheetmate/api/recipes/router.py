"""Recipe：列表、详情、版本、编辑（存为新版本）、匹配输入、用新文件执行（SSE）、删除；公式目录。"""

from __future__ import annotations

from sheetmate.api.recipes.schemas import MatchBody, RunBody

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user, sse_response
from sheetmate.db.session import get_db
from sheetmate.db.models import User
from sheetmate.services.customers import service as customer_service
from sheetmate.services.workflows import service as recipe_service
from sheetmate.services.workflows import replay as replay_service
from sheetmate.services.workflows import runs as run_service
from sheetmate.services.identity.auth import require_customer

router = APIRouter(prefix="/api", tags=["recipes"])


@router.get("/recipes")
def list_recipes(customer_id: str | None = None, conversation_id: str | None = None,
                 db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    if customer_id:
        require_customer(db, user, customer_id)
    records = recipe_service.latest_records(db, customer_id=customer_id, conversation_id=conversation_id)
    return {"ok": True, "recipes": [recipe_service.recipe_brief(r) for r in records]}


@router.get("/recipes/{recipe_id}")
def get_recipe(recipe_id: str, version: int | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    recipe = recipe_service.get_recipe(db, recipe_id, version)
    recipe_service.require_recipe_access(db, user, recipe)
    runs = run_service.list_runs(db, recipe_id=recipe_id, limit=20)
    return {"ok": True, "recipe": recipe.to_dict(), "flow": recipe.flow(), "versions": recipe_service.versions(db, recipe_id),
            "runs": [run_service.run_dict(r, with_steps=False) for r in runs]}


@router.put("/recipes/{recipe_id}")
def update_recipe(recipe_id: str, body: dict[str, Any], background: BackgroundTasks, db: Session = Depends(get_db),
                  user: User = Depends(current_user)) -> dict[str, Any]:
    """编辑流程：结构检查通过后保存为新版本。"""
    record = recipe_service.update_recipe(db, user, recipe_id, body)
    _refresh_later(db, background, record.customer_id)
    return {"ok": True, "recipe": recipe_service.recipe_brief(record)}


@router.delete("/recipes/{recipe_id}")
def delete_recipe(recipe_id: str, background: BackgroundTasks, db: Session = Depends(get_db),
                  user: User = Depends(current_user)) -> dict[str, Any]:
    deleted = recipe_service.delete_recipe(db, recipe_id, user=user)
    _refresh_later(db, background, deleted["customer_id"])
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
                                      allow_repair=body.allow_repair, version=body.version, save_recipe=body.save_recipe)
    return sse_response(job)


@router.get("/formulas")
def list_formulas(customer_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """当前用户可以访问的内置与客户公式目录。"""
    return {"ok": True, **recipe_service.formula_catalog(db, user, customer_id)}
