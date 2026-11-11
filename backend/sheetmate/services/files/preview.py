"""Bounded, read-only cell previews that do not import workbook styling or drawings."""

from __future__ import annotations

import csv
import codecs
import io
import math
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from sheetmate.core.config.settings import settings
from sheetmate.core.errors import ServiceError


MAX_UNPACKED_BYTES = 120 * 1024 * 1024
MAX_COLUMNS = 200
MAX_PAGE_ROWS = 100
MAX_TEXT = 5000
MAX_PAGE_TEXT = 1_000_000
MAX_ROWS = 1_048_576
MAX_EXCEL_COLUMNS = 16_384
_CELL = re.compile(r"\$?([A-Za-z]+)\$?([0-9]+)$")
_DATA_WARNING = "当前为数据预览，不保留原始排版；日期、百分比和金额可能显示为原始数值。"


@dataclass(frozen=True)
class _SharedString:
    index: int


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _coordinate(value: str) -> tuple[int, int]:
    match = _CELL.fullmatch(value)
    if not match:
        return 0, 0
    column = 0
    for char in match[1].upper():
        column = column * 26 + ord(char) - 64
    row = int(match[2])
    if not 1 <= row <= MAX_ROWS or not 1 <= column <= MAX_EXCEL_COLUMNS:
        raise ServiceError("工作簿中的单元格行列坐标超出 Excel 范围，无法提供数据预览。")
    return row, column


def _column_name(index: int) -> str:
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


def _trim(value: Any, warnings: set[str]) -> Any:
    if isinstance(value, str) and len(value) > MAX_TEXT:
        warnings.add(f"过长的单元格文字仅显示前 {MAX_TEXT} 个字符，完整内容请下载查看。")
        return value[:MAX_TEXT]
    return value


def _number(value: str) -> int | float | str:
    try:
        parsed = float(value) if any(char in value.lower() for char in ".e") else int(value)
        return parsed if not isinstance(parsed, float) or math.isfinite(parsed) else value
    except ValueError:
        return value


def _cell_value(cell: ET.Element, warnings: set[str]) -> tuple[Any, bool]:
    kind = cell.get("t", "n")
    raw = next((child.text for child in cell if _local(child.tag) == "v"), None)
    formula = any(_local(child.tag) == "f" for child in cell)
    if kind == "inlineStr":
        value = "".join(child.text or "" for child in cell.iter() if _local(child.tag) == "t")
        return _trim(value, warnings), bool(value) or formula
    if raw is None:
        if formula:
            warnings.add("部分公式未保存计算结果，在数据预览中显示为空；请在 Excel 中重新计算后查看。")
        return None, formula
    if kind == "s":
        try:
            return _SharedString(int(raw)), True
        except ValueError as exc:
            raise ServiceError("工作簿中的共享文本引用无法读取，请下载原文件查看。") from exc
    if kind == "b":
        return raw == "1", True
    if kind in {"str", "e", "d"}:
        return _trim(raw, warnings), True
    return _number(raw), True


def _xml(archive: ZipFile, name: str) -> ET.Element:
    info = archive.getinfo(name)
    if info.file_size > 2 * 1024 * 1024:
        raise ServiceError("工作簿目录信息过大，无法提供数据预览，请下载查看。")
    return ET.fromstring(archive.read(name))


def _shared_strings(archive: ZipFile, wanted: set[int], warnings: set[str]) -> dict[int, str]:
    if not wanted:
        return {}
    try:
        stream = archive.open("xl/sharedStrings.xml")
    except KeyError as exc:
        raise ServiceError("工作簿缺少共享文本，无法显示这页数据，请下载原文件查看。") from exc
    result: dict[int, str] = {}
    with stream:
        index = 0
        parser = ET.iterparse(stream, events=("start", "end"))
        _, root = next(parser)
        for event, element in parser:
            if event != "end" or _local(element.tag) != "si":
                continue
            if index in wanted:
                result[index] = _trim("".join(child.text or "" for child in element.iter()
                                             if _local(child.tag) == "t"), warnings)
            element.clear()
            root.remove(element)
            index += 1
            if len(result) == len(wanted):
                break
    if set(result) != wanted:
        raise ServiceError("工作簿中的共享文本引用不完整，无法显示这页数据，请下载原文件查看。")
    return result


