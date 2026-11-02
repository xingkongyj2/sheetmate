"""Load script source on demand after checking its originating record's permissions."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user
from sheetmate.db.database import get_db
from sheetmate.db.models import User
from sheetmate.engine.script_store import read_script
from sheetmate.services.errors import NotFound
from sheetmate.services.scripts import require_script_access

router = APIRouter(prefix="/api/scripts", tags=["scripts"])


@router.get("/{script_id}")
def get_script(script_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    require_script_access(db, user, script_id)
    try:
        script = read_script(script_id)
    except (OSError, ValueError, KeyError) as exc:
        raise NotFound("脚本文件不存在或已损坏") from exc
    return {"ok": True, "script": script}
