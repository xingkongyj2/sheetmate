"""把「结果表 + 原始行身份」翻译成 OOXML 结构操作，直接在原工作簿的 XML 上完成修改。

这是 write_like_original 的高保真快速路径：openpyxl 载入再保存会丢掉切片器、控件、形状等它不认识的部件，
而这里只改被改动的单元格 / 行，其余部件按字节原样保留。只处理能证明正确的编辑：

- 全部输出都是改写原工作表（不追加新表、不重命名），来自同一个 .xlsx / .xlsm；
- 列与原表一致，右侧可以追加新列；
- 每个结果行都有可证明的原始行身份（source_rows），据此判定哪些行被删除、是否被重排；
- 被改的单元格是数字 / 文本 / 布尔 / 空白，不是公式、不在合并区（日期等留给常规流程）。

任何一项不满足，或底层判定无法保证引用同步，都返回 None，由调用方走常规流程；不会留下半成品文件。
"""

from __future__ import annotations

import logging
import math
import re
from bisect import bisect_left
from pathlib import Path
from typing import Any

import numpy as np
from openpyxl.utils.cell import absolute_coordinate, get_column_letter, quote_sheetname, range_boundaries

from sheetmate.engine.ooxml import OoxmlEditor, OoxmlUnsupported
from sheetmate.engine.ooxml.package import WORKBOOK
from sheetmate.engine.ooxml.xmlutil import check_text, escape, parse_attrs, unescape
from sheetmate.engine.workbooks.writer import _excel_value

logger = logging.getLogger(__name__)


def edit_with_ooxml(source: Path, destination: Path, plans: list[Any], read_sheet: Any) -> list[dict[str, Any]] | None:
    """成功返回与 write_like_original 相同格式的 written 列表；不适用返回 None（不留下目标文件）。

    read_sheet(template) 返回该模板对应原工作表的 SheetData（调用方的读取缓存）。
    """
    try:
        return _edit(Path(source), Path(destination), plans, read_sheet)
    except OoxmlUnsupported as exc:
        logger.info("OOXML 编辑不适用，退回常规写回：%s", exc)
        return None
    except Exception:  # 快速路径的任何意外都退回常规流程，但必须留下线索，不能悄悄吞掉
        logger.warning("OOXML 编辑出现意外错误，退回常规写回", exc_info=True)
        return None


def _edit(source: Path, destination: Path, plans: list[Any], read_sheet: Any) -> list[dict[str, Any]]:
    if not plans:
        raise OoxmlUnsupported("没有输出计划")
    paths = {plan.template.path.resolve() for plan in plans if plan.template is not None}
    if len(paths) != 1 or source.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise OoxmlUnsupported("输出不是来自同一个 xlsx / xlsm 文件")
    seen: set[str] = set()
    for plan in plans:
        template = plan.template
        if not plan.edit or template is None or plan.name != template.sheet or template.sheet in seen:
            raise OoxmlUnsupported("存在追加新表、重命名或重复改写同一工作表的输出")
        seen.add(template.sheet)
    with OoxmlEditor.open(source) as editor:
        for plan in plans:
            _apply(editor, plan, read_sheet(plan.template))
        editor.save(destination)
    return [{"sheet": plan.template.sheet, "rows": int(len(plan.df)), "columns": [str(c) for c in plan.df.columns]}
            for plan in plans]