def _xlsx(path: Path, sheet: str | None, offset: int, limit: int) -> dict[str, Any]:
    warnings = {_DATA_WARNING}
    with ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 20_000 or sum(entry.file_size for entry in entries) > MAX_UNPACKED_BYTES:
            raise ServiceError("文件解压后的内容超过数据预览限制，请下载原文件查看。")
        if any(entry.flag_bits & 1 for entry in entries):
            raise ServiceError("加密文件无法在线预览，请解密后重试。")
        relationships = {item.get("Id"): item.get("Target", "")
                         for item in _xml(archive, "xl/_rels/workbook.xml.rels")
                         if item.get("TargetMode") != "External"}
        worksheets = []
        for item in _xml(archive, "xl/workbook.xml").iter():
            if _local(item.tag) != "sheet":
                continue
            relationship = next((value for key, value in item.attrib.items() if _local(key) == "id"), "")
            target = relationships.get(relationship, "")
            target = posixpath.normpath(target.lstrip("/") if target.startswith("/") else posixpath.join("xl", target))
            if not target.startswith("xl/") or target not in archive.namelist():
                continue
            worksheets.append((item.get("name", "工作表"), target))
        if not worksheets:
            raise ServiceError("文件中没有可预览的工作表。")
        if len(worksheets) > 100:
            raise ServiceError("工作表数量超过数据预览限制，请下载原文件查看。")
        chosen = next((item for item in worksheets if item[0] == sheet), None) if sheet else worksheets[0]
        if chosen is None:
            raise ServiceError("没有找到指定的工作表，请重新选择。")
        page: dict[int, dict[int, Any]] = {}
        last_content_row = 0
        dimension_rows = 0
        visible_columns = 0
        row_number = 0
        row_has_content = False
        has_more = False
        with archive.open(chosen[1]) as stream:
            parents: list[ET.Element] = []
            for event, element in ET.iterparse(stream, events=("start", "end")):
                tag = _local(element.tag)
                if event == "start":
                    parents.append(element)
                    if tag == "dimension":
                        dimension_rows, _ = _coordinate(element.get("ref", "").split(":")[-1])
                    elif tag == "row":
                        row_number = int(element.get("r") or row_number + 1)
                        if not 1 <= row_number <= MAX_ROWS:
                            raise ServiceError("工作簿中的单元格行列坐标超出 Excel 范围，无法提供数据预览。")
                        row_has_content = False
                    continue
                if tag == "c":
                    cell_row, column = _coordinate(element.get("r", ""))
                    if cell_row:
                        row_number = cell_row
                    value, has_content = _cell_value(element, warnings)
                    row_has_content = row_has_content or has_content
                    if has_content:
                        last_content_row = max(last_content_row, row_number)
                        if column > MAX_COLUMNS:
                            warnings.add(f"数据预览仅显示前 {MAX_COLUMNS} 列，完整内容请下载查看。")
                        elif offset < row_number <= offset + limit:
                            visible_columns = max(visible_columns, column)
                            page.setdefault(row_number, {})[column] = value
                    element.clear()
                elif tag == "row":
                    element.clear()
                    if len(parents) > 1:
                        try:
                            parents[-2].remove(element)
                        except ValueError:
                            pass
                    if row_has_content and row_number > offset + limit:
                        has_more = True
                        break
                parents.pop()
        total = max(last_content_row, dimension_rows) if has_more else last_content_row
        visible_columns = max(1, visible_columns)
        wanted = {value.index for row in page.values() for value in row.values() if isinstance(value, _SharedString)}
        shared = _shared_strings(archive, wanted, warnings)
        rows = [[shared[value.index] if isinstance(value, _SharedString) else value
                 for column in range(1, visible_columns + 1)
                 for value in [page.get(row, {}).get(column)]]
                for row in range(offset + 1, min(offset + limit, total) + 1)]
        return {"sheet": chosen[0], "sheets": [item[0] for item in worksheets], "total": total,
                "total_exact": not has_more, "has_more": has_more,
                "columns": [_column_name(column) for column in range(1, visible_columns + 1)],
                "rows": rows, "warnings": sorted(warnings)}


