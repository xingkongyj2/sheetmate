"""Authenticated operational statistics for the current user."""
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user
from sheetmate.db.session import get_db
from sheetmate.db.models import User
from sheetmate.services.observability.metrics import execution_metrics

router = APIRouter(prefix="/api/observability", tags=["observability"])


@router.get("/executions")
def get_execution_metrics(response: Response, days: int = Query(default=30, ge=1, le=90),
                          db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, **execution_metrics(db, user, days=days)}