def _apply(editor: OoxmlEditor, plan: Any, original: Any) -> None:
    template, frame = plan.template, plan.df
    columns, base = [str(c) for c in frame.columns], list(template.columns)
    if columns[:len(base)] != base:
        raise OoxmlUnsupported("结果的列与原表不一致（增删 / 改名 / 换序）")
    added = columns[len(base):]
    count = len(original.df)
    rows = [row + 1 for row in (original.header.row_index or [])]  # Excel 行号，1 起
    if len(rows) != count or rows != sorted(set(rows)):
        raise OoxmlUnsupported("无法定位原表的数据行")
    source_rows = plan.source_rows
    if (source_rows is None or len(source_rows) != len(frame) or len(set(source_rows)) != len(source_rows)
            or any(type(k) is not int or not 0 <= k < count for k in source_rows)):
        raise OoxmlUnsupported("缺少可证明的原始行身份")

    sheet = editor.sheet(template.sheet)
    if added and sheet.has_tables():
        raise OoxmlUnsupported("工作表含表格对象，右侧追加列需要同步调整表格范围")
    original_filter = _auto_filter_ref(sheet.xml.tail) if added else None

    kept = sorted(set(source_rows))
    present = set(source_rows)
    deleted = sorted(rows[k] for k in range(count) if k not in present)
    if deleted:
        sheet.delete_rows(set(deleted))

    def current(k: int) -> int:  # 删行之后原数据行 k 所在的行号
        return rows[k] - bisect_left(deleted, rows[k])

    if list(source_rows) != kept:
        positions = [current(k) for k in kept]
        if positions != list(range(positions[0], positions[0] + len(positions))):
            raise OoxmlUnsupported("被排序的数据行之间不连续，无法安全重排")
        sheet.reorder_rows(positions[0], [current(k) for k in source_rows])
        final = [positions[0] + index for index in range(len(source_rows))]
    else:
        final = [current(k) for k in source_rows]

    before, after = original.df.to_numpy(dtype=object), frame.to_numpy(dtype=object)
    edits: dict[tuple[int, int], Any] = {}
    for index, k in enumerate(source_rows):
        for offset, column in enumerate(template.col_index):
            new, old = _excel_value(after[index, offset]), _excel_value(before[k, offset])
            # 验收时的近似比较不能用来决定是否写入：大金额的小幅修改也必须保留。
            if _kind(new) == _kind(old) and new == old:
                continue
            edits[(final[index], column + 1)] = _normalise(new)
    if edits:
        sheet.set_values(edits)

    if added:
        start = sheet.max_column() + 1
        header = {(template.header_row, start + m): check_text(str(name)) for m, name in enumerate(added)}
        sheet.set_values(header, style_from={start + m: start + m - 1 for m in range(len(added))})  # 表头样式照左边一列
        donors = {start + m: _style_donor(sheet, template, frame, len(base) + m, final[0] if final else None)
                  for m in range(len(added))}
        body = {(row, start + m): _normalise(_excel_value(after[index, len(base) + m]))
                for index, row in enumerate(final) for m in range(len(added))}
        sheet.set_values(body, style_from={col: donor for col, donor in donors.items() if donor is not None})
        if original_filter is not None:
            _extend_auto_filter(sheet, template, start - 1, start + len(added) - 1, final, original_filter)


def _auto_filter_ref(tail: str) -> str | None:
    matches = list(re.finditer(r"<autoFilter\b([^>]*?)/?>", tail))
    if not matches:
        return None
    if len(matches) != 1:
        raise OoxmlUnsupported("工作表有多个筛选区域，无法安全扩展新增列")
    reference = parse_attrs(matches[0].group(1)).get("ref")
    if not reference:
        raise OoxmlUnsupported("筛选区域缺少范围，无法安全扩展新增列")
    return unescape(reference)


def _filter_bounds(reference: str) -> tuple[int, int, int, int]:
    try:
        bounds = range_boundaries(reference)
    except ValueError as exc:
        raise OoxmlUnsupported("筛选或排序范围无法解析，无法安全扩展新增列") from exc
    if any(value is None for value in bounds):
        raise OoxmlUnsupported("筛选或排序范围不是普通单元格区域，无法安全扩展新增列")
    return bounds


