"""从声明式布局创建新工作簿；AI 提供数据与格式，引擎只执行经过校验的配置。

``rows`` 是从 A1 开始的完整网格，包含标题、表头和数据。字符串始终作为
文本保存，公式必须明确写成 ``{"formula": "=SUM(C3:C8)"}``。不执行模型生成
的 Python，也不读取模板；输出使用全新路径，失败不会留下半个工作簿。
"""

from __future__ import annotations

import datetime as dt
import math
import os
import re
import tempfile
from copy import copy
from pathlib import Path
from typing import Any, Literal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, PatternFill, Side
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.workbook.properties import CalcProperties
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr, field_validator, model_validator

MAX_SHEETS = 20
MAX_ROWS = 10_000
MAX_COLUMNS = 256
MAX_GRID_CELLS = 250_000
MAX_FORMAT_CELLS = 500_000
MAX_RANGES = 500
_RANGE = re.compile(r"^\$?([A-Za-z]{1,3})\$?([1-9][0-9]*)(?::\$?([A-Za-z]{1,3})\$?([1-9][0-9]*))?$")
_COLOR = re.compile(r"^[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
_INVALID_TEXT = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")
_INVALID_SHEET = re.compile(r"[\\/*?:\[\]]")


def _check_text(value: str, *, max_length: int = 32767) -> str:
    if len(value) > max_length or _INVALID_TEXT.search(value):
        raise ValueError(f"文字不能包含非法控制字符，长度不能超过 {max_length}")
    return value


def _bounds(value: str) -> tuple[int, int, int, int]:
    match = _RANGE.fullmatch(value)
    if not match:
        raise ValueError(f"单元格范围「{value}」无效，请使用 A1 或 A1:D8")
    c1, r1, c2, r2 = match.groups()
    left, top = column_index_from_string(c1.upper()), int(r1)
    right, bottom = column_index_from_string((c2 or c1).upper()), int(r2 or r1)
    if left > right or top > bottom:
        raise ValueError(f"单元格范围「{value}」的起点必须在终点左上方")
    if right > MAX_COLUMNS or bottom > MAX_ROWS:
        raise ValueError(f"单元格范围不能超过 {MAX_ROWS} 行、{MAX_COLUMNS} 列")
    return left, top, right, bottom


def _normalize_range(value: str) -> str:
    left, top, right, bottom = _bounds(value)
    start = f"{get_column_letter(left)}{top}"
    end = f"{get_column_letter(right)}{bottom}"
    return start if start == end else f"{start}:{end}"


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FormulaCell(_Spec):
    formula: StrictStr = Field(description="明确的 Excel 公式，以 = 开头；由 Excel 打开时重算，服务端不计算公式。")

    @field_validator("formula")
    @classmethod
    def validate_formula(cls, value: str) -> str:
        _check_text(value, max_length=8192)
        if not value.startswith("=") or not value[1:].strip():
            raise ValueError("公式必须以 = 开头并包含表达式")
        # DDE 可以在客户端运行程序，不属于声明式计算公式。
        if "|" in value:
            raise ValueError("公式不支持 DDE 外部程序调用")
        return value


def _parse_date(value: str) -> dt.date | dt.datetime:
    try:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            return dt.date.fromisoformat(value)
        if not re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}", value):
            raise ValueError
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("日期必须是有效的 YYYY-MM-DD 或 ISO 日期时间，如 2026-10-31T14:30:00") from exc
    if parsed.tzinfo is not None:
        raise ValueError("Excel 日期不支持时区，请先明确转换为所需时区的本地日期时间")
    return parsed


class DateCell(_Spec):
    date: StrictStr = Field(description="真正的 Excel 日期或日期时间：YYYY-MM-DD 或无时区 ISO 日期时间，如 2026-10-31T14:30:00；普通日期字符串仍作为文本。")

    @field_validator("date")
    @classmethod
    def validate_date(cls, value: str) -> str:
        return _parse_date(value).isoformat()


CellValue = StrictStr | StrictBool | StrictInt | StrictFloat | FormulaCell | DateCell | None


