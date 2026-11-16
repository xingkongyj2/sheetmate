"""文件：信息、预览、下载、删除（权限：所属客户或所属会话）。"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user
from sheetmate.db.session import get_db
from sheetmate.db.models import FileRecord, User
from sheetmate.services.customers import service as customer_service
from sheetmate.services.files import service as file_service
from sheetmate.services.files import inspection
from sheetmate.services.privacy import masked_copy
from sheetmate.services.privacy import service as privacy_service
from sheetmate.services.workflows import node_edit as node_edit_service
from sheetmate.core.errors import Forbidden, NotFound, ServiceError

router = APIRouter(prefix="/api/files", tags=["files"])


class PrivacyStatusBody(BaseModel):
    ids: list[str]


class RenameFileBody(BaseModel):
    filename: str
    customer_id: str | None = None
    recipe_id: str | None = None  # 改输出文件的名字时必填：名字记录在这个流程里


@router.post("/preview-upload")
def preview_upload(file: UploadFile = File(...), sheet: str | None = None,
                   offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=500),
                   raw: bool = False,
                   user: User = Depends(current_user)) -> dict[str, Any]:
    """首页文件临时预览，不创建会话或持久化文件记录。"""
    preview = inspection.preview_upload(file.file, filename=file.filename, sheet=sheet,
                                        offset=offset, limit=limit, raw=raw)
    return {"ok": True, "preview": preview}


@router.post("/privacy-status")
def privacy_status(body: PrivacyStatusBody, user: User = Depends(current_user)) -> dict[str, Any]:
    """文件列表里每个文件的脱敏状态（已识别 / 识别中 / 识别失败 / 还没识别）；只返回当前用户能访问的表格文件。"""
    return {"ok": True, "status": privacy_service.file_statuses(user, body.ids[:200])}


def _check(db: Session, user: User, record: FileRecord) -> None:
    if not file_service.can_access_file(db, user, record):
        raise Forbidden("你没有权限访问这个文件")


@router.get("/{file_id}")
def file_info(file_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    record = file_service.get_file(db, file_id)
    _check(db, user, record)
    return {"ok": True, "file": file_service.file_dict(record)}


@router.patch("/{file_id}")
def rename_file(file_id: str, body: RenameFileBody, background: BackgroundTasks, db: Session = Depends(get_db),
                user: User = Depends(current_user)) -> dict[str, Any]:
    """改文件名：输入文件同步流程里记录的名字，输出文件同步流程里声明的名字；只改名字，不重跑。"""
    result = node_edit_service.rename_file(db, user, file_id, body.filename, customer_id=body.customer_id, recipe_id=body.recipe_id)
    owners = {item["customer_id"] for item in result["recipes"] if item["customer_id"]}
    if owners:
        db.commit()  # 后台任务要读到刚保存的新版本
        for owner in owners:
            background.add_task(customer_service.auto_refresh_summary, owner)
    return {"ok": True, **result}


@router.get("/{file_id}/privacy-preview")
async def privacy_preview(file_id: str, sheet: str | None = None, offset: int = Query(0, ge=0),
                          limit: int = Query(50, ge=1, le=200), conversation_id: str | None = None,
                          db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """脱敏副本的在线查看：AI 实际看到的样子（被保护的列显示为令牌）。"""
    _check(db, user, file_service.get_file(db, file_id))
    preview = await masked_copy.masked_preview(user, file_id, sheet=sheet, offset=offset, limit=limit,
                                               conversation_id=conversation_id)
    return {"ok": True, "preview": preview}


@router.get("/{file_id}/privacy-copy")
async def privacy_copy(file_id: str, conversation_id: str | None = None, db: Session = Depends(get_db),
                       user: User = Depends(current_user)) -> Response:
    """下载脱敏副本（xlsx）：和在线查看是同一份内容，每次下载都会写进脱敏日志。"""
    _check(db, user, file_service.get_file(db, file_id))
    filename, content = await masked_copy.masked_xlsx(user, file_id, conversation_id=conversation_id)
    return Response(content, media_type=masked_copy.XLSX,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}", "Cache-Control": "no-store"})


@router.get("/{file_id}/preview")
def file_preview(file_id: str, sheet: str | None = None, offset: int = Query(0, ge=0),
                 limit: int = Query(50, ge=1, le=500), raw: bool = False,
                 db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    record = file_service.get_file(db, file_id)
    _check(db, user, record)
    return {"ok": True, "preview": inspection.preview_file(record, sheet=sheet, offset=offset, limit=limit, raw=raw)}


@router.get("/{file_id}/download")
def download(file_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> FileResponse:
    record = file_service.get_file(db, file_id)
    _check(db, user, record)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(record.filename)}"}
    return FileResponse(record.stored_path, headers=headers,
                        media_type=file_service.file_media_type(record))


@router.get("/{file_id}/image")
def image_preview(file_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> FileResponse:
    record = file_service.get_file(db, file_id)
    _check(db, user, record)
    media_type = inspection.image_media_type(record)
    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{quote(record.filename)}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    return FileResponse(record.stored_path, headers=headers, media_type=media_type)


@router.delete("/{file_id}")
def delete_file(file_id: str, customer_id: str | None = None, conversation_id: str | None = None,
                recipe_id: str | None = None,
                db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    # 删除关联也适用于原件已丢失的旧记录，让用户可以清理损坏条目。
    record = db.get(FileRecord, file_id)
    if record is None:
        raise NotFound("文件不存在或已被删除")
    _check(db, user, record)
    if customer_id and conversation_id:
        raise ServiceError("请指定客户或会话中的一个删除范围")
    scope = customer_id or record.customer_id
    if recipe_id:
        if not scope or conversation_id:
            raise ServiceError("流程文件需要指定客户")
        deleted = file_service.remove_from_recipe(db, record, user, scope, recipe_id)
    elif conversation_id:
        deleted = file_service.remove_from_workspace(db, record, user, conversation_id)
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
