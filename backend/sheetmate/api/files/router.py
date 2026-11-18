"""文件：信息、预览、下载、删除（权限：所属客户或所属会话）。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.background import BackgroundTask
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user
from sheetmate.db.session import get_db
from sheetmate.db.models import FileRecord, User
from sheetmate.services.customers import service as customer_service
from sheetmate.services.files import service as file_service
from sheetmate.services.files import inspection
from sheetmate.services.privacy import columns as privacy_columns_service
from sheetmate.services.privacy import masked_copy
from sheetmate.services.privacy import service as privacy_service
from sheetmate.services.privacy.workspace import restored_download
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


class PrivacyColumnChange(BaseModel):
    sheet: str
    column: str
    choice: str | None = None      # mask 脱敏 / keep 不脱敏 / 空 = 恢复自动识别
    kind: str | None = None        # phone 电话号码 / address 详细地址 / custom 通用（手动选脱敏时；不传：自动识别的列照自动结果，其余用通用）


class PrivacyColumnsBody(BaseModel):
    changes: list[PrivacyColumnChange]
    conversation_id: str | None = None


@router.get("/{file_id}/privacy-columns")
async def privacy_columns(file_id: str, conversation_id: str | None = None, db: Session = Depends(get_db),
                          user: User = Depends(current_user)) -> dict[str, Any]:
    """脱敏维护页：每一列现在是否脱敏、为什么（规则 + 脱敏模型分析列头和内容的结果，加上用户手动改的）。"""
    _check(db, user, file_service.get_file(db, file_id))
    return {"ok": True, "columns": await privacy_columns_service.list_columns(user, file_id, conversation_id)}


@router.put("/{file_id}/privacy-columns")
async def save_privacy_columns(file_id: str, body: PrivacyColumnsBody, db: Session = Depends(get_db),
                               user: User = Depends(current_user)) -> dict[str, Any]:
    """手动改某些列的脱敏（脱敏 / 不脱敏 / 恢复自动）；下一轮对话开始时这个文件在对话里的表会按新的选择重新生成。"""
    _check(db, user, file_service.get_file(db, file_id))
    return {"ok": True, "columns": await privacy_columns_service.save_columns(
        user, file_id, [change.model_dump() for change in body.changes], body.conversation_id)}


@router.get("/{file_id}/privacy-copy")
async def privacy_copy(file_id: str, conversation_id: str | None = None, db: Session = Depends(get_db),
                       user: User = Depends(current_user)) -> Response:
    """下载脱敏副本（xlsx）：和在线查看是同一份内容，每次下载都会写进脱敏日志。"""
    _check(db, user, file_service.get_file(db, file_id))
    filename, content = await masked_copy.masked_xlsx(user, file_id, conversation_id=conversation_id)
    return Response(content, media_type=masked_copy.XLSX,
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}", "Cache-Control": "no-store"})


@router.get("/{file_id}/preview")
async def file_preview(file_id: str, sheet: str | None = None, offset: int = Query(0, ge=0),
                       limit: int = Query(50, ge=1, le=500), raw: bool = False,
                       db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    record = file_service.get_file(db, file_id)
    _check(db, user, record)
    path, mode = await privacy_columns_service.viewable_path(user, record)    # 查看到的一律是令牌，不换回真实值（只有下载才还原）
    preview = await asyncio.to_thread(inspection.preview_file, record, sheet=sheet, offset=offset, limit=limit, raw=raw, path=path)
    return JSONResponse({"ok": True, "preview": jsonable_encoder(preview)},
                        headers={"Cache-Control": "no-store"})


@router.get("/{file_id}/content")
async def content(file_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> FileResponse:
    """在线查看用的文件内容（预览页的表格查看器读这个）：和存下来的一致，脱敏的列里是令牌，不还原。下载走 /download。"""
    record = file_service.get_file(db, file_id)
    _check(db, user, record)
    source, mode = await privacy_columns_service.viewable_path(user, record)
    filename = record.filename
    if mode == "copy" and source.suffix.lower() != Path(filename).suffix.lower():
        filename = f"{Path(filename).stem}{source.suffix}"                     # .xls 的令牌版是 .xlsx
    headers = {"Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}", "Cache-Control": "no-store"}
    return FileResponse(source, headers=headers, media_type=file_service.file_media_type(record))


@router.get("/{file_id}/download")
async def download(file_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> FileResponse:
    """下载一律是真实内容：存下来的文件（上传时脱敏的、系统生成的结果）里的令牌在这里统一还原。
    xls 这类没法原地改的文件，原文件本来就是真实内容。"""
    record = file_service.get_file(db, file_id)
    _check(db, user, record)
    privacy_service.start()                                                    # 还原要用到本机的令牌库
    # 脱敏状态随时会变（开关、手动选择）：浏览器不能缓存，每次都取最新的
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(record.filename)}", "Cache-Control": "no-store"}
    path, temporary = await asyncio.to_thread(restored_download, Path(record.stored_path), record.id)
    return FileResponse(path, headers=headers, media_type=file_service.file_media_type(record),
                        background=BackgroundTask(path.unlink, missing_ok=True) if temporary else None)


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
