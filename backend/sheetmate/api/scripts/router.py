"""Load script source on demand after checking its originating record's permissions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user
from sheetmate.db.session import get_db
from sheetmate.db.models import User
from sheetmate.services.scripts.service import get_script as load_script

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


@router.get("/{script_id}")
def get_script(script_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    return {"ok": True, "script": load_script(db, user, script_id)}
