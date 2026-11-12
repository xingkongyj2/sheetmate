"""旧版 .xls（BIFF）→ .xlsx 的内置转换。

.xls 无法被 openpyxl 读取，也没有可用的写入库，所以"保留原格式的编辑"必须先转成 .xlsx 工作副本。
这里用 xlrd 读取值与样式（formatting_info），再用 openpyxl 写出：

- 保留：值（文本 / 数字 / 日期 / 布尔 / 错误）、合并单元格、列宽、行高、隐藏的行 / 列 / 工作表、
  字体（名称 / 字号 / 加粗 / 斜体 / 下划线 / 删除线 / 颜色 / 上下标）、填充、边框、对齐（含换行 / 缩进 / 缩小填充 / 旋转）、
  数字格式、冻结窗格、批注、超链接；
- 不保留：公式（xls 里只有缓存的计算结果，转换后是数值）、条件格式、数据验证、图表、图片、宏。

xlrd 的样式对象字段名与 openpyxl 不同（hor_align / underline_type / colour_index ……），集中在这里映射并有测试覆盖，
不要在别处凭印象重写一遍。
"""

from __future__ import annotations

import datetime as dt
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import xlrd
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from sheetmate.engine.workbooks.atomic import atomic_path
from sheetmate.engine.workbooks.reader import ReadError

LIMITATIONS = "旧版 .xls 已转换为 .xlsx 工作副本：公式只保留计算结果；条件格式、数据验证、图表、图片、宏不会转换。"
_OLE_MAGIC = bytes.fromhex("D0CF11E0A1B11AE1")

_HORIZONTAL = {0: None, 1: "left", 2: "center", 3: "right", 4: "fill", 5: "justify", 6: "centerContinuous", 7: "distributed"}
_VERTICAL = {0: "top", 1: "center", 2: "bottom", 3: "justify", 4: "distributed"}
_BORDER = {1: "thin", 2: "medium", 3: "dashed", 4: "dotted", 5: "thick", 6: "double", 7: "hair", 8: "mediumDashed",
           9: "dashDot", 10: "mediumDashDot", 11: "dashDotDot", 12: "mediumDashDotDot", 13: "slantDashDot"}
_UNDERLINE = {1: "single", 2: "double", 0x21: "singleAccounting", 0x22: "doubleAccounting"}
_VERT_ALIGN = {1: "superscript", 2: "subscript"}
_PATTERN = {1: "solid", 2: "mediumGray", 3: "darkGray", 4: "lightGray", 5: "darkHorizontal", 6: "darkVertical",
            7: "darkDown", 8: "darkUp", 9: "darkGrid", 10: "darkTrellis", 11: "lightHorizontal", 12: "lightVertical",
            13: "lightDown", 14: "lightUp", 15: "lightGrid", 16: "lightTrellis", 17: "gray125", 18: "gray0625"}
_AUTOMATIC_COLOURS = {64, 65, 0x7FFF}  # 系统前景 / 背景 / 窗口文本：交给 Excel 自动决定
_VISIBILITY = {0: "visible", 1: "hidden", 2: "veryHidden"}


@dataclass
class XlsConversion:
    sheets: int = 0
    cells: int = 0
    warnings: list[str] = field(default_factory=list)  # 本次转换中发现的具体问题（不含固定的 LIMITATIONS）


def convert_xls_to_xlsx(source: Path, destination: Path) -> XlsConversion:
    """把 source（.xls）转换为 destination（.xlsx）；原子写入，失败不留下半成品。

    实际是 .xlsx 却用了 .xls 扩展名的文件直接复制；既不是 BIFF 也不是 OOXML 的（如网页 / XML 表格）抛 ReadError。
    """
    source, destination = Path(source), Path(destination)
    with source.open("rb") as stream:
        magic = stream.read(8)
    if magic.startswith(b"PK"):
        with atomic_path(destination) as staging:
            shutil.copyfile(source, staging)
        return XlsConversion(warnings=["文件扩展名是 .xls，但内容已经是 .xlsx，已原样使用。"])
    if magic != _OLE_MAGIC:
        raise ReadError("这个 .xls 不是标准的 Excel 二进制文件（可能是网页或 XML 导出），请在 Excel 里另存为 .xlsx 后再上传。")
    try:
        book = xlrd.open_workbook(str(source), formatting_info=True)
    except (xlrd.XLRDError, xlrd.compdoc.CompDocError, NotImplementedError, AssertionError, IndexError) as exc:
        raise ReadError(f"无法读取 .xls 文件：{exc}") from exc
    report = XlsConversion()
    workbook = Workbook()
    workbook.remove(workbook.active)
    styles = _StyleCache(book)
    for sheet in book.sheets():
        _convert_sheet(workbook, book, sheet, styles, report)
    if not workbook.worksheets:
        raise ReadError(".xls 文件中没有工作表")
    with atomic_path(destination) as staging:
        workbook.save(staging)
    return report


