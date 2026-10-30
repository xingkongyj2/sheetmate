"""Excel / CSV 读取。

真实业务里的 Excel 往往不规整，这里统一处理成「一张干净的表」：

1. 多 sheet：逐个读取（隐藏的 sheet 默认跳过）
2. 合并单元格：用左上角的值填满整个合并区域
3. 表头探测：表头不一定在第 1 行（上面常有「XX 公司 10 月账单」这样的标题行）
4. 多行表头：上一行是横向合并的分组表头时，拼成「分组_子列」
5. 列名清洗：去换行/多余空格，空列名补「列N」，重名加序号
6. 类型推断：数字、日期、文本；以 0 开头或很长的编号（运单号等）保持文本，避免丢精度

读取结果是 SheetData（DataFrame + 表头信息）。表头信息会存进 Recipe 的 inputs，
下个月重复执行时按同样的表头位置读取。
"""

from __future__ import annotations

import datetime as dt
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd

SUPPORTED_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".csv"}
_MAX_ROWS = 200_000
_HEADER_SCAN_ROWS = 30
_EXCEL_ERRORS = {"#N/A", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#NULL!", "#SPILL!", "#CALC!"}
_NUMERIC_RE = re.compile(r"^[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?$")
_CJK = "一-鿿"


class ReadError(ValueError):
    """文件无法读取（格式不支持、文件损坏、没有数据）。"""


@dataclass
class HeaderInfo:
    """一个 sheet 的表头结构。"""

    header_row: int = 1  # 表头最后一行的 Excel 行号（从 1 开始）
    header_rows: int = 1  # 表头占几行；多行表头时 > 1
    title: str = ""  # 表头上方的标题/说明文字

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HeaderInfo | None:
        if not data:
            return None
        return cls(
            header_row=int(data.get("header_row", 1)),
            header_rows=int(data.get("header_rows", 1)),
            title=str(data.get("title", "")),
        )


@dataclass
class SheetData:
    """读取后的一个工作表。"""

    name: str
    df: pd.DataFrame
    header: HeaderInfo
    hidden: bool = False


@dataclass
class _RawSheet:
    name: str
    grid: list[list[Any]]
    merged_rows: set[int]  # 含横向合并单元格的行（0 起）
    hidden: bool = False


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------


def read_workbook(
    path: str | Path,
    header_hints: dict[str, dict[str, Any]] | None = None,
    include_hidden: bool = False,
) -> list[SheetData]:
    """读取文件里的所有工作表。header_hints: {sheet 名: HeaderInfo 字典}，重复执行时沿用表头位置。"""
    path = Path(path)
    raws = _load_raw_sheets(path)
    sheets: list[SheetData] = []
    for raw in raws:
        if raw.hidden and not include_hidden:
            continue
        hint = HeaderInfo.from_dict((header_hints or {}).get(raw.name))
        sheet = _raw_to_sheet(raw, hint)
        if sheet is not None:
            sheets.append(sheet)
    if not sheets:
        raise ReadError(f"文件「{path.name}」里没有可读取的数据")
    return sheets


def read_sheet(
    path: str | Path,
    sheet: str | None = None,
    header_hint: dict[str, Any] | None = None,
) -> SheetData:
    """读取单个工作表；sheet 为空时取第一个有数据的工作表。"""
    hints = {sheet: header_hint} if sheet and header_hint else None
    sheets = read_workbook(path, header_hints=hints)
    if sheet is None:
        return sheets[0]
    for item in sheets:
        if item.name == sheet:
            return item
    names = "、".join(s.name for s in sheets)
    raise ReadError(f"文件「{Path(path).name}」里没有工作表「{sheet}」，现有：{names}")


# ---------------------------------------------------------------------------
# 原始网格读取（按文件类型）
# ---------------------------------------------------------------------------


def _load_raw_sheets(path: Path) -> list[_RawSheet]:
    if not path.exists():
        raise ReadError(f"文件不存在：{path.name}")
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ReadError(f"不支持的文件类型：{suffix}（支持 xlsx / xlsm / xls / csv）")
    try:
        if suffix in {".xlsx", ".xlsm"}:
            return _load_xlsx(path)
        if suffix == ".xls":
            return _load_xls(path)
        return _load_csv(path)
    except ReadError:
        raise
    except Exception as exc:  # 文件损坏、加密等
        raise ReadError(f"读取「{path.name}」失败：{exc}") from exc


def _load_xlsx(path: Path) -> list[_RawSheet]:
    """openpyxl 读取（data_only=True 取公式的缓存结果），并展开合并单元格。"""
    workbook = openpyxl.load_workbook(path, data_only=True)
    raws: list[_RawSheet] = []
    for ws in workbook.worksheets:
        max_row = min(ws.max_row or 0, _MAX_ROWS)
        max_col = ws.max_column or 0
        grid = [list(row) for row in ws.iter_rows(min_row=1, max_row=max_row, max_col=max_col, values_only=True)]
        merged_rows: set[int] = set()
        for rng in ws.merged_cells.ranges:
            if rng.min_row > max_row:
                continue
            origin = grid[rng.min_row - 1][rng.min_col - 1]
            if rng.max_col > rng.min_col:
                merged_rows.update(range(rng.min_row - 1, min(rng.max_row, max_row)))
            for r in range(rng.min_row - 1, min(rng.max_row, max_row)):
                for c in range(rng.min_col - 1, min(rng.max_col, max_col)):
                    grid[r][c] = origin
        raws.append(_RawSheet(ws.title, grid, merged_rows, hidden=ws.sheet_state != "visible"))
    workbook.close()
    return raws


def _load_xls(path: Path) -> list[_RawSheet]:
    frames = pd.read_excel(path, sheet_name=None, header=None, engine="xlrd", dtype=object)
    return [
        _RawSheet(str(name), [[None if _is_blank(v) else v for v in row] for row in frame.values.tolist()], set())
        for name, frame in frames.items()
    ]


def _load_csv(path: Path) -> list[_RawSheet]:
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            frame = pd.read_csv(
                path, header=None, dtype=str, keep_default_na=False, sep=None, engine="python", encoding=encoding
            )
            grid = [[None if _is_blank(v) else v for v in row] for row in frame.values.tolist()]
            return [_RawSheet(path.stem, grid, set())]
        except (UnicodeDecodeError, UnicodeError, pd.errors.ParserError) as exc:
            last_error = exc
    raise ReadError(f"无法识别 CSV 文件编码：{last_error}")


# ---------------------------------------------------------------------------
# 网格 → DataFrame
# ---------------------------------------------------------------------------


def _raw_to_sheet(raw: _RawSheet, hint: HeaderInfo | None) -> SheetData | None:
    grid = [[_clean_cell(v) for v in row] for row in raw.grid]
    grid = _trim_trailing_empty_rows(grid)
    if not grid:
        return None

    header_idx, header_rows = _detect_header(grid, raw.merged_rows, hint)
    header_block = grid[header_idx - header_rows + 1 : header_idx + 1]
    data_rows = [row for row in grid[header_idx + 1 :] if any(not _is_blank(v) for v in row)]
    title = _title_text(grid[: header_idx - header_rows + 1])

    width = max([len(r) for r in header_block] + [len(r) for r in data_rows] + [0])
    columns = _build_columns(header_block, width)
    rows = [row + [None] * (width - len(row)) for row in data_rows]

    # 去掉表头和数据都为空的尾部列
    keep = [
        i
        for i in range(width)
        if not columns[i].startswith("列") or any(not _is_blank(r[i]) for r in rows)
    ]
    columns = _dedupe([columns[i] for i in keep])
    data = {col: _infer_series([r[i] for r in rows]) for col, i in zip(columns, keep)}
    df = pd.DataFrame(data, columns=columns)
    df = df.reset_index(drop=True)

    header = HeaderInfo(header_row=header_idx + 1, header_rows=header_rows, title=title)
    return SheetData(name=raw.name, df=df, header=header, hidden=raw.hidden)


def _detect_header(grid: list[list[Any]], merged_rows: set[int], hint: HeaderInfo | None) -> tuple[int, int]:
    """返回 (表头最后一行的下标, 表头行数)。

    规则：表头是「第一行足够宽、大部分是文字、值基本不重复」的行；
    如果它上一行是横向合并的分组行（且有 ≥2 个不同分组），视为两行表头。
    """
    if hint and 1 <= hint.header_row <= len(grid):
        return hint.header_row - 1, max(1, min(hint.header_rows, hint.header_row))

    widths = [len(_non_empty(row)) for row in grid[:200]]
    width = max(widths) if widths else 0
    header_idx: int | None = None
    first_wide: int | None = None
    for i in range(min(len(grid), _HEADER_SCAN_ROWS)):
        cells = _non_empty(grid[i])
        if len(cells) < max(1, math.ceil(width * 0.5)):
            continue
        if first_wide is None:
            first_wide = i
        texty = sum(1 for v in cells if _is_texty(v)) / len(cells)
        distinct = len({str(v).strip() for v in cells}) / len(cells)
        if texty >= 0.6 and distinct >= 0.6:
            header_idx = i
            break
    if header_idx is None:
        header_idx = first_wide if first_wide is not None else 0

    header_rows = 1
    above = header_idx - 1
    if above >= 0 and above in merged_rows:
        cells = _non_empty(grid[above])
        if len({str(v) for v in cells}) >= 2 and all(_is_texty(v) for v in cells):
            header_rows = 2
    return header_idx, header_rows


def _build_columns(header_block: list[list[Any]], width: int) -> list[str]:
    names: list[str] = []
    for c in range(width):
        parts: list[str] = []
        for row in header_block:
            value = row[c] if c < len(row) else None
            if _is_blank(value):
                continue
            text = _clean_header_text(value)
            if text and (not parts or parts[-1] != text):
                parts.append(text)
        names.append("_".join(parts) if parts else f"列{c + 1}")
    return names


def _clean_header_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, dt.datetime):
        value = value.strftime("%Y-%m-%d") if value.time() == dt.time() else value.strftime("%Y-%m-%d %H:%M")
    text = str(value).replace("\r", " ").replace("\n", " ").replace("\t", " ").replace("　", " ")
    text = re.sub(rf"(?<=[{_CJK}])\s+(?=[{_CJK}])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result: list[str] = []
    for name in names:
        if name not in seen:
            seen[name] = 1
            result.append(name)
            continue
        seen[name] += 1
        candidate = f"{name}_{seen[name]}"
        while candidate in seen:
            seen[name] += 1
            candidate = f"{name}_{seen[name]}"
        seen[candidate] = 1
        result.append(candidate)
    return result


def _title_text(rows: list[list[Any]]) -> str:
    texts: list[str] = []
    for row in rows:
        for value in _non_empty(row):
            text = str(value).strip()
            if text and text not in texts:
                texts.append(text)
    return " / ".join(texts)[:200]


# ---------------------------------------------------------------------------
# 类型推断
# ---------------------------------------------------------------------------


def _infer_series(values: list[Any]) -> pd.Series:
    """推断一列的类型：日期 / 数字 / 文本。宁可保守地当文本，也不能把编号转坏。"""
    non_null = [v for v in values if not _is_blank(v)]
    if not non_null:
        return pd.Series([None] * len(values), dtype=object)

    if all(isinstance(v, (dt.datetime, dt.date)) and not isinstance(v, bool) for v in non_null):
        return pd.to_datetime(pd.Series([None if _is_blank(v) else v for v in values]), errors="coerce")

    if all(isinstance(v, bool) for v in non_null):
        return pd.Series([None if _is_blank(v) else bool(v) for v in values], dtype=object)

    numeric = 0
    id_like = False
    for v in non_null:
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            numeric += 1
        elif isinstance(v, str) and _NUMERIC_RE.match(v.strip()):
            digits = v.strip().lstrip("+-").replace(",", "")
            if (len(digits) > 1 and digits[0] == "0" and not digits.startswith("0.")) or len(
                re.sub(r"\D", "", digits)
            ) >= 16:
                id_like = True
            numeric += 1
    if not id_like and numeric == len(non_null):
        parsed = [_parse_number(v) for v in values]
        finite = [p for p in parsed if p is not None]
        if finite and all(float(p).is_integer() and abs(p) < 2**53 for p in finite):
            return pd.Series([None if p is None else int(p) for p in parsed], dtype="Int64")
        return pd.Series([float("nan") if p is None else float(p) for p in parsed], dtype="float64")

    return pd.Series([None if _is_blank(v) else _to_text(v) for v in values], dtype=object)


def _parse_number(value: Any) -> float | int | None:
    if _is_blank(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value).strip().replace(",", "")
    if text.endswith("%"):
        return float(text[:-1]) / 100
    number = float(text)
    return int(number) if number.is_integer() and "." not in text else number


def _to_text(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, dt.datetime):
        return value.strftime("%Y-%m-%d") if value.time() == dt.time() else value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, dt.date):
        return value.isoformat()
    return str(value).strip()


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------


def _clean_cell(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text or text in _EXCEL_ERRORS:
            return None
        return text
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, float):
        return math.isnan(value)
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _is_texty(value: Any) -> bool:
    return isinstance(value, str) and not _NUMERIC_RE.match(value.strip())


def _non_empty(row: list[Any]) -> list[Any]:
    return [v for v in row if not _is_blank(v)]


def _trim_trailing_empty_rows(grid: list[list[Any]]) -> list[list[Any]]:
    end = len(grid)
    while end > 0 and all(_is_blank(v) for v in grid[end - 1]):
        end -= 1
    return grid[:end]
