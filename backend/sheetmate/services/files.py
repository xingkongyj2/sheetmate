"""文件：上传、画像、预览、加载、导出，以及独立的客户/会话引用。

路径仅代表最初存放位置，不决定归属。客户和会话共享原件，最后一个引用解除后才回收。
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any

import openpyxl
from sqlalchemy import event, or_, select
from sqlalchemy.orm import Session

from sheetmate.config import settings
from sheetmate.db.models import Conversation, FileConversation, FileCustomer, FileRecord, User
from sheetmate.engine.file_copies import temporary_file_copies
from sheetmate.engine.images import IMAGE_SUFFIXES, image_profile
from sheetmate.engine.profile import profile_workbook
from sheetmate.engine.reader import SUPPORTED_SUFFIXES, ReadError, SheetData, read_workbook
from sheetmate.engine.sqlutil import frame_rows, sanitize_table_name
from sheetmate.engine.workspace import TableInfo, Workspace
from sheetmate.engine.writer import SheetSpec, write_excel
from sheetmate.recipe.schema import file_pattern_from_name
from sheetmate.services.auth import can_access_customer, require_customer
from sheetmate.services.errors import Forbidden, NotFound, ServiceError

logger = logging.getLogger(__name__)


@event.listens_for(Session, "after_commit")
def _remove_deleted_files(db: Session) -> None:
    for path in db.info.pop("deleted_file_paths", set()):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("无法清理已解除所有关联的文件 %s", path, exc_info=True)


@event.listens_for(Session, "after_rollback")
def _cancel_deleted_files(db: Session) -> None:
    db.info.pop("deleted_file_paths", None)

KIND_LABELS = {"input": "输入文件", "expected": "期望结果", "output": "生成结果", "doc": "处理说明"}
_UNSAFE = re.compile(r"[^\w一-鿿.\-()（） ]+")
_COPY_SUFFIX = re.compile(r"^(.*?)[（(]([1-9]\d*)[）)]$")


def safe_filename(name: str) -> str:
    """去掉路径和危险字符，只保留文件名本身。"""
    base = Path(str(name or "file")).name
    base = _UNSAFE.sub("_", base).strip(" .") or "file"
    return base[-120:]


def unique_filename(db: Session, filename: str, *, customer_id: str | None = None,
                    conversation_id: str | None = None, user_id: int | None = None,
                    exclude_file_id: str | None = None) -> str:
    """当前可见范围内重名时，在扩展名前依次追加（1）、（2），不覆盖原文件。"""
    name = safe_filename(filename)
    customer_ids = {customer_id} - {None}
    conversation_ids = {conversation_id} - {None}
    if exclude_file_id:
        existing = db.get(FileRecord, exclude_file_id)
        if existing is not None:
            customer_ids.update(db.scalars(select(FileCustomer.customer_id).where(FileCustomer.file_id == existing.id)))
            conversation_ids.update(db.scalars(select(FileConversation.conversation_id).where(FileConversation.file_id == existing.id)))
            if existing.customer_id:
                customer_ids.add(existing.customer_id)
            if existing.conversation_id:
                conversation_ids.add(existing.conversation_id)
    if conversation_ids:
        customer_ids.update(owner for owner in db.scalars(select(Conversation.customer_id).where(
            Conversation.id.in_(conversation_ids))) if owner)
    if customer_ids:
        # 客户移除过的文件可能仍在旧会话中；新客户文件也会进入这些会话的可见范围。
        conversation_ids.update(db.scalars(select(Conversation.id).where(Conversation.customer_id.in_(customer_ids))))
    conditions = [customer_file_condition(owner) for owner in customer_ids]
    if conversation_ids:
        linked = select(FileConversation.file_id).where(FileConversation.conversation_id.in_(conversation_ids))
        conditions.extend([FileRecord.conversation_id.in_(conversation_ids), FileRecord.id.in_(linked)])
    if not conditions:
        conditions.append(FileRecord.created_by == user_id)
    query = select(FileRecord.filename).where(or_(*conditions))
    if exclude_file_id:
        query = query.where(FileRecord.id != exclude_file_id)
    # 会话在同一个事务里可登记多个结果，先刷入前面的记录，后面的文件名才能正确顺延。
    db.flush()
    occupied = {item.casefold() for item in db.scalars(query)}
    if name.casefold() not in occupied:
        return name
    suffix = Path(name).suffix
    stem = name[:-len(suffix)] if suffix else name
    numbered = _COPY_SUFFIX.fullmatch(stem)
    number = int(numbered[2]) + 1 if numbered else 1
    stem = numbered[1] if numbered else stem
    while True:
        addition = f"（{number}）"
        candidate = f"{stem[:120 - len(addition) - len(suffix)]}{addition}{suffix}"
        if candidate.casefold() not in occupied:
            return candidate
        number += 1


def owner_dir(customer_id: str | None, conversation_id: str | None) -> str:
    if customer_id:
        return customer_id
    if conversation_id:
        return f"conv_{conversation_id}"
    return "shared"


def save_upload(
    db: Session,
    *,
    data: bytes,
    filename: str,
    user: User,
    customer_id: str | None = None,
    conversation_id: str | None = None,
    kind: str = "input",
) -> FileRecord:
    original_name = safe_filename(filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES | IMAGE_SUFFIXES:
        raise ServiceError(f"不支持的文件类型 {suffix or '(无扩展名)'}，请上传 xlsx / xlsm / xls / csv 或 PNG / JPG / JPEG / WebP / GIF 图片")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ServiceError(f"文件超过 {settings.max_upload_mb} MB")
    if not data:
        raise ServiceError("文件是空的")
    name = unique_filename(db, original_name, customer_id=customer_id, conversation_id=conversation_id, user_id=user.id)
    file_id = f"f_{uuid.uuid4().hex[:10]}"
    folder = settings.uploads_dir / owner_dir(customer_id, conversation_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{file_id}_{name}"
    path.write_bytes(data)
    try:
        profile = profile_file(path)
    except ReadError as exc:
        path.unlink(missing_ok=True)
        raise ServiceError(str(exc)) from exc
    profile["original_filename"] = filename
    record = FileRecord(
        id=file_id, customer_id=customer_id, conversation_id=conversation_id, filename=name, stored_path=str(path),
        size=len(data), kind=kind if kind in KIND_LABELS else "input", profile=profile, created_by=user.id,
    )
    db.add(record)
    db.flush()
    if customer_id:
        retain_for_customer(db, record, customer_id)
    if conversation_id:
        retain_for_conversation(db, record, conversation_id)
    return record


def register_output(db: Session, *, path: Path, filename: str, customer_id: str | None, conversation_id: str | None,
                    run_id: str | None, user_id: int | None) -> FileRecord:
    """登记系统生成的结果文件。"""
    try:
        profile = profile_file(path)
    except ReadError:
        profile = {}
    profile["original_filename"] = filename
    name = unique_filename(db, filename, customer_id=customer_id, conversation_id=conversation_id, user_id=user_id)
    record = FileRecord(
        id=f"f_{uuid.uuid4().hex[:10]}", customer_id=customer_id, conversation_id=conversation_id, run_id=run_id,
        filename=name, stored_path=str(path), size=path.stat().st_size, kind="output",
        profile=profile, created_by=user_id,
    )
    db.add(record)
    db.flush()
    if customer_id:
        retain_for_customer(db, record, customer_id)
    if conversation_id:
        retain_for_conversation(db, record, conversation_id)
    return record


def profile_file(path: Path) -> dict[str, Any]:
    """文件画像（不含数据预览，预览按需读取）。"""
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return image_profile(path)
    try:
        sheets = _read_file_copy(path)
    except ReadError as exc:
        # 空白或只含样式的合法工作簿仍能交给脚本处理，损坏文件不能借此绕过校验。
        if path.suffix.lower() not in {".xlsx", ".xlsm"} or "没有可读取的数据" not in str(exc):
            raise
        try:
            with temporary_file_copies() as copies:
                workbook = openpyxl.load_workbook(copies.file_copy(path), read_only=True, data_only=False)
                workbook.close()
        except Exception as invalid:
            raise exc from invalid
        return {"sheets": [], "file_only": True}
    profile = profile_workbook(sheets, preview_rows=0)
    for sheet in profile["sheets"]:
        sheet.pop("preview", None)
    return profile


def _read_file_copy(path: Path) -> list[SheetData]:
    with temporary_file_copies() as files:
        return read_workbook(files.file_copy(path))


def file_dict(record: FileRecord) -> dict[str, Any]:
    sheets = (record.profile or {}).get("sheets", [])
    image_type = (record.profile or {}).get("media_type")
    return {
        "file_id": record.id,
        "filename": record.filename,
        "kind": record.kind,
        "kind_label": KIND_LABELS.get(record.kind, record.kind),
        "size": record.size,
        "customer_id": record.customer_id,
        "conversation_id": record.conversation_id,
        "run_id": record.run_id,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "sheets": [
            {"sheet": s["sheet"], "rows": s["rows"], "cols": s["cols"], "header": s.get("header"),
             "columns": [{"name": c["name"], "dtype": c["dtype"]} for c in s.get("columns", [])]}
            for s in sheets
        ],
        "download_url": f"/api/files/{record.id}/download",
        "media_type": file_media_type(record),
        "preview_url": f"/api/files/{record.id}/image" if image_type in {
            "image/png", "image/jpeg", "image/webp", "image/gif",
        } else None,
    }


def file_media_type(record: FileRecord) -> str:
    media_type = (record.profile or {}).get("media_type")
    if media_type in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return media_type
    return {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
        ".xls": "application/vnd.ms-excel",
        ".csv": "text/csv",
    }.get(Path(record.filename).suffix.lower(), "application/octet-stream")


def get_file(db: Session, file_id: str) -> FileRecord:
    record = db.get(FileRecord, file_id)
    if record is None or not Path(record.stored_path).is_file():
        raise NotFound("文件不存在或已被删除")
    return record


def retain_for_conversation(db: Session, record: FileRecord, conversation_id: str) -> None:
    """在已完成范围检查后保存引用，不复制文件，也不更换文件 ID。"""
    key = (record.id, conversation_id)
    if db.get(FileConversation, key) is None:
        db.add(FileConversation(file_id=record.id, conversation_id=conversation_id))
        db.flush()


def retain_for_customer(db: Session, record: FileRecord, customer_id: str) -> None:
    if not can_access_in_customer(db, record, customer_id):
        name = unique_filename(db, record.filename, customer_id=customer_id, exclude_file_id=record.id)
        if name != record.filename:
            record.profile = {"original_filename": record.filename, **(record.profile or {})}
            record.filename = name
    # 升级前的首选客户也必须先保存引用，后续单侧删除才能独立。
    for owner_id in {record.customer_id, customer_id} - {None}:
        if db.get(FileCustomer, (record.id, owner_id)) is None:
            db.add(FileCustomer(file_id=record.id, customer_id=owner_id))
            db.flush()
    if record.customer_id is None:
        record.customer_id = customer_id
        db.flush()


def customer_file_condition(customer_id: str):
    linked = select(FileCustomer.file_id).where(FileCustomer.customer_id == customer_id)
    return or_(FileRecord.customer_id == customer_id, FileRecord.id.in_(linked))


def can_access_in_customer(db: Session, record: FileRecord, customer_id: str) -> bool:
    return record.customer_id == customer_id or db.get(FileCustomer, (record.id, customer_id)) is not None


def can_access_in_conversation(db: Session, record: FileRecord, *, conversation_id: str,
                               customer_id: str | None = None) -> bool:
    return (record.conversation_id == conversation_id
            or db.get(FileConversation, (record.id, conversation_id)) is not None
            or bool(customer_id and can_access_in_customer(db, record, customer_id)))


def can_access_file(db: Session, user: User, record: FileRecord) -> bool:
    if user.role == "admin" or record.created_by == user.id:
        return True
    if record.customer_id and can_access_customer(db, user, record.customer_id):
        return True
    if any(can_access_customer(db, user, customer_id) for customer_id in db.scalars(
        select(FileCustomer.customer_id).where(FileCustomer.file_id == record.id)
    )):
        return True
    linked = select(FileConversation.conversation_id).where(FileConversation.file_id == record.id)
    return db.scalar(select(Conversation.id).where(
        Conversation.user_id == user.id,
        or_(Conversation.id == record.conversation_id, Conversation.id.in_(linked)),
    ).limit(1)) is not None


def retain_customer_files(db: Session, customer_id: str, *, conversation_id: str | None = None) -> None:
    """在解绑/删除前保留各现存会话能使用的客户资料，兼容升级前的单归属记录。"""
    conversation_ids = ([conversation_id] if conversation_id else list(db.scalars(
        select(Conversation.id).where(Conversation.customer_id == customer_id))))
    if not conversation_ids:
        return
    file_ids = list(db.scalars(select(FileRecord.id).where(customer_file_condition(customer_id))))
    if not file_ids:
        return
    existing = set(db.execute(select(FileConversation.file_id, FileConversation.conversation_id).where(
        FileConversation.file_id.in_(file_ids), FileConversation.conversation_id.in_(conversation_ids),
    )))
    db.add_all([FileConversation(file_id=file_id, conversation_id=conv_id)
                for file_id in file_ids for conv_id in conversation_ids if (file_id, conv_id) not in existing])
    db.flush()


def delete_if_unowned(db: Session, record: FileRecord) -> bool:
    """最后一个客户/会话引用释放后才回收原件；回滚事务时原件仍在。"""
    if record.customer_id or record.conversation_id:
        return False
    if db.scalar(select(FileCustomer.file_id).where(FileCustomer.file_id == record.id).limit(1)):
        return False
    if db.scalar(select(FileConversation.file_id).where(FileConversation.file_id == record.id).limit(1)):
        return False
    path = record.stored_path
    db.delete(record)
    db.flush()
    # 老版本可能登记过指向同一路径的多个结果，其他记录仍持有时不能删原件。
    if db.scalar(select(FileRecord.id).where(FileRecord.stored_path == path).limit(1)) is None:
        db.info.setdefault("deleted_file_paths", set()).add(path)
    return True


def remove_from_customer(db: Session, record: FileRecord, user: User, customer_id: str) -> bool:
    require_customer(db, user, customer_id)
    if not can_access_in_customer(db, record, customer_id):
        raise ServiceError("这个文件不属于指定客户")
    # 兼容已有会话尚未显式加载的客户文件：客户删除不能破坏它们原有的输入范围。
    for conv_id in db.scalars(select(Conversation.id).where(Conversation.customer_id == customer_id)):
        retain_for_conversation(db, record, conv_id)
    release_customer_reference(db, record, customer_id)
    db.flush()
    return delete_if_unowned(db, record)


def release_customer_reference(db: Session, record: FileRecord, customer_id: str) -> None:
    link = db.get(FileCustomer, (record.id, customer_id))
    if link is not None:
        db.delete(link)
    db.flush()
    if record.customer_id == customer_id:
        record.customer_id = db.scalar(select(FileCustomer.customer_id).where(
            FileCustomer.file_id == record.id, FileCustomer.customer_id != customer_id,
        ).order_by(FileCustomer.customer_id).limit(1))
        db.flush()


def remove_from_conversation(db: Session, record: FileRecord, user: User, conversation_id: str) -> bool:
    conv = db.get(Conversation, conversation_id)
    if conv is None or (conv.user_id != user.id and user.role != "admin"):
        raise Forbidden("你没有权限修改这个会话的文件")
    if record.conversation_id != conversation_id and db.get(FileConversation, (record.id, conversation_id)) is None:
        raise ServiceError("这个文件不属于指定会话")
    link = db.get(FileConversation, (record.id, conversation_id))
    if link is not None:
        db.delete(link)
    if record.conversation_id == conversation_id:
        record.conversation_id = None
    db.flush()
    return delete_if_unowned(db, record)


def preview(record: FileRecord, sheet: str | None = None, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    if Path(record.stored_path).suffix.lower() in IMAGE_SUFFIXES:
        raise ServiceError("图片不支持表格预览，请使用图片预览")
    sheets = _read_file_copy(Path(record.stored_path))
    chosen = next((s for s in sheets if s.name == sheet), sheets[0]) if sheet else sheets[0]
    part = chosen.df.iloc[offset : offset + limit]
    return {
        "file_id": record.id, "filename": record.filename, "sheet": chosen.name, "sheets": [s.name for s in sheets],
        "total": int(len(chosen.df)), "offset": offset, "limit": limit,
        "columns": [str(c) for c in chosen.df.columns], "rows": frame_rows(part), "header": chosen.header.to_dict(),
    }


def list_files(db: Session, *, customer_id: str | None = None, conversation_id: str | None = None,
               kinds: tuple[str, ...] | None = None, available_only: bool = True) -> list[FileRecord]:
    conds = []
    if customer_id:
        conds.append(customer_file_condition(customer_id))
    if conversation_id:
        linked = select(FileConversation.file_id).where(FileConversation.conversation_id == conversation_id)
        conds.extend([FileRecord.conversation_id == conversation_id, FileRecord.id.in_(linked)])
    if not conds:
        return []
    stmt = select(FileRecord).where(or_(*conds)).order_by(FileRecord.created_at.desc())
    if kinds:
        stmt = stmt.where(FileRecord.kind.in_(kinds))
    records = [record for record in db.scalars(stmt) if not available_only or Path(record.stored_path).is_file()]
    if conversation_id:
        for record in records:
            retain_for_conversation(db, record, conversation_id)
    return records


# ---------------------------------------------------------------------------
# 工作区
# ---------------------------------------------------------------------------


def table_base_name(filename: str) -> str:
    """表名去掉日期月份，让每个月的同类文件得到同一个表名（Recipe 的输入名因此稳定）：运费明细_2026-09.xlsx → 运费明细"""
    stem = file_pattern_from_name(filename).replace("*", "").strip(" _-")
    return stem or Path(filename).stem


def load_into_workspace(ws: Workspace, record: FileRecord, *, path: Path | None = None) -> list[TableInfo]:
    """把文件的每个 sheet 存成工作区里的一张表。期望结果文件的表名用「期望_sheet名」。"""
    if (getattr(record, "profile", None) or {}).get("file_only"):
        return []
    sheets = read_workbook(path) if path is not None else _read_file_copy(Path(record.stored_path))
    for info in ws.list_tables():  # 同一个文件重复加载时先清掉旧表
        if info.file_id == record.id:
            ws.drop(info.name)
    base = table_base_name(record.filename)
    if ws.exists(sanitize_table_name(base)):
        # 工作区里已经有别的文件占了这个名字（例如同时上传了 9 月和 10 月）：用带日期的完整文件名，方便区分
        base = Path(record.filename).stem
    tables = []
    for sheet in sheets:
        if record.kind == "expected":
            name = f"期望_{sheet.name}"
        else:
            name = base if len(sheets) == 1 else f"{base}_{sheet.name}"
        name = ws.unique_name(name)
        info = ws.put_dataframe(
            name, sheet.df, label=f"{record.filename} · {sheet.name}", source="uploaded",
            role="expected" if record.kind == "expected" else "input", file_id=record.id, filename=record.filename,
            sheet=sheet.name, header=sheet.header.to_dict(),
            description=("人工做好的期望结果" if record.kind == "expected" else
                         "系统生成的工作簿，可继续作为输入处理" if record.kind == "output" else "上传的原始数据")
                        + (f"，标题：{sheet.header.title}" if sheet.header.title else ""),
        )
        tables.append(info)
    return tables


def export_tables(ws: Workspace, sheets: list[tuple[str, str]], path: Path) -> list[dict[str, Any]]:
    specs = [SheetSpec(name=sheet_name or table, df=ws.df(table)) for table, sheet_name in sheets]
    return write_excel(path, specs)