def _extend_auto_filter(sheet: Any, template: Any, old_right: int, new_right: int,
                        rows: list[int], original_ref: str) -> None:
    """只扩展完整覆盖原记录列的普通筛选，保留筛选条件、排序键及其余 XML。"""
    current_ref = _auto_filter_ref(sheet.xml.tail)
    if current_ref is None:
        raise OoxmlUnsupported("原筛选区域在编辑后消失，无法安全扩展新增列")
    left, top, right, bottom = _filter_bounds(current_ref)
    if (top != template.header_row or right != old_right or not template.col_index
            or left > min(template.col_index) + 1 or max(template.col_index) + 1 > right
            or any(row <= top or row > bottom for row in rows)):
        raise OoxmlUnsupported("筛选区域没有完整覆盖原表，无法安全扩展新增列")
    expanded = f"{get_column_letter(left)}{top}:{get_column_letter(new_right)}{bottom}"

    def extend_sort(match: re.Match[str]) -> str:
        attrs = parse_attrs(match.group(1))
        reference = attrs.get("ref")
        if attrs.get("columnSort") in {"1", "true"} or reference is None:
            raise OoxmlUnsupported("列排序或缺少范围的排序状态无法安全扩展新增列")
        sort_left, sort_top, sort_right, sort_bottom = _filter_bounds(unescape(reference))
        if (sort_left != left or sort_right != right or sort_top not in {top, top + 1}
                or sort_bottom != bottom):
            raise OoxmlUnsupported("排序区域没有完整覆盖原表，无法安全扩展新增列")
        replacement = f"{get_column_letter(left)}{sort_top}:{get_column_letter(new_right)}{bottom}"
        return match.group(0).replace(f'ref="{reference}"', f'ref="{replacement}"', 1)

    tail = re.sub(r"<sortState\b([^>]*?)/?>", extend_sort, sheet.xml.tail)
    tail = re.sub(r'(<autoFilter\b[^>]*?\bref=")[^"]*(")',
                  lambda match: f"{match.group(1)}{expanded}{match.group(2)}", tail, count=1)

    # 行映射在 save 时统一传播；这里保留名称原有行号，只先扩大列边界。
    package = sheet.editor.package
    workbook = package.read_text(WORKBOOK)
    sheet_index = str(sheet.editor.sheet_names().index(sheet.name))
    original_bounds = _filter_bounds(original_ref)

    def extend_name(match: re.Match[str]) -> str:
        attrs = parse_attrs(match.group(1))
        if attrs.get("name") != "_xlnm._FilterDatabase" or attrs.get("localSheetId") != sheet_index:
            return match.group(0)
        formula = unescape(match.group(2))
        qualified = re.fullmatch(r"(?:'((?:[^']|'')+)'|([^!]+))!(.+)", formula)
        if (qualified is None or (qualified.group(1) or qualified.group(2)).replace("''", "'").casefold()
                != sheet.name.casefold() or _filter_bounds(qualified.group(3)) != original_bounds):
            raise OoxmlUnsupported("筛选定义名称与筛选范围不一致，无法安全扩展新增列")
        old_left, old_top, _, old_bottom = original_bounds
        reference = absolute_coordinate(
            f"{get_column_letter(old_left)}{old_top}:{get_column_letter(new_right)}{old_bottom}")
        updated = escape(f"{quote_sheetname(sheet.name)}!{reference}")
        return f"<definedName{match.group(1)}>{updated}</definedName>"

    updated = re.sub(r"<definedName\b([^>]*)>(.*?)</definedName>", extend_name, workbook, flags=re.S)
    if updated != workbook:
        package.write(WORKBOOK, updated)
    sheet.xml.tail = tail
    sheet.dirty = True


def _style_donor(sheet: Any, template: Any, frame: Any, position: int, sample_row: int | None) -> int | None:
    """新增列的数据样式向哪一列借（1 起的列号）：同数据类型、数字格式不会把值显示错的原列；借不到就不设样式。

    与常规流程"按语义找样子最像的列借样式"的原则一致。样式表里的百分比 / 日期格式被识别出来，
    避免把数字显示成 20000% 或 1900 年的日期。需要百分比格式的比例列交给常规流程处理。
    """
    from sheetmate.engine.workbooks.profile import dtype_label
    from sheetmate.engine.workbooks.template_writer import _is_percent_like

    series = frame.iloc[:, position]
    kind = dtype_label(series)
    if _is_percent_like(series, str(frame.columns[position])):
        raise OoxmlUnsupported("比例列需要百分比格式，交给常规流程选择样式")
    if kind == "datetime":
        raise OoxmlUnsupported("日期列需要匹配数字格式，交给常规流程")
    if sample_row is None:
        return None
    for name, index in zip(template.columns, template.col_index):
        if template.dtypes.get(name, "") != kind:
            continue
        if sheet.style_kind(sample_row, index + 1) in {"general", "other"}:
            return index + 1
    return None


def _kind(value: Any) -> str:
    """数字 5 与文本 "5" 是不同的单元格内容，不能因为显示相同就跳过。"""
    if value is None:
        return "blank"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "text"
    return "other"


def _normalise(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, str):
        return check_text(value)
    raise OoxmlUnsupported(f"{type(value).__name__} 类型的值需要与单元格数字格式配合，交给常规流程")
