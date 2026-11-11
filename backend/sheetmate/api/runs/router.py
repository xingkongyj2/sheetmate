"""运行记录：列表、详情、查看中间表（单步查看）、从某一步重跑（断点续跑）。"""

from __future__ import annotations

from sheetmate.api.runs.schemas import RerunBody

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user, sse_response
from sheetmate.core.config.settings import settings
from sheetmate.db.session import get_db
from sheetmate.db.models import Conversation, User
from sheetmate.services.workflows import replay as replay_service
from sheetmate.services.workflows import runs as run_service
from sheetmate.services.identity.auth import can_access_customer, require_customer
from sheetmate.core.errors import Forbidden, NotFound

router = APIRouter(prefix="/api/runs", tags=["runs"])


def _check(db: Session, user: User, run) -> None:
    if run.customer_id and can_access_customer(db, user, run.customer_id):
        return
    if run.conversation_id:
        conv = db.get(Conversation, run.conversation_id)
        if conv is not None and conv.user_id == user.id:
            return
    if run.user_id == user.id or user.role == "admin":
        return
    raise Forbidden("你没有权限查看这条运行记录")


@router.get("")
def list_runs(customer_id: str | None = None, recipe_id: str | None = None,
              db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    if customer_id:
        require_customer(db, user, customer_id)
    runs = [r for r in run_service.list_runs(db, customer_id=customer_id, recipe_id=recipe_id, limit=50)
            if customer_id or r.user_id == user.id or user.role == "admin"]
    return {"ok": True, "runs": [run_service.run_dict(r, with_steps=False) for r in runs]}


@router.get("/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    run = run_service.get_run(db, run_id)
    _check(db, user, run)
    has_tables = (settings.runs_dir / f"{run_id}.duckdb").exists()
    tables = []
    if has_tables:
        tables = [{"name": t.name, "label": t.label, "rows": t.rows, "cols": len(t.columns), "source": t.source}
                  for t in run_service.run_workspaces.get(run_id).list_tables()]
    return {"ok": True, "run": run_service.run_dict(run), "tables": tables}


@router.get("/{run_id}/tables/{name}")
def run_table(run_id: str, name: str, offset: int = 0, limit: int = 50,
              db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    run = run_service.get_run(db, run_id)
    _check(db, user, run)
    if not (settings.runs_dir / f"{run_id}.duckdb").exists():
        raise NotFound("这次运行的中间数据已经不在了")
    return {"ok": True, "table": run_service.run_workspaces.get(run_id).page(name, offset, min(limit, 500))}


@router.post("/{run_id}/rerun/{step_id}")
async def rerun_from(run_id: str, step_id: int, body: RerunBody, user: User = Depends(current_user)):
    """从第 step_id 步重跑（可以先改这一步的参数），复用前面已经算好的中间表。"""
    return sse_response(replay_service.start_rerun_from(user, run_id, step_id, body.params, save_recipe=body.save_recipe))
