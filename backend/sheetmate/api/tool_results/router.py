"""Authorized access to durable output pages; references are never file paths."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user
from sheetmate.db.session import get_db
from sheetmate.db.models import User
from sheetmate.services.execution.tool_results import read_tool_result

router = APIRouter(prefix="/api/conversations", tags=["tool-results"])


@router.get("/{conversation_id}/tool-results/{result_id}")
def tool_result_page(conversation_id: str, result_id: str, response: Response, section: str = "text",
                     offset: int = Query(0, ge=0), limit: int = Query(4000, ge=1, le=8000),
                     db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, **read_tool_result(db, user_id=user.id, conversation_id=conversation_id,
                                         result_id=result_id, section=section, offset=offset, limit=limit)}