def _csv(path: Path, offset: int, limit: int) -> dict[str, Any]:
    warnings = {"当前为数据预览，单元格按文本显示。"}
    with path.open("rb") as binary:
        sample = binary.read(64 * 1024)
        if sample.startswith((b"\xff\xfe", b"\xfe\xff")):
            encoding = "utf-16"
        else:
            try:
                codecs.getincrementaldecoder("utf-8-sig")().decode(sample, final=False)
                encoding = "utf-8-sig"
            except UnicodeDecodeError:
                encoding = "gb18030"
        decoded = sample.decode(encoding, errors="replace")
        try:
            dialect = csv.Sniffer().sniff(decoded, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        binary.seek(0)
        with io.TextIOWrapper(binary, encoding=encoding, newline="") as text:
            rows = []
            total = 0
            has_more = False
            for index, row in enumerate(csv.reader(text, dialect), 1):
                total = index
                if index <= offset:
                    continue
                if len(rows) == limit:
                    has_more = True
                    break
                if len(row) > MAX_COLUMNS:
                    warnings.add(f"数据预览仅显示前 {MAX_COLUMNS} 列，完整内容请下载查看。")
                rows.append([_trim(value, warnings) for value in row[:MAX_COLUMNS]])
    width = max((len(row) for row in rows), default=1)
    rows = [row + [None] * (width - len(row)) for row in rows]
    return {"sheet": path.stem, "sheets": [path.stem], "total": total, "total_exact": not has_more,
            "has_more": has_more, "columns": [_column_name(index) for index in range(1, width + 1)],
            "rows": rows, "warnings": sorted(warnings)}


def _xls(path: Path, sheet: str | None, offset: int, limit: int) -> dict[str, Any]:
    import xlrd

    try:
        workbook = xlrd.open_workbook(path, on_demand=True)
    except xlrd.XLRDError as exc:
        raise ServiceError("无法读取旧版 Excel 的单元格数据，请下载原文件查看，或另存为 .xlsx 后重试。") from exc
    warnings = {"当前为数据预览，不保留原始排版。"}
    try:
        names = workbook.sheet_names()
        if not names:
            raise ServiceError("文件中没有可预览的工作表。")
        if sheet is not None and sheet not in names:
            raise ServiceError("没有找到指定的工作表，请重新选择。")
        chosen = workbook.sheet_by_name(sheet or names[0])
        width = min(MAX_COLUMNS, chosen.ncols)
        if chosen.ncols > MAX_COLUMNS:
            warnings.add(f"数据预览仅显示前 {MAX_COLUMNS} 列，完整内容请下载查看。")
        rows = []
        for index in range(offset, min(offset + limit, chosen.nrows)):
            row = []
            for column in range(width):
                cell = chosen.cell(index, column)
                if cell.ctype == xlrd.XL_CELL_DATE:
                    value = xlrd.xldate_as_datetime(cell.value, workbook.datemode).isoformat()
                elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                    value = bool(cell.value)
                elif cell.ctype == xlrd.XL_CELL_ERROR:
                    value = xlrd.error_text_from_code.get(cell.value, "#ERROR!")
                else:
                    value = cell.value
                row.append(_trim(value, warnings))
            rows.append(row)
        return {"sheet": chosen.name, "sheets": names, "total": chosen.nrows, "total_exact": True,
                "has_more": offset + limit < chosen.nrows,
                "columns": [_column_name(index) for index in range(1, width + 1)], "rows": rows,
                "warnings": sorted(warnings)}
    finally:
        workbook.release_resources()


def preview_data(path: Path, *, filename: str, sheet: str | None = None,
                 offset: int = 0, limit: int = MAX_PAGE_ROWS) -> dict[str, Any]:
    """Read one page without rewriting the original or registering temporary uploads."""
    if offset < 0 or offset >= MAX_ROWS:
        raise ServiceError("预览行号超出范围。")
    limit = max(1, min(limit, MAX_PAGE_ROWS))
    if path.stat().st_size > settings.max_upload_mb * 1024 * 1024:
        raise ServiceError(f"文件超过 {settings.max_upload_mb} MB，请下载原文件查看。")
    suffix = Path(filename).suffix.lower()
    try:
        if suffix in {".xlsx", ".xlsm"}:
            result = _xlsx(path, sheet, offset, limit)
        elif suffix == ".csv":
            result = _csv(path, offset, limit)
        elif suffix == ".xls":
            result = _xls(path, sheet, offset, limit)
        else:
            raise ServiceError("数据预览支持 .xlsx、.xlsm、.xls 和 .csv 文件。")
    except ServiceError:
        raise
    except (BadZipFile, ET.ParseError, KeyError, ValueError, OSError, csv.Error) as exc:
        raise ServiceError("无法读取文件中的单元格数据，请下载原文件查看，或另存为 .xlsx 后重试。") from exc
    remaining = MAX_PAGE_TEXT
    for row in result["rows"]:
        for index, value in enumerate(row):
            if isinstance(value, str):
                if len(value) > remaining:
                    row[index] = value[:remaining]
                    warning = "当前页文字较多，部分单元格文字已截短，完整内容请下载查看。"
                    if warning not in result["warnings"]:
                        result["warnings"].append(warning)
                remaining = max(0, remaining - len(value))
    return {"filename": filename, "offset": offset, "limit": limit, "column_offset": 0, "raw": True, **result}
