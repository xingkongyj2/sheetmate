"""工作表 XML 的行 / 单元格模型。

只把 <sheetData> 里的行和单元格拆开管理；<sheetData> 之前（head）与之后（tail）保持文本。
未被修改的行直接输出原始文本，保证字节不变。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.cell import coordinate_from_string

from sheetmate.engine.ooxml.errors import OoxmlUnsupported
from sheetmate.engine.ooxml.xmlutil import check_text, escape, parse_attrs, render_attrs, unescape

_SHEET_DATA = re.compile(r"<sheetData\b[^>]*?(/?)>")
_ROW = re.compile(r"<row\b([^>]*?)(?:/>|>(.*?)</row>)", re.S)
_CELL = re.compile(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", re.S)
_F = re.compile(r"<f\b([^>]*?)(?:/>|>(.*?)</f>)", re.S)
F_ELEMENT = _F
_DIMENSION = re.compile(r'<dimension\s+ref="[^"]*"\s*/>')
FormulaFn = Callable[[str, dict[str, str]], "str | None"]
MAX_EXACT_INTEGER = 10 ** 15


@dataclass
class Cell:
    col: int  # 1 起
    attrs: dict[str, str]
    inner: str = ""

    @property
    def has_formula(self) -> bool:
        return bool(self.inner) and _F.search(self.inner) is not None

    def xml(self, row: int) -> str:
        attrs = dict(self.attrs)
        attrs["r"] = f"{get_column_letter(self.col)}{row}"
        return f"<c{render_attrs(attrs)}>{self.inner}</c>" if self.inner else f"<c{render_attrs(attrs)}/>"


@dataclass
class Row:
    number: int
    attrs: dict[str, str]
    cells: list[Cell] = field(default_factory=list)
    raw: str | None = None  # 未修改时原样输出

    def touch(self) -> None:
        self.raw = None

    def cell(self, col: int) -> Cell | None:
        return next((cell for cell in self.cells if cell.col == col), None)

    def xml(self) -> str:
        if self.raw is not None:
            return self.raw
        attrs = dict(self.attrs)
        attrs["r"] = str(self.number)
        body = "".join(cell.xml(self.number) for cell in self.cells)
        return f"<row{render_attrs(attrs)}>{body}</row>" if body else f"<row{render_attrs(attrs)}/>"


class SheetXml:
    def __init__(self, head: str, rows: list[Row], tail: str):
        self.head, self.rows, self.tail = head, rows, tail
        self._index: dict[int, Row] | None = None

    # ---- 解析 / 序列化 ----
    @classmethod
    def parse(cls, xml: str) -> "SheetXml":
        opening = _SHEET_DATA.search(xml)
        if opening is None or opening.group(1):
            raise OoxmlUnsupported("工作表没有 sheetData 或为空工作表")
        closing = xml.find("</sheetData>", opening.end())
        if closing < 0:
            raise OoxmlUnsupported("sheetData 未正常结束")
        body = xml[opening.end():closing]
        rows: list[Row] = []
        for match in _ROW.finditer(body):
            attrs = parse_attrs(match.group(1))
            if "r" not in attrs or not attrs["r"].isdigit():
                raise OoxmlUnsupported("存在没有行号的行")
            row = Row(int(attrs["r"]), attrs, [], match.group(0))
            previous = 0
            for cell_match in _CELL.finditer(match.group(2) or ""):
                cell_attrs = parse_attrs(cell_match.group(1))
                reference = cell_attrs.get("r")
                if reference is None:
                    raise OoxmlUnsupported("存在没有坐标的单元格")
                letters, number = coordinate_from_string(reference)
                col = column_index_from_string(letters)
                if number != row.number or col <= previous:
                    raise OoxmlUnsupported("单元格坐标与所在行不一致或顺序错误")
                previous = col
                row.cells.append(Cell(col, cell_attrs, cell_match.group(2) or ""))
            if _CELL.sub("", match.group(2) or "").strip():
                raise OoxmlUnsupported("行内含有非单元格元素")
            rows.append(row)
        if _ROW.sub("", body).strip():
            raise OoxmlUnsupported("sheetData 内含有非行元素")
        numbers = [row.number for row in rows]
        if numbers != sorted(set(numbers)):
            raise OoxmlUnsupported("行号重复或未按升序排列")
        return cls(xml[:opening.end()], rows, xml[closing + len("</sheetData>"):])

    def serialize(self) -> str:
        head = self.head
        dimension = self.dimension()
        if dimension and _DIMENSION.search(head):
            head = _DIMENSION.sub(f'<dimension ref="{dimension}"/>', head, count=1)
        return head + "".join(row.xml() for row in self.rows) + "</sheetData>" + self.tail

    def dimension(self) -> str | None:
        cells = [(row.number, cell.col) for row in self.rows for cell in row.cells]
        if not cells:
            return None
        rows, cols = [item[0] for item in cells], [item[1] for item in cells]
        first, last = f"{get_column_letter(min(cols))}{min(rows)}", f"{get_column_letter(max(cols))}{max(rows)}"
        return first if first == last else f"{first}:{last}"

    # ---- 查询 ----
    def reindex(self) -> None:
        self._index = None

    def row(self, number: int) -> Row | None:
        if self._index is None:
            self._index = {row.number: row for row in self.rows}
        return self._index.get(number)

    def max_column(self) -> int:
        return max((cell.col for row in self.rows for cell in row.cells), default=0)

    # ---- 公式 ----
    def rewrite_formulas(self, fn: FormulaFn) -> int:
        """对每个带文本的 <f> 调用 fn(unescaped_text, attrs)；返回新文本则替换（None 表示不改）。返回被改动的公式数。"""
        changed = 0
        for row in self.rows:
            for cell in row.cells:
                if not cell.inner or "<f" not in cell.inner:
                    continue
                new_inner, count = rewrite_f_elements(cell.inner, fn)
                if count:
                    cell.inner = new_inner
                    row.touch()
                    changed += count
        return changed

    def formulas_present(self, kinds: tuple[str, ...] = ()) -> bool:
        for row in self.rows:
            for cell in row.cells:
                if not cell.inner:
                    continue
                for match in _F.finditer(cell.inner):
                    if not kinds or parse_attrs(match.group(1)).get("t") in kinds:
                        return True
        return False


def rewrite_f_elements(xml: str, fn: FormulaFn) -> tuple[str, int]:
    changed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal changed
        if match.group(2) is None:
            return match.group(0)  # 共享公式的子单元格等没有文本的 <f/>
        text = unescape(match.group(2))
        new = fn(text, parse_attrs(match.group(1)))
        if new is None or new == text:
            return match.group(0)
        changed += 1
        return f"<f{match.group(1)}>{escape(new)}</f>"

    return _F.sub(replace, xml), changed


def cell_xml(reference: str, style: str | None, value: Any) -> Cell:
    """按值类型构造一个单元格。日期等需要匹配数字格式的类型不在此处理。"""
    letters, _ = coordinate_from_string(reference)
    col = column_index_from_string(letters)
    attrs: dict[str, str] = {"r": reference}
    if style:
        attrs["s"] = style
    if value is None:
        return Cell(col, attrs)
    if isinstance(value, bool):
        attrs["t"] = "b"
        return Cell(col, attrs, f"<v>{int(value)}</v>")
    if isinstance(value, int):
        if abs(value) > MAX_EXACT_INTEGER:
            raise OoxmlUnsupported("超过 15 位的整数需要按文本写入")
        return Cell(col, attrs, f"<v>{value}</v>")
    if isinstance(value, float):
        return Cell(col, attrs, f"<v>{value!r}</v>")
    if isinstance(value, str):
        attrs["t"] = "inlineStr"
        space = ' xml:space="preserve"' if value != value.strip() or "\n" in value else ""
        # 内联字符串不需要改动 sharedStrings.xml，也就不会影响其他单元格或其他工作表。
        return Cell(col, attrs, f"<is><t{space}>{escape(check_text(value))}</t></is>")
    raise OoxmlUnsupported(f"不支持写入 {type(value).__name__} 类型的值")