class _StyleCache:
    """按 xf 序号缓存转换后的 openpyxl 样式，避免每个单元格重复构造。"""

    def __init__(self, book: xlrd.Book):
        self.book = book
        self._cache: dict[int, dict[str, Any] | None] = {}

    def colour(self, index: int | None) -> str | None:
        if index is None or index in _AUTOMATIC_COLOURS:
            return None
        rgb = self.book.colour_map.get(index)
        return "%02X%02X%02X" % rgb if rgb else None

    def get(self, xf_index: int) -> dict[str, Any] | None:
        """返回该样式要设置的 openpyxl 属性；None 表示默认样式（无需设置）。"""
        if xf_index not in self._cache:
            self._cache[xf_index] = self._build(xf_index)
        return self._cache[xf_index]

    def _build(self, xf_index: int) -> dict[str, Any] | None:
        if xf_index >= len(self.book.xf_list):
            return None
        xf = self.book.xf_list[xf_index]
        font = self.book.font_list[xf.font_index]
        result: dict[str, Any] = {
            "font": Font(name=font.name, size=(font.height or 200) / 20, bold=bool(font.bold), italic=bool(font.italic),
                         underline=_UNDERLINE.get(font.underline_type), strike=bool(font.struck_out),
                         color=self.colour(font.colour_index), vertAlign=_VERT_ALIGN.get(font.escapement)),
        }
        align = xf.alignment
        result["alignment"] = Alignment(
            horizontal=_HORIZONTAL.get(align.hor_align), vertical=_VERTICAL.get(align.vert_align),
            wrap_text=bool(align.text_wrapped), indent=align.indent_level or 0,
            shrink_to_fit=bool(align.shrink_to_fit), text_rotation=align.rotation or 0)
        border = xf.border
        result["border"] = Border(**{
            side: Side(style=_BORDER.get(getattr(border, f"{side}_line_style")),
                       color=self.colour(getattr(border, f"{side}_colour_index")))
            if _BORDER.get(getattr(border, f"{side}_line_style")) else Side()
            for side in ("left", "right", "top", "bottom")})
        pattern = _PATTERN.get(xf.background.fill_pattern)
        foreground = self.colour(xf.background.pattern_colour_index)
        if pattern and foreground:  # 前景色是"自动"时不设填充，避免把文字盖住
            colours = {"fgColor": foreground}
            background = self.colour(xf.background.background_colour_index)
            if background:
                colours["bgColor"] = background
            result["fill"] = PatternFill(fill_type=pattern, **colours)
        number = self.book.format_map.get(xf.format_key)
        if number is not None and number.format_str and number.format_str != "General":
            result["number_format"] = number.format_str
        return result


def _convert_sheet(workbook: Workbook, book: xlrd.Book, sheet: xlrd.sheet.Sheet, styles: _StyleCache, report: XlsConversion) -> None:
    target = workbook.create_sheet(sheet.name)
    target.sheet_state = _VISIBILITY.get(sheet.visibility, "visible")
    report.sheets += 1
    for row_index in range(sheet.nrows):
        for column_index, cell in enumerate(sheet.row(row_index)):
            value = _value(book, cell, report, f"{sheet.name}!{get_column_letter(column_index + 1)}{row_index + 1}")
            style = styles.get(cell.xf_index) if cell.xf_index else None
            if value is None and style is None:
                continue
            out = target.cell(row=row_index + 1, column=column_index + 1)
            if value is not None:
                out.value = value
            if style is not None:
                for name, item in style.items():
                    setattr(out, name, item)
            report.cells += 1
    # 先写完所有单元格再合并：合并之后非锚点单元格是只读的 MergedCell，不能再写值。
    for first_row, last_row, first_col, last_col in sheet.merged_cells:
        if last_row - first_row > 1 or last_col - first_col > 1:
            target.merge_cells(start_row=first_row + 1, start_column=first_col + 1, end_row=last_row, end_column=last_col)
    for column, info in sheet.colinfo_map.items():
        dimension = target.column_dimensions[get_column_letter(column + 1)]
        if info.width:
            dimension.width = info.width / 256
        if info.hidden:
            dimension.hidden = True
        if info.outline_level:
            dimension.outlineLevel = info.outline_level
    for row, info in sheet.rowinfo_map.items():
        dimension = target.row_dimensions[row + 1]
        if info.height_mismatch or not info.has_default_height:
            dimension.height = info.height / 20
        if info.hidden:
            dimension.hidden = True
        if info.outline_level:
            dimension.outlineLevel = info.outline_level
    if sheet.panes_are_frozen and (sheet.horz_split_pos or sheet.vert_split_pos):
        # xlrd 的命名与直觉相反：horz_split_pos 是冻结的行数，vert_split_pos 是冻结的列数。
        target.freeze_panes = f"{get_column_letter(sheet.vert_split_pos + 1)}{sheet.horz_split_pos + 1}"
    _convert_notes(target, sheet)
    _convert_hyperlinks(target, sheet)


def _value(book: xlrd.Book, cell: xlrd.sheet.Cell, report: XlsConversion, where: str) -> Any:
    kind, value = cell.ctype, cell.value
    if kind in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if kind == xlrd.XL_CELL_TEXT:
        return value
    if kind == xlrd.XL_CELL_NUMBER:
        return int(value) if float(value).is_integer() and abs(value) < 10 ** 15 else value
    if kind == xlrd.XL_CELL_DATE:
        try:
            moment = xlrd.xldate.xldate_as_datetime(value, book.datemode)
        except (xlrd.xldate.XLDateError, OverflowError, ValueError):
            report.warnings.append(f"{where} 的日期无法转换，已保留为数字")
            return value
        return moment.time() if 0 <= value < 1 else moment
    if kind == xlrd.XL_CELL_BOOLEAN:
        return bool(value)
    if kind == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(value, "#VALUE!")
    return value


def _convert_notes(target: Any, sheet: xlrd.sheet.Sheet) -> None:
    for (row, column), note in sheet.cell_note_map.items():
        text = getattr(note, "text", "") or ""
        if text:
            target.cell(row=row + 1, column=column + 1).comment = Comment(text, getattr(note, "author", "") or "作者")


def _convert_hyperlinks(target: Any, sheet: xlrd.sheet.Sheet) -> None:
    for link in sheet.hyperlink_list:
        destination = link.url_or_path or (f"#{link.textmark}" if link.textmark else None)
        if destination:
            target.cell(row=link.frowx + 1, column=link.fcolx + 1).hyperlink = destination
