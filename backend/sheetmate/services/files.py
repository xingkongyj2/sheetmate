"""文件：上传（校验扩展名和大小、文件名防注入、按客户/会话隔离存放）、画像、预览、加载到工作区、导出。

本地优先：文件存在 data/uploads/{客户id 或 conv_会话id}/ 下，输出在 data/outputs/... 下，源文件只读。
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from sheetmate.config import settings
from sheetmate.db.models import FileRecord, User
from sheetmate.engine.profile import profile_workbook
from sheetmate.engine.reader import SUPPORTED_SUFFIXES, ReadError, read_workbook
from sheetmate.engine.sqlutil import frame_rows, sanitize_table_name
from sheetmate.engine.workspace import TableInfo, Workspace
from sheetmate.engine.writer import SheetSpec, write_excel
from sheetmate.recipe.schema import file_pattern_from_name
from sheetmate.services.errors import NotFound, ServiceError

KIND_LABELS = {"input": "输入文件", "expected": "期望结果", "output": "生成结果", "doc": "处理说明"}
_UNSAFE = re.compile(r"[^\w一-鿿.\-()（） ]+")


def safe_filename(name: str) -> str:
    """去掉路径和危险字符，只保留文件名本身。"""
    base = Path(str(name or "file")).name
    base = _UNSAFE.sub("_", base).strip(" .") or "file"
    return base[-120:]


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
    name = safe_filename(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ServiceError(f"不支持的文件类型 {suffix or '(无扩展名)'}，请上传 xlsx / xls / csv")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ServiceError(f"文件超过 {settings.max_upload_mb} MB")
    if not data:
        raise ServiceError("文件是空的")
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
    record = FileRecord(
        id=file_id, customer_id=customer_id, conversation_id=conversation_id, filename=name, stored_path=str(path),
        size=len(data), kind=kind if kind in KIND_LABELS else "input", profile=profile, created_by=user.id,
    )
    db.add(record)
    db.flush()
    return record


def register_output(db: Session, *, path: Path, filename: str, customer_id: str | None, conversation_id: str | None,
                    run_id: str | None, user_id: int | None) -> FileRecord:
    """登记系统生成的结果文件。"""
    try:
        profile = profile_file(path)
    except ReadError:
        profile = {}
    record = FileRecord(
        id=f"f_{uuid.uuid4().hex[:10]}", customer_id=customer_id, conversation_id=conversation_id, run_id=run_id,
        filename=safe_filename(filename), stored_path=str(path), size=path.stat().st_size, kind="output",
        profile=profile, created_by=user_id,
    )
    db.add(record)
    db.flush()
    return record


def profile_file(path: Path) -> dict[str, Any]:
    """文件画像（不含数据预览，预览按需读取）。"""
    sheets = read_workbook(path)
    profile = profile_workbook(sheets, preview_rows=0)
    for sheet in profile["sheets"]:
        sheet.pop("preview", None)
    return profile


def file_dict(record: FileRecord) -> dict[str, Any]:
    sheets = (record.profile or {}).get("sheets", [])
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
    }


def get_file(db: Session, file_id: str) -> FileRecord:
    record = db.get(FileRecord, file_id)
    if record is None or not Path(record.stored_path).exists():
        raise NotFound("文件不存在或已被删除")
    return record


def preview(record: FileRecord, sheet: str | None = None, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    sheets = read_workbook(Path(record.stored_path))
    chosen = next((s for s in sheets if s.name == sheet), sheets[0]) if sheet else sheets[0]
    part = chosen.df.iloc[offset : offset + limit]
    return {
        "file_id": record.id, "filename": record.filename, "sheet": chosen.name, "sheets": [s.name for s in sheets],
        "total": int(len(chosen.df)), "offset": offset, "limit": limit,
        "columns": [str(c) for c in chosen.df.columns], "rows": frame_rows(part), "header": chosen.header.to_dict(),
    }


def list_files(db: Session, *, customer_id: str | None = None, conversation_id: str | None = None,
               kinds: tuple[str, ...] | None = None) -> list[FileRecord]:
    conds = []
    if customer_id:
        conds.append(FileRecord.customer_id == customer_id)
    if conversation_id:
        conds.append(FileRecord.conversation_id == conversation_id)
    if not conds:
        return []
    stmt = select(FileRecord).where(or_(*conds)).order_by(FileRecord.created_at.desc())
    if kinds:
        stmt = stmt.where(FileRecord.kind.in_(kinds))
    return list(db.scalars(stmt))


# ---------------------------------------------------------------------------
# 工作区
# ---------------------------------------------------------------------------


def table_base_name(filename: str) -> str:
    """表名去掉日期月份，让每个月的同类文件得到同一个表名（Recipe 的输入名因此稳定）：运费明细_2026-09.xlsx → 运费明细"""
    stem = file_pattern_from_name(filename).replace("*", "").strip(" _-")
    return stem or Path(filename).stem


def load_into_workspace(ws: Workspace, record: FileRecord) -> list[TableInfo]:
    """把文件的每个 sheet 存成工作区里的一张表。期望结果文件的表名用「期望_sheet名」。"""
    sheets = read_workbook(Path(record.stored_path))
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
            description=("人工做好的期望结果" if record.kind == "expected" else "上传的原始数据") + (f"，标题：{sheet.header.title}" if sheet.header.title else ""),
        )
        tables.append(info)
    return tables


def export_tables(ws: Workspace, sheets: list[tuple[str, str]], path: Path) -> list[dict[str, Any]]:
    specs = [SheetSpec(name=sheet_name or table, df=ws.df(table)) for table, sheet_name in sheets]
    return write_excel(path, specs)
