"""文件：信息、预览、下载、删除（权限：所属客户或所属会话）。"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user
from sheetmate.config import settings
from sheetmate.db.database import get_db
from sheetmate.db.models import FileRecord, User
from sheetmate.engine.reader import ReadError, read_workbook
from sheetmate.engine.sqlutil import frame_rows
from sheetmate.services import files as file_service
from sheetmate.services.errors import Forbidden, NotFound, ServiceError

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post("/preview-upload")
def preview_upload(file: UploadFile = File(...), sheet: str | None = None,
                   offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
                   user: User = Depends(current_user)) -> dict[str, Any]:
    """首页旧 XLS 的临时数据预览，不创建会话或文件记录。"""
    filename = file_service.safe_filename(file.filename or "file")
    if Path(filename).suffix.lower() != ".xls":
        raise ServiceError("临时数据预览只支持 .xls 文件")
    max_mb = min(settings.max_upload_mb, 25)
    data = file.file.read(max_mb * 1024 * 1024 + 1)
    if len(data) > max_mb * 1024 * 1024:
        raise ServiceError(f"文件超过 {max_mb} MB，请拆分后再预览")
    if not data:
        raise ServiceError("文件是空的")
    with TemporaryDirectory(prefix="sheetmate_preview_") as directory:
        path = Path(directory) / filename
        path.write_bytes(data)
        try:
            sheets = read_workbook(path)
        except ReadError as exc:
            raise ServiceError(str(exc)) from exc
        chosen = next((item for item in sheets if item.name == sheet), sheets[0]) if sheet else sheets[0]
        page = chosen.df.iloc[offset : offset + limit]
        return {"ok": True, "preview": {
            "filename": filename, "sheet": chosen.name, "sheets": [item.name for item in sheets],
            "total": int(len(chosen.df)), "offset": offset, "limit": limit,
            "columns": [str(column) for column in chosen.df.columns], "rows": frame_rows(page),
            "header": chosen.header.to_dict(),
        }}


def _check(db: Session, user: User, record: FileRecord) -> None:
    if not file_service.can_access_file(db, user, record):
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
def delete_file(file_id: str, customer_id: str | None = None, conversation_id: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    # 删除关联也适用于原件已丢失的旧记录，让用户可以清理损坏条目。
    record = db.get(FileRecord, file_id)
    if record is None:
        raise NotFound("文件不存在或已被删除")
    _check(db, user, record)
    if customer_id and conversation_id:
        raise ServiceError("请指定客户或会话中的一个删除范围")
    scope = customer_id or record.customer_id
    if conversation_id:
        deleted = file_service.remove_from_conversation(db, record, user, conversation_id)
    elif scope:
        deleted = file_service.remove_from_customer(db, record, user, scope)
    elif record.conversation_id:
        deleted = file_service.remove_from_conversation(db, record, user, record.conversation_id)
    else:
        # 引用保存在其他会话时，读取权限不代表有权删除其他会话的文件。
        deleted = file_service.delete_if_unowned(db, record)
        if not deleted:
            raise ServiceError("文件仍被会话使用，请在所属会话中管理")
    return {"ok": True, "deleted": deleted}