class RangeStyle(_Spec):
    range: StrictStr = Field(description="应用样式的 A1 范围，如 A1:D1 或 C3:C20")
    bold: StrictBool | None = None
    italic: StrictBool | None = None
    font_size: float | None = Field(default=None, gt=0, le=72, allow_inf_nan=False)
    font_color: StrictStr | None = Field(default=None, description="6 或 8 位十六进制颜色，不带 #")
    fill: StrictStr | None = Field(default=None, description="填充色，6 或 8 位十六进制颜色，不带 #")
    horizontal: Literal["left", "center", "right", "justify", "distributed", "general"] | None = None
    vertical: Literal["top", "center", "bottom", "justify", "distributed"] | None = None
    wrap_text: StrictBool | None = None
    border: Literal["thin", "medium", "thick", "dashed", "dotted", "double", "none"] | None = None
    number_format: StrictStr | None = Field(default=None, max_length=255, description="Excel 数字格式，如 #,##0.00、0%、yyyy-mm-dd")

    @field_validator("range")
    @classmethod
    def validate_range(cls, value: str) -> str:
        return _normalize_range(value)

    @field_validator("font_color", "fill")
    @classmethod
    def validate_color(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not _COLOR.fullmatch(value):
            raise ValueError("颜色必须是 6 或 8 位十六进制，如 1F4E78")
        return ("FF" + value if len(value) == 6 else value).upper()

    @field_validator("number_format")
    @classmethod
    def validate_number_format(cls, value: str | None) -> str | None:
        return _check_text(value) if value is not None else None


class CreatedSheet(_Spec):
    name: StrictStr = Field(min_length=1, max_length=31, description="工作表名称，不能含 \\ / * ? : [ ]")
    rows: list[list[CellValue]] = Field(default_factory=list, max_length=MAX_ROWS, description="从 A1 起的完整二维网格，包含标题、表头和数据；合并格除左上角外填 null。")
    merges: list[StrictStr] = Field(default_factory=list, max_length=MAX_RANGES, description="合并范围，如 [\"A1:D1\",\"A2:A3\"]；不可重叠或吞掉非空数据。")
    styles: list[RangeStyle] = Field(default_factory=list, max_length=MAX_RANGES, description="依次应用的区域样式，后项覆盖相同属性。")
    column_widths: dict[StrictStr, float] = Field(default_factory=dict, description="列字母到列宽，如 {\"A\":12,\"B\":24}")
    row_heights: dict[StrictStr, float] = Field(default_factory=dict, description="行号字符串到行高，如 {\"1\":30}")
    freeze_panes: StrictStr | None = Field(default=None, description="冻结位置，如 A3（冻结前两行）；null 不冻结。")
    auto_filter: StrictStr | None = Field(default=None, description="筛选区域，应从实际表头开始，如 A2:D20。")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        _check_text(value)
        if not value.strip() or _INVALID_SHEET.search(value) or value.startswith("'") or value.endswith("'"):
            raise ValueError("工作表名称不能为空，不能含 \\ / * ? : [ ]，不能以单引号开头或结尾")
        return value

    @field_validator("rows")
    @classmethod
    def validate_rows(cls, rows: list[list[CellValue]]) -> list[list[CellValue]]:
        if sum(len(row) for row in rows) > MAX_GRID_CELLS:
            raise ValueError(f"数据网格最多 {MAX_GRID_CELLS} 个单元格")
        for row in rows:
            if len(row) > MAX_COLUMNS:
                raise ValueError(f"每行最多 {MAX_COLUMNS} 列")
            for value in row:
                if isinstance(value, str):
                    _check_text(value)
                elif isinstance(value, float) and not math.isfinite(value):
                    raise ValueError("数字必须为有限值，不能使用 NaN 或 Infinity")
        return rows

    @field_validator("merges")
    @classmethod
    def validate_merges(cls, values: list[str]) -> list[str]:
        normalized = [_normalize_range(value) for value in values]
        if any(":" not in value for value in normalized):
            raise ValueError("合并范围至少需要两个单元格")
        return normalized

    @field_validator("freeze_panes", "auto_filter")
    @classmethod
    def validate_position(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        result = _normalize_range(value)
        if info.field_name == "freeze_panes" and ":" in result:
            raise ValueError("freeze_panes 需要一个单元格坐标，如 A3")
        return result

    @field_validator("column_widths")
    @classmethod
    def validate_widths(cls, values: dict[str, float]) -> dict[str, float]:
        result = {}
        for column, width in values.items():
            if not re.fullmatch(r"[A-Za-z]{1,3}", column) or column_index_from_string(column.upper()) > MAX_COLUMNS:
                raise ValueError(f"列宽的列名「{column}」无效或超出 {MAX_COLUMNS} 列")
            if not math.isfinite(width) or not 0 < width <= 255:
                raise ValueError("列宽必须大于 0 且不超过 255")
            result[column.upper()] = width
        return result

    @field_validator("row_heights")
    @classmethod
    def validate_heights(cls, values: dict[str, float]) -> dict[str, float]:
        for row, height in values.items():
            if not re.fullmatch(r"[1-9][0-9]*", row) or int(row) > MAX_ROWS:
                raise ValueError(f"行高的行号「{row}」无效或超出 {MAX_ROWS} 行")
            if not math.isfinite(height) or not 0 < height <= 409:
                raise ValueError("行高必须大于 0 且不超过 409")
        return values

    @model_validator(mode="after")
    def validate_layout(self) -> CreatedSheet:
        ranges = [_bounds(value) for value in self.merges]
        for i, (left, top, right, bottom) in enumerate(ranges):
            for other in ranges[:i]:
                if left <= other[2] and right >= other[0] and top <= other[3] and bottom >= other[1]:
                    raise ValueError(f"合并区域「{self.merges[i]}」与其他合并区域重叠")
            for row_index in range(top - 1, min(bottom, len(self.rows))):
                row = self.rows[row_index]
                for column_index in range(left - 1, min(right, len(row))):
                    if row_index == top - 1 and column_index == left - 1:
                        continue
                    if row[column_index] is not None and row[column_index] != "":
                        raise ValueError(f"合并「{self.merges[i]}」会丢失 {get_column_letter(column_index + 1)}{row_index + 1} 的内容，请将内容移到左上角或调整范围")
        if self.format_cells > MAX_FORMAT_CELLS:
            raise ValueError(f"单个工作表格式区域累计不能超过 {MAX_FORMAT_CELLS} 个单元格")
        return self

    @property
    def format_cells(self) -> int:
        return sum((right - left + 1) * (bottom - top + 1)
                   for left, top, right, bottom in [_bounds(item.range) for item in self.styles] + [_bounds(item) for item in self.merges])


class WorkbookSpec(_Spec):
    sheets: list[CreatedSheet] = Field(min_length=1, max_length=MAX_SHEETS, description="要从零创建的工作表，支持多个 sheet。")

    @model_validator(mode="after")
    def validate_sheets(self) -> WorkbookSpec:
        names = [sheet.name.casefold() for sheet in self.sheets]
        if len(names) != len(set(names)):
            raise ValueError("工作表名称不能重复（不区分大小写）")
        if sum(sum(len(row) for row in sheet.rows) for sheet in self.sheets) > MAX_GRID_CELLS:
            raise ValueError(f"整个工作簿数据网格累计最多 {MAX_GRID_CELLS} 个单元格")
        if sum(sheet.format_cells for sheet in self.sheets) > MAX_FORMAT_CELLS:
            raise ValueError(f"整个工作簿格式区域累计不能超过 {MAX_FORMAT_CELLS} 个单元格")
        return self


def workbook_tool_schema() -> dict[str, Any]:
    """为 OpenAI 兼容工具调用内联 Pydantic 的 $ref，避免服务商不支持引用。"""
    schema = WorkbookSpec.model_json_schema()
    definitions = schema.pop("$defs", {})

    def expand(value: Any) -> Any:
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if "$ref" in value:
                base = expand(definitions[value["$ref"].rsplit("/", 1)[1]])
                return {**base, **{key: expand(item) for key, item in value.items() if key != "$ref"}}
            return {key: expand(item) for key, item in value.items()}
        return value

    return expand(schema)


def _apply_style(sheet: Any, style: RangeStyle) -> None:
    left, top, right, bottom = _bounds(style.range)
    font_attrs = {key: getattr(style, field) for key, field in {"bold": "bold", "italic": "italic", "size": "font_size", "color": "font_color"}.items()
                  if getattr(style, field) is not None}
    alignment_attrs = {field: getattr(style, field) for field in ("horizontal", "vertical", "wrap_text") if getattr(style, field) is not None}
    for row in sheet.iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right):
        for cell in row:
            if font_attrs:
                font = copy(cell.font)
                for key, value in font_attrs.items():
                    setattr(font, key, value)
                cell.font = font
            if alignment_attrs:
                alignment = copy(cell.alignment)
                for key, value in alignment_attrs.items():
                    setattr(alignment, key, value)
                cell.alignment = alignment
            if style.fill is not None:
                cell.fill = PatternFill("solid", fgColor=style.fill)
            if style.border is not None:
                side = Side(style=None if style.border == "none" else style.border, color="FFD1D5DB")
                cell.border = Border(left=side, right=side, top=side, bottom=side)
            if style.number_format is not None:
                cell.number_format = style.number_format


def write_created_workbook(path: Path, spec: WorkbookSpec | dict[str, Any]) -> list[dict[str, Any]]:
    """校验后创建独立 xlsx，返回实际工作表布局；拒绝覆盖任何已有文件。"""
    validated = WorkbookSpec.model_validate(spec)
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise ValueError("新建工作簿只支持 .xlsx 格式")
    if path.exists():
        raise ValueError("输出文件已存在，请使用新的文件名，不能覆盖原文件")
    workbook = Workbook()
    workbook.remove(workbook.active)
    workbook.calculation = CalcProperties(calcId=0, fullCalcOnLoad=True, forceFullCalc=True, calcMode="auto")
    written = []
    temporary: Path | None = None
    try:
        for item in validated.sheets:
            sheet = workbook.create_sheet(item.name)
            widths: dict[int, int] = {}
            formulas = 0
            for row_index, row in enumerate(item.rows, start=1):
                for column_index, value in enumerate(row, start=1):
                    if value is None:
                        continue
                    cell = sheet.cell(row_index, column_index)
                    if isinstance(value, FormulaCell):
                        cell.value = value.formula
                        formulas += 1
                    elif isinstance(value, DateCell):
                        cell.value = _parse_date(value.date)
                        cell.number_format = "yyyy-mm-dd hh:mm:ss" if isinstance(cell.value, dt.datetime) else "yyyy-mm-dd"
                    else:
                        cell.value = value
                        if isinstance(value, str):
                            cell.data_type = "s"
                    cell.alignment = Alignment(vertical="center")
                    if row_index <= 500:
                        text = value.date if isinstance(value, DateCell) else str(value) if not isinstance(value, FormulaCell) else ""
                        widths[column_index] = max(widths.get(column_index, 8), min(60, sum(2 if ord(c) > 0x2E7F else 1 for c in text[:80]) + 2))
            for column, width in widths.items():
                sheet.column_dimensions[get_column_letter(column)].width = width
            for column, width in item.column_widths.items():
                sheet.column_dimensions[column].width = width
            for row, height in item.row_heights.items():
                sheet.row_dimensions[int(row)].height = height
            for style in item.styles:
                _apply_style(sheet, style)
            for area in item.merges:
                sheet.merge_cells(area)
            if item.freeze_panes is not None:
                sheet.freeze_panes = item.freeze_panes
            if item.auto_filter is not None:
                sheet.auto_filter.ref = item.auto_filter
            written.append({"sheet": sheet.title, "rows": sheet.max_row, "columns": sheet.max_column,
                            "merges": list(item.merges), "formula_cells": formulas})
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=".sheetmate_create_", suffix=".xlsx", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
        workbook.save(temporary)
        # 同目录硬链接原子交付，且已有路径（含竞态）不会被覆盖。
        os.link(temporary, path)
        return written
    finally:
        workbook.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)
