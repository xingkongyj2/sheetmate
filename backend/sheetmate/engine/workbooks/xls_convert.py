"""旧版 .xls（BIFF）→ .xlsx 的内置转换。

.xls 无法被 openpyxl 读取，也没有可用的写入库，所以"保留原格式的编辑"必须先转成 .xlsx 工作副本。
这里用 xlrd 读取值与样式（formatting_info），再用 openpyxl 写出：

- 保留：值（文本 / 数字 / 日期 / 布尔 / 错误）、合并单元格、列宽、行高、隐藏的行 / 列 / 工作表、
  字体（名称 / 字号 / 加粗 / 斜体 / 下划线 / 删除线 / 颜色 / 上下标）、填充、边框、对齐（含换行 / 缩进 / 缩小填充 / 旋转）、
  数字格式、冻结窗格、批注、超链接；
- 不保留：公式（xlrd 只提供缓存的计算结果，转换后是数值）、条件格式、数据验证、图表、图片、宏。

xlrd 的样式对象字段名与 openpyxl 不同（hor_align / underline_type / colour_index ……），集中在这里映射并有测试覆盖，
不要在别处凭印象重写一遍。
"""

from __future__ import annotations

import math
import posixpath
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import xlrd
from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.cell.rich_text import CellRichText, TextBlock
from openpyxl.cell.text import InlineFont
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900

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
_MAX_ZIP_BYTES = 256 * 1024 * 1024
_WORKBOOK_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
}


@dataclass
class XlsConversion:
    sheets: int = 0
    cells: int = 0
    warnings: list[str] = field(default_factory=list)  # 本次转换中发现的具体问题（不含固定的 LIMITATIONS）


class _ConversionBudget:
    def __init__(self, timeout_seconds: float, cancel_event: Any, max_cells: int):
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ReadError(".xls 转换时限必须为正数")
        if not isinstance(max_cells, int) or max_cells < 1:
            raise ReadError(".xls 转换单元格上限必须为正整数")
        self.deadline = time.monotonic() + timeout_seconds
        self.cancel_event = cancel_event
        self.max_cells = max_cells
        self.cells = 0

    def check(self, additional_cells: int = 0) -> None:
        if self.cancel_event is not None and self.cancel_event.is_set():
            raise ReadError(".xls 转换已取消")
        if time.monotonic() >= self.deadline:
            raise ReadError(".xls 转换超时，请缩小工作簿后重试")
        if self.cells + additional_cells > self.max_cells:
            raise ReadError(f".xls 转换超过 {self.max_cells} 个单元格的上限，请拆分工作簿后重试")
        self.cells += additional_cells


