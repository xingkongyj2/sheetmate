"""上传内容、原始数据预览和图片媒体类型检查。

只接收标准文件流和存储记录，不依赖 HTTP 上传对象或响应类型。
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, BinaryIO

from sheetmate.core.config.settings import settings
from sheetmate.core.errors import ServiceError
from sheetmate.db.models import FileRecord
from sheetmate.engine.workbooks.images import image_profile
from sheetmate.engine.workbooks.reader import ReadError, read_workbook
from sheetmate.engine.tables.sql import frame_rows
from sheetmate.services.files import service as file_service
from sheetmate.services.files.preview import preview_data


def preview_upload(stream: BinaryIO, *, filename: str | None, sheet: str | None = None,
                   offset: int = 0, limit: int = 50, raw: bool = False) -> dict[str, Any]:
    """不创建持久化记录，限量读取并预览临时上传内容。"""
    filename = file_service.safe_filename(filename or "file")
    if not raw and Path(filename).suffix.lower() != ".xls":
        raise ServiceError("临时数据预览只支持 .xls 文件")
    max_mb = settings.max_upload_mb if raw else min(settings.max_upload_mb, 25)
    data = stream.read(max_mb * 1024 * 1024 + 1)
    if len(data) > max_mb * 1024 * 1024:
        raise ServiceError(f"文件超过 {max_mb} MB，请拆分后再预览")
    if not data:
        raise ServiceError("文件是空的")
    with TemporaryDirectory(prefix="sheetmate_preview_") as directory:
        path = Path(directory) / filename
        path.write_bytes(data)
        if raw:
            return preview_data(path, filename=filename, sheet=sheet, offset=offset, limit=limit)
        try:
            sheets = read_workbook(path)
        except ReadError as exc:
            raise ServiceError(str(exc)) from exc
        chosen = next((item for item in sheets if item.name == sheet), sheets[0]) if sheet else sheets[0]
        page = chosen.df.iloc[offset : offset + limit]
        return {
            "filename": filename, "sheet": chosen.name, "sheets": [item.name for item in sheets],
            "total": int(len(chosen.df)), "offset": offset, "limit": limit,
            "columns": [str(column) for column in chosen.df.columns], "rows": frame_rows(page),
            "header": chosen.header.to_dict(),
        }


def preview_file(record: FileRecord, *, sheet: str | None = None, offset: int = 0,
                 limit: int = 50, raw: bool = False) -> dict[str, Any]:
    if raw:
        return {"file_id": record.id, **preview_data(Path(record.stored_path), filename=record.filename,
                                                    sheet=sheet, offset=offset, limit=limit)}
    return file_service.preview(record, sheet, offset, min(limit, 500))


def image_media_type(record: FileRecord) -> str:
    """从文件内容检查图片类型，拒绝无法读取的图片。"""
    try:
        return image_profile(Path(record.stored_path))["media_type"]
    except ReadError as exc:
        raise ServiceError(str(exc)) from exc
