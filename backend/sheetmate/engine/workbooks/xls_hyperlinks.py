"""把 xlrd 的 BIFF 超链接转换成保留目标地址与单元格内容的 OOXML 超链接。"""

from __future__ import annotations

import codecs
from typing import Any, Callable

from openpyxl.cell.cell import MergedCell
from openpyxl.cell.rich_text import CellRichText
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.hyperlink import Hyperlink

from sheetmate.engine.workbooks.reader import ReadError


def _path(value: str | bytes | None, book: Any, where: str) -> str | None:
    if not isinstance(value, bytes):
        return value
    try:
        return value.decode("ascii")
    except UnicodeDecodeError:
        pass
    # BIFF8 的 CODEPAGE=1200 只说明工作簿字符串采用 UTF-16；文件 moniker 的短路径仍是未知 ANSI。
    # 不能拿它解码，也不能用替换字符悄悄改变链接目标。
    encoding = getattr(book, "encoding", None)
    try:
        canonical = codecs.lookup(encoding).name if encoding else ""
        if not canonical or canonical.startswith(("utf-", "utf_")):
            raise LookupError("unknown ANSI code page")
        return value.decode(canonical, errors="strict")
    except (LookupError, UnicodeError) as exc:
        raise ReadError(f"{where} 的本地文件超链接使用无法确定的字符编码，请在 Excel 中更新该链接后重试。") from exc


def convert_hyperlinks(target: Any, sheet: Any, book: Any, report: Any, *,
                       check: Callable[[], None] | None = None, max_cells: int = 1_000_000,
                       charge: Callable[[int], None] | None = None) -> None:
    """保留内部位置、外部目标和书签，以及原超链接范围内每个可编辑的单元格。

    合并区域只写锚点；空白单元格使用显式空文本，不使用链接地址补值。范围展开先检查上限并逐行响应取消。
    """
    expanded = 0
    for link in sheet.hyperlink_list:
        if check is not None:
            check()
        first_row, last_row, first_col, last_col = link.frowx, link.lrowx, link.fcolx, link.lcolx
        if (not all(isinstance(value, int) for value in (first_row, last_row, first_col, last_col))
                or not 0 <= first_row <= last_row < 65536 or not 0 <= first_col <= last_col < 256):
            raise ReadError(".xls 文件中的超链接范围无效。")
        expanded += (last_row - first_row + 1) * (last_col - first_col + 1)
        if expanded > max_cells:
            raise ReadError(f".xls 超链接范围超过转换上限（{max_cells} 个单元格）。")
        where = f"{sheet.name}!{get_column_letter(first_col + 1)}{first_row + 1}"
        destination = _path(link.url_or_path, book, where)
        location = link.textmark or None
        if not destination and not location:
            report.warnings.append(f"{where} 的超链接缺少目标，未转换。")
            continue
        if getattr(link, "target", None):
            report.warnings.append(f"{where} 的超链接目标窗口设置未转换。")
        for row in range(first_row + 1, last_row + 2):
            if check is not None:
                check()
            for column in range(first_col + 1, last_col + 2):
                if charge is not None and (row, column) not in target._cells:
                    charge(1)
                cell = target.cell(row=row, column=column)
                if isinstance(cell, MergedCell):
                    continue
                value, data_type = cell.value, cell.data_type
                cell.hyperlink = Hyperlink(
                    ref=cell.coordinate, target=destination or None, location=location,
                    tooltip=getattr(link, "quicktip", None), display=getattr(link, "desc", None),
                )
                if value is None:
                    # 普通空单元格会被 writer 跳过，空 <c/> 又会在 loader 绑定链接时被填成目标地址。
                    # 显式空文本 run 同时保留链接和空白外观；重新载入后值为 ""。
                    cell.value = CellRichText([""])
                else:
                    cell.value = value
                    cell.data_type = data_type