def convert_xls_to_xlsx(source: Path, destination: Path, *, timeout_seconds: float = 30,
                        cancel_event: Any = None, max_cells: int = 1_000_000) -> XlsConversion:
    """把 source（.xls）转换为 destination（.xlsx）；原子写入，失败不留下半成品。

    实际是 .xlsx 却用了 .xls 扩展名的文件直接复制；既不是 BIFF 也不是 OOXML 的（如网页 / XML 表格）抛 ReadError。
    时限、取消及单元格上限在读取后、逐行转换和落盘前后检查；调用方可用工作进程中断阻塞的底层读取。
    """
    budget = _ConversionBudget(timeout_seconds, cancel_event, max_cells)
    budget.check()
    source, destination = Path(source), Path(destination)
    if source.resolve() == destination.resolve():
        raise ReadError("XLS 转换必须另存副本，不能覆盖原文件")
    with source.open("rb") as stream:
        magic = stream.read(8)
    if magic.startswith(b"PK"):
        sheets, cells = _validate_renamed_xlsx(source, budget)
        with atomic_path(destination) as staging:
            with source.open("rb") as original, staging.open("wb") as target:
                while block := original.read(1024 * 1024):
                    budget.check()
                    target.write(block)
            budget.check()
        return XlsConversion(sheets=sheets, cells=cells,
                             warnings=["文件扩展名是 .xls，但内容已经是 .xlsx，已原样使用。"])
    if magic != _OLE_MAGIC:
        raise ReadError("这个 .xls 不是标准的 Excel 二进制文件（可能是网页或 XML 导出），请在 Excel 里另存为 .xlsx 后再上传。")
    try:
        book = xlrd.open_workbook(str(source), formatting_info=True, ragged_rows=True)
    except (xlrd.XLRDError, xlrd.compdoc.CompDocError, NotImplementedError, AssertionError, IndexError) as exc:
        raise ReadError(f"无法读取 .xls 文件：{exc}") from exc
    workbook = Workbook()
    try:
        budget.check()
        report = XlsConversion()
        workbook.epoch = CALENDAR_MAC_1904 if book.datemode else CALENDAR_WINDOWS_1900
        workbook.remove(workbook.active)
        styles = _StyleCache(book)
        for sheet in book.sheets():
            _convert_sheet(workbook, book, sheet, styles, report, budget)
        if not workbook.worksheets:
            raise ReadError(".xls 文件中没有工作表")
        budget.check()
        with atomic_path(destination) as staging:
            workbook.save(staging)
            budget.check()
        return report
    finally:
        workbook.close()
        book.release_resources()


