"""文件：信息、预览、下载、删除（权限：所属客户或所属会话）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user
from sheetmate.db.database import get_db
from sheetmate.db.models import Conversation, FileRecord, User
from sheetmate.services import files as file_service
from sheetmate.services.auth import can_access_customer
from sheetmate.services.errors import Forbidden

router = APIRouter(prefix="/api/files", tags=["files"])


def _check(db: Session, user: User, record: FileRecord) -> None:
    if record.customer_id and can_access_customer(db, user, record.customer_id):
        return
    if record.conversation_id:
        conv = db.get(Conversation, record.conversation_id)
        if conv is not None and (conv.user_id == user.id or user.role == "admin"):
            return
    if record.created_by == user.id or user.role == "admin":
        return
    raise Forbidden("你没有权限访问这个文件")


@router.get("/{file_id}")
def file_info(file_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    record = file_service.get_file(db, file_id)
    _check(db, user, record)
    return {"ok": True, "file": file_service.file_dict(record)}


@router.get("/{file_id}/preview")
def file_preview(file_id: str, sheet: str | None = None, offset: int = 0, limit: int = 50,
                 db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    record = file_service.get_file(db, file_id)
    _check(db, user, record)
    return {"ok": True, "preview": file_service.preview(record, sheet, offset, min(limit, 500))}


@router.get("/{file_id}/download")
def download(file_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> FileResponse:
    record = file_service.get_file(db, file_id)
    _check(db, user, record)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(record.filename)}"}
    return FileResponse(record.stored_path, headers=headers,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@router.delete("/{file_id}")
def delete_file(file_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    record = file_service.get_file(db, file_id)
    _check(db, user, record)
    Path(record.stored_path).unlink(missing_ok=True)
    db.delete(record)
    return {"ok": True}
