"""写回：把结果表写成新的 Excel 文件（源文件只读，永远不覆盖）。

格式约定：表头加粗灰底、冻结首行、按内容自动列宽、日期按 yyyy-mm-dd 显示。
支持按某列拆成多个 sheet（例如按「归属」每个归属一个 tab）。
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from sheetmate.engine.tables.sql import clean_float

_HEADER_FONT = Font(bold=True, color="111111")
_HEADER_FILL = PatternFill("solid", fgColor="F2F2F2")
_INVALID_SHEET_CHARS = re.compile(r"[\[\]:*?/\\]")


@dataclass
class SheetSpec:
    """要写出的一个 sheet。"""

    name: str
    df: pd.DataFrame
    split_by: str | None = None  # 按这一列的取值拆成多个 sheet
    meta: dict[str, Any] = field(default_factory=dict)


def safe_sheet_name(name: str, used: set[str]) -> str:
    text = _INVALID_SHEET_CHARS.sub("_", str(name or "Sheet")).strip("'") or "Sheet"
    text = text[:31]
    candidate, i = text, 2
    while candidate.lower() in {u.lower() for u in used}:
        suffix = f"_{i}"
        candidate = text[: 31 - len(suffix)] + suffix
        i += 1
    used.add(candidate)
    return candidate


def write_excel(path: Path, sheets: list[SheetSpec]) -> list[dict[str, Any]]:
    """写出 Excel，返回每个 sheet 的名字和行数。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook(write_only=True)
    used: set[str] = set()
    written: list[dict[str, Any]] = []
    for spec in _expand_splits(sheets):
        title = safe_sheet_name(spec.name, used)
        _write_sheet(workbook, title, spec.df)
        written.append({"sheet": title, "rows": int(len(spec.df)), "columns": [str(c) for c in spec.df.columns]})
    if not written:
        _write_sheet(workbook, "Sheet1", pd.DataFrame())
        written.append({"sheet": "Sheet1", "rows": 0, "columns": []})
    workbook.save(path)
    return written


def _expand_splits(sheets: list[SheetSpec]) -> list[SheetSpec]:
    result: list[SheetSpec] = []
    for spec in sheets:
        if not spec.split_by or spec.split_by not in spec.df.columns:
            result.append(spec)
            continue
        groups = spec.df.groupby(spec.df[spec.split_by].fillna("(空)").astype(str), sort=False)
        for value, part in groups:
            result.append(SheetSpec(name=str(value), df=part.reset_index(drop=True)))
    return result


def _write_sheet(workbook: Workbook, title: str, df: pd.DataFrame) -> None:
    ws = workbook.create_sheet(title=title)
    columns = [str(c) for c in df.columns]
    widths = [_display_width(c) for c in columns]
    sample = df.head(500)
    for i, col in enumerate(df.columns):
        for value in sample[col].tolist():
            widths[i] = max(widths[i], _display_width(_excel_value(value)))
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = min(max(width + 2, 8), 60)
    ws.freeze_panes = "A2"

    header = []
    for name in columns:
        cell = WriteOnlyCell(ws, value=name)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL
        cell.alignment = Alignment(vertical="center")
        header.append(cell)
    ws.append(header)

    date_cols = {i for i, col in enumerate(df.columns) if pd.api.types.is_datetime64_any_dtype(df[col])}
    for row in df.itertuples(index=False, name=None):
        values = []
        for i, value in enumerate(row):
            v = _excel_value(value)
            if i in date_cols and isinstance(v, dt.datetime):
                cell = WriteOnlyCell(ws, value=v)
                cell.number_format = "yyyy-mm-dd" if v.time() == dt.time() else "yyyy-mm-dd hh:mm:ss"
                values.append(cell)
            else:
                values.append(v)
        ws.append(values)


def _excel_value(value: Any) -> Any:
    """转成 openpyxl 能写的原生类型。"""
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        v = float(value)
        if not math.isfinite(v):
            return None
        v = clean_float(v, 15)  # 去掉浮点尾数（341.73999999999995 → 341.74），与 Excel 的 15 位精度一致
        return int(v) if v.is_integer() and abs(v) < 1e15 else v
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.to_pydatetime()
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, (list, tuple, dict)):
        return str(value)
    return value


def _display_width(value: Any) -> int:
    if value is None:
        return 0
    text = value.strftime("%Y-%m-%d") if isinstance(value, (dt.date, dt.datetime)) else str(value)
    return sum(2 if ord(ch) > 0x2E7F else 1 for ch in text[:80])