def _validate_renamed_xlsx(source: Path, budget: _ConversionBudget) -> tuple[int, int]:
    """先验证改后缀的 OOXML 是工作簿，再原样复制；解压大小和实际单元格同样受限。"""
    try:
        if source.stat().st_size > _MAX_ZIP_BYTES:
            raise ReadError(".xls 文件中的 OOXML 内容过大，请拆分工作簿后重试")
        with ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > 10_000 or sum(info.file_size for info in infos) > _MAX_ZIP_BYTES:
                raise ReadError(".xls 文件中的 OOXML 内容过大，请拆分工作簿后重试")
            names = {info.filename for info in infos}
            if len(names) != len(infos) or not {"[Content_Types].xml", "xl/workbook.xml", "xl/_rels/workbook.xml.rels"} <= names:
                raise ReadError("这个 .xls 中的 ZIP 内容不是有效的 Excel 工作簿")
            types = ElementTree.fromstring(archive.read("[Content_Types].xml"))
            if not any(node.get("PartName") == "/xl/workbook.xml" and node.get("ContentType") in _WORKBOOK_MIME_TYPES
                       for node in types):
                raise ReadError("这个 .xls 中的 ZIP 内容不是有效的 Excel 工作簿")
            workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
            relationships = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            targets = {node.get("Id"): node.get("Target", "") for node in relationships}
            sheet_nodes = list(workbook.iter(f"{namespace}sheet"))
            sheets = len(sheet_nodes)
            if not sheets:
                raise ReadError(".xls 文件中没有工作表")
            for sheet in sheet_nodes:
                target = targets.get(sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"), "")
                part = target.lstrip("/") if target.startswith("/") else posixpath.normpath(f"xl/{target}")
                if not target or part not in names:
                    raise ReadError("这个 .xls 中的 OOXML 工作表关系不完整")
            for info in infos:
                budget.check()
                if info.filename.endswith(".xml") and info.filename.startswith("xl/worksheets/"):
                    with archive.open(info) as stream:
                        for index, (_, node) in enumerate(ElementTree.iterparse(stream, events=("end",))):
                            if index % 128 == 0:
                                budget.check()
                            if node.tag == f"{namespace}c":
                                budget.check(1)
                            node.clear()
        return sheets, budget.cells
    except ReadError:
        raise
    except (BadZipFile, KeyError, ElementTree.ParseError, RuntimeError, OSError) as exc:
        raise ReadError("这个 .xls 中的 OOXML 内容损坏，无法作为 Excel 工作簿使用") from exc


class _StyleCache:
    """按 xf 序号缓存转换后的 openpyxl 样式，避免每个单元格重复构造。"""

    def __init__(self, book: xlrd.Book):
        self.book = book
        self._cache: dict[int, dict[str, Any] | None] = {}
        self._fonts: dict[int, Font] = {}
        self._inline_fonts: dict[int, InlineFont] = {}

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

    def font(self, font_index: int) -> Font:
        if font_index not in self._fonts:
            font = self.book.font_list[font_index]
            self._fonts[font_index] = Font(
                name=font.name, size=(font.height or 200) / 20, bold=bool(font.bold), italic=bool(font.italic),
                underline=_UNDERLINE.get(font.underline_type), strike=bool(font.struck_out),
                color=self.colour(font.colour_index), vertAlign=_VERT_ALIGN.get(font.escapement),
                charset=font.character_set, family=font.family, outline=bool(font.outline), shadow=bool(font.shadow))
        return self._fonts[font_index]

    def inline_font(self, font_index: int) -> InlineFont:
        if font_index not in self._inline_fonts:
            font = self.font(font_index)
            self._inline_fonts[font_index] = InlineFont(
                rFont=font.name, sz=font.sz, b=font.b, i=font.i, u=font.u, strike=font.strike,
                color=font.color, vertAlign=font.vertAlign, charset=font.charset, family=font.family,
                outline=font.outline, shadow=font.shadow)
        return self._inline_fonts[font_index]

    def _build(self, xf_index: int) -> dict[str, Any] | None:
        if xf_index >= len(self.book.xf_list):
            return None
        xf = self.book.xf_list[xf_index]
        result: dict[str, Any] = {"font": self.font(xf.font_index)}
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


def _convert_sheet(workbook: Workbook, book: xlrd.Book, sheet: xlrd.sheet.Sheet, styles: _StyleCache,
                   report: XlsConversion, budget: _ConversionBudget) -> None:
    budget.check()
    target = workbook.create_sheet(sheet.name)
    target.sheet_state = _VISIBILITY.get(sheet.visibility, "visible")
    report.sheets += 1
    for row_index in range(sheet.nrows):
        budget.check()
        for column_index, cell in enumerate(sheet.row(row_index)):
            # XL_CELL_EMPTY 是稀疏数据中的占位，不是显式带样式的 BLANK。不能把占位也铺成新单元格。
            if cell.ctype == xlrd.XL_CELL_EMPTY:
                continue
            budget.check(1)
            value = _value(book, cell, report, f"{sheet.name}!{get_column_letter(column_index + 1)}{row_index + 1}")
            style = styles.get(cell.xf_index) if cell.xf_index is not None else None
            out = target.cell(row=row_index + 1, column=column_index + 1)
            if value is not None:
                if cell.ctype == xlrd.XL_CELL_TEXT:
                    if ILLEGAL_CHARACTERS_RE.search(value):
                        raise ReadError(f"{sheet.name}!{out.coordinate} 含 XLSX 不允许的文本控制字符，请在 Excel 中清理后重试")
                    value = _rich_text(value, sheet.rich_text_runlist_map.get((row_index, column_index)), styles)
                out.value = value
                if cell.ctype == xlrd.XL_CELL_TEXT:
                    out.data_type = "s"  # '=...' 和 '#N/A' 在原文件里也可能只是文本。
                elif cell.ctype == xlrd.XL_CELL_ERROR:
                    out.data_type = "e"
            if style is not None:
                for name, item in style.items():
                    setattr(out, name, item)
            report.cells += 1
    # 先写完所有单元格再合并：合并之后非锚点单元格是只读的 MergedCell，不能再写值。
    for first_row, last_row, first_col, last_col in sheet.merged_cells:
        if last_row - first_row > 1 or last_col - first_col > 1:
            _reserve_merge_cells(target, first_row, last_row, first_col, last_col, budget)
            target.merge_cells(start_row=first_row + 1, start_column=first_col + 1, end_row=last_row, end_column=last_col)
    if sheet.default_row_height is not None:
        target.sheet_format.defaultRowHeight = sheet.default_row_height / 20
        target.sheet_format.customHeight = bool(sheet.default_row_height_mismatch)
        target.sheet_format.zeroHeight = bool(sheet.default_row_hidden)
    if sheet.standardwidth is not None:
        target.sheet_format.defaultColWidth = sheet.standardwidth / 256
    elif sheet.defcolwidth is not None:
        target.sheet_format.baseColWidth = sheet.defcolwidth
    for column, info in sheet.colinfo_map.items():
        budget.check()
        dimension = target.column_dimensions[get_column_letter(column + 1)]
        if info.width is not None:
            dimension.width = info.width / 256
        if info.hidden:
            dimension.hidden = True
        if info.outline_level:
            dimension.outlineLevel = info.outline_level
    for row, info in sheet.rowinfo_map.items():
        budget.check()
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
    _convert_notes(target, sheet, budget)
    from sheetmate.engine.workbooks.xls_hyperlinks import convert_hyperlinks

    convert_hyperlinks(target, sheet, book, report, check=budget.check, max_cells=budget.max_cells, charge=budget.check)


def _value(book: xlrd.Book, cell: xlrd.sheet.Cell, report: XlsConversion, where: str) -> Any:
    kind, value = cell.ctype, cell.value
    if kind in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    if kind == xlrd.XL_CELL_TEXT:
        return value
    if kind == xlrd.XL_CELL_NUMBER:
        return int(value) if float(value).is_integer() and abs(value) < 10 ** 15 else value
    if kind == xlrd.XL_CELL_DATE:
        # 保存原序列值 + 工作簿日期制：日期、跨天时长及 Excel 的 1900 闰年兼容值都不能重映射。
        return value
    if kind == xlrd.XL_CELL_BOOLEAN:
        return bool(value)
    if kind == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(value, "#VALUE!")
    return value


def _rich_text(value: str, runs: list[tuple[int, int]] | None, styles: _StyleCache) -> str | CellRichText:
    if not runs or not value:
        return value
    # BIFF 的字符偏移以 UTF-16 code unit 计数；Python 字符串对非 BMP 字符只计一个位置。
    encoded = value.encode("utf-16-le")
    blocks: list[str | TextBlock] = []
    previous = 0
    font_index = None
    for offset, next_font in runs:
        position = max(previous, min(offset * 2, len(encoded)))
        if position > previous:
            text = encoded[previous:position].decode("utf-16-le")
            blocks.append(text if font_index is None else TextBlock(styles.inline_font(font_index), text))
        previous, font_index = position, next_font
    if previous < len(encoded):
        text = encoded[previous:].decode("utf-16-le")
        blocks.append(text if font_index is None else TextBlock(styles.inline_font(font_index), text))
    return CellRichText(blocks)


def _reserve_merge_cells(target: Any, first_row: int, last_row: int, first_col: int, last_col: int,
                          budget: _ConversionBudget) -> None:
    area = (last_row - first_row) * (last_col - first_col)
    if area > budget.max_cells:
        raise ReadError(f".xls 合并区域超过 {budget.max_cells} 个单元格的上限，请拆分工作簿后重试")
    existing = 0
    for row in range(first_row + 1, last_row + 1):
        budget.check()
        existing += sum((row, column) in target._cells for column in range(first_col + 1, last_col + 1))
    budget.check(area - existing)


def _convert_notes(target: Any, sheet: xlrd.sheet.Sheet, budget: _ConversionBudget) -> None:
    for (row, column), note in sheet.cell_note_map.items():
        budget.check()
        text = getattr(note, "text", "") or ""
        if text:
            if (row + 1, column + 1) not in target._cells:
                budget.check(1)
            target.cell(row=row + 1, column=column + 1).comment = Comment(text, getattr(note, "author", "") or "作者")
