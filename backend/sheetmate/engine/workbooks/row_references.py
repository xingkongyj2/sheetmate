"""Safely adjust Excel A1 references after worksheet rows are deleted.

Only ordinary A1 references are rewritten. Structured references, defined names,
array constants and other unsupported Excel reference forms are left alone or
rejected explicitly so a formula is never silently changed into a bad formula.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from openpyxl.formula import Tokenizer
from openpyxl.formula.tokenizer import TokenizerError

_CELL = re.compile(r"(?P<col>\$?[A-Za-z]{1,3})(?P<row>\$?\d+)$")
_ROW = re.compile(r"(?P<first>\$?\d+)(?::(?P<last>\$?\d+))?$")
_COL = re.compile(r"(?P<first>\$?[A-Za-z]{1,3})(?::(?P<last>\$?[A-Za-z]{1,3}))?$")


class _DeletedRows:
    """删行映射：被删的行不存在，其后的行整体上移。"""

    def __init__(self, deleted: set[int]):
        self.deleted = deleted

    def __bool__(self) -> bool:
        return bool(self.deleted)

    def __contains__(self, row: int) -> bool:
        return row in self.deleted

    def map_row(self, row: int) -> int:
        return row - _count_before(self.deleted, row)

    def map_range(self, first: int, last: int) -> list[int]:
        return _mapped_rows(first, last, self.deleted)


DeletedRows = _DeletedRows


class InsertedRows:
    """插入行映射：从 at 行起（含）的行整体下移 count 行。

    extend_end 为真时，恰好结束在 at-1 的区域引用（如 SUM(C3:C10) 且新行插在第 11 行）一起向下扩展，
    让“在数据区末尾追加”的新行自动被合计公式、筛选范围、条件格式等覆盖；单个单元格引用不扩展。
    """

    def __init__(self, at: int, count: int, extend_end: bool = False):
        self.at, self.count, self.extend_end = at, count, extend_end

    def __bool__(self) -> bool:
        return self.count > 0

    def __contains__(self, row: int) -> bool:  # 插入不会让任何已有行消失
        return False

    def map_row(self, row: int) -> int:
        return row + self.count if row >= self.at else row

    def map_range(self, first: int, last: int) -> list[int]:
        new_last = self.map_row(last)
        if self.extend_end and last == self.at - 1 and first < self.at:
            new_last = last + self.count
        return [self.map_row(first), new_last]


RowMap = _DeletedRows | InsertedRows


def as_row_map(value: "set[int] | RowMap") -> RowMap:
    return value if isinstance(value, (_DeletedRows, InsertedRows)) else _DeletedRows(value)


@dataclass(frozen=True)
class _Reference:
    prefix: str
    body: str
    sheet: str | None


def adjust_formula(formula: str, current_sheet: str, deleted: "dict[str, set[int] | RowMap]") -> str:
    """Adjust ordinary A1 references in *formula* after deleting worksheet rows.

    A direct reference to a deleted cell, an all-deleted range, a 3-D reference,
    or an external workbook reference raises ``ValueError``. Defined names and
    structured references are preserved unchanged because their rows cannot be
    safely inferred here.
    """
    if not isinstance(formula, str):
        return formula
    had_equals = formula.startswith("=")
    expression = formula if had_equals else "=" + formula
    try:
        tokens = Tokenizer(expression).items
    except TokenizerError as exc:
        raise ValueError(f"unsupported formula syntax: {exc}") from exc
    if any(token.type == "ARRAY" for token in tokens):
        raise ValueError("array formulas/constants are not supported for row deletion")
    pieces: list[str] = []
    for token in tokens:
        if token.type != "OPERAND" or token.subtype != "RANGE":
            pieces.append(token.value)
            continue
        pieces.append(_adjust_token(token.value, current_sheet, deleted))
    rendered = "".join(pieces)
    return "=" + rendered if had_equals else rendered


def shift_range_ref(reference: str, deleted: "set[int] | RowMap") -> str:
    """Shift an A1 range after deleting rows.

    Multiple areas separated by whitespace are handled independently. If every
    row in an area is deleted, that area is omitted; an empty return means all
    areas were deleted. Column-only ranges are unchanged.
    """
    if not reference:
        return ""
    deleted = as_row_map(deleted)
    areas = _split_areas(reference)
    shifted: list[str] = []
    for area in areas:
        value = _shift_area(area, deleted)
        if value:
            shifted.append(value)
    return " ".join(shifted)


def _adjust_token(token: str, current_sheet: str, deleted: dict[str, set[int]]) -> str:
    ref = _parse_reference(token)
    if ref is None:
        return token
    rows = _lookup_deleted(deleted, ref.sheet or current_sheet)
    if not rows:
        return token
    shifted = _shift_area(token, rows, reject_single=True)
    if not shifted:
        raise ValueError(f"reference {token!r} is fully deleted")
    return shifted


def _shift_area(area: str, deleted: "set[int] | RowMap", *, reject_single: bool = False) -> str:
    deleted = as_row_map(deleted)
    ref = _parse_reference(area)
    if ref is None:
        return area
    body = ref.body
    if _COL.fullmatch(body):
        return area
    row_match = _ROW.fullmatch(body)
    if row_match:
        first = int(row_match.group("first").lstrip("$"))
        last = int((row_match.group("last") or row_match.group("first")).lstrip("$"))
        if last < first:
            raise ValueError(f"reversed row range {area!r} is unsupported")
        mapped = deleted.map_range(first, last)
        if not mapped:
            return ""
        first_text = _shift_number(row_match.group("first"), mapped[0])
        if row_match.group("last") is None:
            return f"{ref.prefix}{first_text}"
        last_text = _shift_number(row_match.group("last"), mapped[-1])
        return f"{ref.prefix}{first_text}:{last_text}"
    parts = body.split(":")
    if len(parts) == 1:
        match = _CELL.fullmatch(parts[0])
        if not match:
            return area
        row = int(match.group("row").lstrip("$"))
        if row in deleted:
            if not reject_single:
                return ""
            raise ValueError(f"reference {area!r} points to a deleted cell")
        return f"{ref.prefix}{match.group('col')}{_shift_number(match.group('row'), deleted.map_row(row))}"
    if len(parts) != 2:
        raise ValueError(f"unsupported range reference {area!r}")
    start, end = (_CELL.fullmatch(part) for part in parts)
    if start is None or end is None:
        return area
    start_row = int(start.group("row").lstrip("$"))
    end_row = int(end.group("row").lstrip("$"))
    if end_row < start_row:
        raise ValueError(f"reversed cell range {area!r} is unsupported")
    mapped = deleted.map_range(start_row, end_row)
    if not mapped:
        return ""
    return (
        f"{ref.prefix}{start.group('col')}{_shift_number(start.group('row'), mapped[0])}:"
        f"{end.group('col')}{_shift_number(end.group('row'), mapped[-1])}"
    )


def _mapped_rows(first: int, last: int, deleted: set[int]) -> list[int]:
    removed = sorted(row for row in deleted if first <= row <= last)
    if len(removed) == last - first + 1:
        return []
    first_live = first
    while first_live in deleted:
        first_live += 1
    last_live = last
    while last_live in deleted:
        last_live -= 1
    return [first_live - _count_before(deleted, first_live), last_live - _count_before(deleted, last_live)]


def _count_before(deleted: set[int], row: int) -> int:
    return sum(item < row for item in deleted)


def _lookup_deleted(deleted: dict[str, set[int]], sheet: str) -> set[int] | None:
    if sheet in deleted:
        return deleted[sheet]
    folded = sheet.casefold()
    return next((rows for name, rows in deleted.items() if name.casefold() == folded), None)


def _shift_number(original: str, value: int) -> str:
    return ("$" if original.startswith("$") else "") + str(value)


def _parse_reference(value: str) -> _Reference | None:
    if "[" in value or "]" in value:
        if "!" in value:
            raise ValueError(f"external workbook reference is unsupported: {value!r}")
        return None
    bang = _find_sheet_separator(value)
    if bang < 0:
        prefix, body, sheet = "", value, None
    else:
        prefix, body = value[: bang + 1], value[bang + 1 :]
        sheet_text = value[:bang]
        if ":" in sheet_text:
            raise ValueError(f"3-D sheet reference is unsupported: {value!r}")
        sheet = _decode_sheet(sheet_text)
    if _CELL.fullmatch(body) or ":" in body and (_CELL.fullmatch(body.split(":")[0]) and _CELL.fullmatch(body.split(":")[-1])):
        return _Reference(prefix, body, sheet)
    if _ROW.fullmatch(body) or _COL.fullmatch(body):
        return _Reference(prefix, body, sheet)
    return None


def _find_sheet_separator(value: str) -> int:
    quoted = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'":
            if quoted and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif char == "!" and not quoted:
            return index
        index += 1
    return -1


def _decode_sheet(value: str) -> str:
    if value.startswith("'") and value.endswith("'"):
        return value[1:-1].replace("''", "'")
    return value


def _split_areas(value: str) -> list[str]:
    areas: list[str] = []
    start = 0
    quoted = False
    index = 0
    while index < len(value):
        char = value[index]
        if char == "'":
            if quoted and index + 1 < len(value) and value[index + 1] == "'":
                index += 2
                continue
            quoted = not quoted
        elif char.isspace() and not quoted:
            if start < index:
                areas.append(value[start:index])
            start = index + 1
        index += 1
    if start < len(value):
        areas.append(value[start:])
    return areas
