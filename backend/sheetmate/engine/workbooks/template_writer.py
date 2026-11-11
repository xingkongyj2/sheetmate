"""按原文件样式写出结果：复制一份原始 Excel，在里面修改 / 删除 / 新增。

财务同事要的是「输入什么样，输出还是什么样」，而不是一张格式全新的表。所以输出不是新建一个空白 Excel，
而是把原始文件复制一份，然后：

- 标题行、合并单元格、表头样式、列宽、隐藏列、冻结窗格、数字格式、斑马纹……都沿用原文件
- 被处理的那张 sheet：表头以下的数据区整体换成结果（行数变多变少都行），每一行沿用原来数据行的样式
- 新增的列：表头和数据样式照着相邻列（或同类型的列）来；删掉的列直接去掉；列顺序变了就跟着变
- 结果里多出来的 sheet（例如汇总表）：追加到这份副本里，表头和数据样式照着原表
- 原文件里没被动过的 sheet 原样保留

支持 .xlsx / .xlsm 的原文件。优先保留原格式；结构不兼容时重新加载副本基线继续修改，并报告格式变化。
"""

from __future__ import annotations

import datetime as dt
import warnings
from bisect import bisect_left
from copy import copy
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from xml.etree import ElementTree
from zipfile import ZipFile

import openpyxl
import pandas as pd
from openpyxl.cell.cell import MergedCell
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.formula.translate import Translator
from openpyxl.styles.cell_style import StyleArray
from openpyxl.utils import coordinate_to_tuple, get_column_letter
from openpyxl.worksheet.cell_range import CellRange, MultiCellRange
from openpyxl.worksheet.table import TableColumn
from openpyxl.worksheet.worksheet import Worksheet

from sheetmate.engine.workbooks.profile import dtype_label
from sheetmate.engine.workbooks.reader import read_sheet
from sheetmate.engine.workbooks.row_references import adjust_formula, shift_range_ref
from sheetmate.engine.workbooks.writer import _display_width, _excel_value, safe_sheet_name

TEMPLATE_SUFFIXES = {".xlsx", ".xlsm"}


class TemplatePreservationError(ValueError):
    pass


@dataclass
class TemplateSource:
    """一张原始表在原文件里的位置信息（输出时用来找样式）。"""

    alias: str
    path: Path
    sheet: str  # 原文件里的 sheet 名
    header_row: int  # 表头最后一行的行号（1 起）
    header_rows: int  # 表头占几行
    col_index: list[int]  # DataFrame 每一列在原 sheet 里的列位置（0 起）
    columns: list[str]  # DataFrame 每一列的名字（字段映射之后）
    dtypes: dict[str, str] = field(default_factory=dict)  # 列名 → text / integer / float / datetime / boolean
    file_id: str | None = None

    def usable(self) -> bool:
        return (
            self.path.suffix.lower() in TEMPLATE_SUFFIXES
            and self.path.exists()
            and len(self.col_index) == len(self.columns)
        )

    def to_persist(self) -> dict[str, Any]:
        """存进运行记录（不含本机路径，路径靠 file_id 再查）。"""
        return {
            "file_id": self.file_id, "sheet": self.sheet, "header_row": self.header_row, "header_rows": self.header_rows,
            "col_index": self.col_index, "columns": self.columns, "dtypes": self.dtypes,
        }

    @classmethod
    def from_persist(cls, alias: str, data: dict[str, Any], path: Path) -> TemplateSource:
        return cls(
            alias=alias, path=path, sheet=data["sheet"], header_row=int(data["header_row"]),
            header_rows=int(data.get("header_rows", 1)), col_index=list(data["col_index"]), columns=list(data["columns"]),
            dtypes=dict(data.get("dtypes", {})), file_id=data.get("file_id"),
        )


@dataclass
class OutputPlan:
    """一个要输出的 sheet。edit=True：改写原文件里 template 对应的 sheet；否则新建一个 sheet。"""

    name: str
    df: pd.DataFrame
    template: TemplateSource | None = None
    edit: bool = False


# ---------------------------------------------------------------------------
# 样式快照
# ---------------------------------------------------------------------------


@dataclass
class _Dim:
    width: float | None = None
    hidden: bool = False
    outline: int = 0


@dataclass
class _Snapshot:
    """被改写 sheet 在动手之前的样式：表头、数据行、列宽、行高。"""

    tpl: TemplateSource
    header_value: dict[int, Any]  # 工作表列号（1 起）→ 表头最后一行的值
    header_style: dict[int, StyleArray]
    data_styles: list[dict[int, StyleArray]]  # 斑马纹：1 或 2 套数据行样式（列号 → 样式）
    row_heights: list[float | None]  # 和 data_styles 对应
    dims: dict[int, _Dim]
    max_col: int

    @classmethod
    def capture(cls, ws: Worksheet, tpl: TemplateSource) -> _Snapshot:
        max_col = max(ws.max_column or 1, (max(tpl.col_index) + 1) if tpl.col_index else 1)
        header_value, header_style = {}, {}
        for c in range(1, max_col + 1):
            cell = ws.cell(row=tpl.header_row, column=c)
            header_value[c] = cell.value
            header_style[c] = copy(cell._style)
        first = tpl.header_row + 1
        variants: list[dict[int, StyleArray]] = []
        heights: list[float | None] = []
        for r in (first, first + 1):
            if r > (ws.max_row or 0):
                break
            variants.append({c: copy(ws.cell(row=r, column=c)._style) for c in range(1, max_col + 1)})
            heights.append(ws.row_dimensions[r].height if r in ws.row_dimensions else None)
        # 两行样式完全一样就只用一套；不一样说明有斑马纹，交替使用
        if len(variants) == 2 and _same(variants[0], variants[1]):
            variants, heights = variants[:1], heights[:1]
        return cls(tpl=tpl, header_value=header_value, header_style=header_style, data_styles=variants or [{}],
                   row_heights=heights or [None], dims=_read_dims(ws), max_col=max_col)

    def data_style(self, k: int, col: int | None) -> StyleArray | None:
        if col is None:
            return None
        return self.data_styles[k % len(self.data_styles)].get(col)

    def height(self, k: int) -> float | None:
        return self.row_heights[k % len(self.row_heights)]


def _same(a: dict[int, StyleArray], b: dict[int, StyleArray]) -> bool:
    return all(a[column] == b.get(column) for column in a)


def _read_dims(ws: Worksheet) -> dict[int, _Dim]:
    dims: dict[int, _Dim] = {}
    for key, d in ws.column_dimensions.items():
        lo = d.min or openpyxl.utils.column_index_from_string(key)
        hi = d.max or lo
        for idx in range(lo, min(hi, lo + 300) + 1):
            dims[idx] = _Dim(width=d.width, hidden=bool(d.hidden), outline=int(d.outlineLevel or 0))
    return dims


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------


def write_like_original(path: Path, plans: list[OutputPlan]) -> list[dict[str, Any]] | None:
    """优先保真输出；保真检查失败后从干净副本重试，原件覆盖检查始终保留。"""
    base = next((plan for plan in plans if plan.template and plan.template.usable()), None)
    if base is None:
        return None
    tpl = base.template
    assert tpl is not None
    if path.resolve() == tpl.path.resolve():
        raise TemplatePreservationError("编辑结果必须另存副本，不能覆盖原文件")
    if tpl.path.suffix.lower() == ".xlsm" and path.suffix.lower() != ".xlsm":
        raise TemplatePreservationError("含宏工作簿必须另存为 .xlsm，不能丢弃宏后输出 .xlsx")
    notices: list[str] = []
    try:
        _check_package(tpl.path)
    except TemplatePreservationError:
        notices.append("工作簿包含当前编辑引擎不能完整保留的扩展对象，已在副本继续处理，这些对象可能变化。")
    for relaxed in (False, True):
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always", UserWarning)
            workbook = openpyxl.load_workbook(tpl.path, keep_vba=tpl.path.suffix.lower() == ".xlsm", rich_text=True)
            try:
                written = _write_workbook(workbook, path, plans, tpl, relaxed=relaxed, notices=notices)
            except TemplatePreservationError:
                if relaxed:
                    raise
                notices.append("部分修改与原有模板结构不兼容，已重新加载干净副本并完成修改；受影响区域的格式或引用可能变化。")
                continue
            finally:
                workbook.close()
                if workbook.vba_archive is not None:
                    workbook.vba_archive.close()
            notices.extend(str(item.message) for item in observed)
            if notices and written:
                written[0]["warnings"] = list(dict.fromkeys(notices))
            return written
    return None


def _check_package(path: Path) -> None:
    with ZipFile(path) as archive:
        unsupported = ("xl/activeX/", "xl/ctrlProps/", "xl/slicers/", "xl/slicerCaches/", "xl/embeddings/",
                       "xl/threadedComments/", "customXml/")
        for name in archive.namelist():
            if name.startswith(unsupported):
                raise TemplatePreservationError("原文件包含控件、切片器或其他暂不能完整保留的内容，已停止输出以避免丢失")
            if name.startswith("xl/drawings/") and name.endswith(".xml"):
                drawing = ElementTree.fromstring(archive.read(name))
                if any(node.tag.rsplit("}", 1)[-1] in {"sp", "grpSp", "cxnSp", "contentPart"} for node in drawing.iter()):
                    raise TemplatePreservationError("原文件包含暂不能完整保留的绘图形状，已停止输出以避免丢失")


def _write_workbook(workbook: openpyxl.Workbook, path: Path, plans: list[OutputPlan], base: TemplateSource,
                    *, relaxed: bool = False, notices: list[str] | None = None) -> list[dict[str, Any]]:
    notices = notices if notices is not None else []
    snapshots: dict[str, _Snapshot] = {}
    for source in [base, *(plan.template for plan in plans if plan.template)]:
        if source.path != base.path:
            continue
        if source.sheet not in workbook.sheetnames:
            raise TemplatePreservationError(f"原工作簿中找不到「{source.sheet}」，无法保留原格式")
        snapshots[source.sheet] = _Snapshot.capture(workbook[source.sheet], source)
    written: list[dict[str, Any]] = []
    used = set(workbook.sheetnames)
    edited: set[str] = set()
    for plan in plans:
        source = plan.template
        snap = snapshots.get(source.sheet, snapshots[base.sheet]) if source else snapshots[base.sheet]
        if plan.edit:
            if source is None or source.path != base.path or source.sheet in edited:
                raise TemplatePreservationError("输出与原工作表的对应关系不明确，已停止输出以避免丢失格式")
            sheet = workbook[source.sheet]
            title = _unique_title(plan.name, used - {sheet.title}) if plan.name != sheet.title else sheet.title
            if title != sheet.title and _has_formulas(workbook) and not relaxed:
                raise TemplatePreservationError("重命名工作表可能破坏现有公式引用，请保留原工作表名称")
            _rewrite_sheet(sheet, snap, plan.df, relaxed=relaxed, notices=notices)
            used.discard(sheet.title)
            used.add(title)
            sheet.title = title
            edited.add(source.sheet)
        else:
            title = safe_sheet_name(plan.name, used)
            _append_sheet(workbook, title, snap, plan.df)
        written.append(_info(title, plan.df))
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, suffix=path.suffix, delete=False) as temporary:
        staging = Path(temporary.name)
    try:
        workbook.save(staging)
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)
    return written


def _has_formulas(workbook: openpyxl.Workbook) -> bool:
    return any(cell.data_type == "f" for sheet in workbook for row in sheet for cell in row)


def _info(title: str, df: pd.DataFrame) -> dict[str, Any]:
    return {"sheet": title, "rows": int(len(df)), "columns": [str(c) for c in df.columns]}


def _unique_title(name: str, others: set[str]) -> str:
    names = set(others)
    return safe_sheet_name(name, names)


# ---------------------------------------------------------------------------
# 改写原 sheet
# ---------------------------------------------------------------------------


def _column_map(snap: _Snapshot, df: pd.DataFrame) -> list[int | None]:
    """结果里的每一列 → 原 sheet 的列号（1 起）；新增的列是 None。"""
    pos = {name: i for i, name in enumerate(snap.tpl.columns)}
    return [snap.tpl.col_index[pos[str(c)]] + 1 if str(c) in pos else None for c in df.columns]


def _rewrite_sheet(ws: Worksheet, snap: _Snapshot, df: pd.DataFrame, *, relaxed: bool = False,
                   notices: list[str] | None = None) -> None:
    notices = notices if notices is not None else []
    tpl = snap.tpl
    mapping = _column_map(snap, df)
    columns = [str(c) for c in df.columns]
    same_columns = columns == tpl.columns
    header_row, first_data = tpl.header_row, tpl.header_row + 1

    original = read_sheet(tpl.path, tpl.sheet, header_hint={"header_row": tpl.header_row, "header_rows": tpl.header_rows})
    if len(original.df.columns) != len(tpl.columns) and not relaxed:
        raise TemplatePreservationError("原工作表列结构已变化，无法安全编辑")
    if (columns[:len(tpl.columns)] == tpl.columns and len(df) == len(original.df)
            and len(original.df.columns) == len(tpl.columns)):
        _patch_cells(ws, snap, df, original.df, original.header.row_index or [], mapping, relaxed=relaxed)
        return
    if same_columns and len(df) < len(original.df):
        try:
            matched = _match_original_rows(df, original.df)
        except TemplatePreservationError:
            if not relaxed:
                raise
            matched = None
        if matched is None:
            matched = _match_edited_rows(df, original.df)
        if matched is not None:
            previous_rows = original.header.row_index or []
            removed = sorted(previous_rows[index] + 1 for index in range(len(previous_rows)) if index not in set(matched))
            _delete_original_rows(ws, original, matched, relaxed=relaxed, notices=notices)
            remaining_rows = [previous_rows[index] - bisect_left(removed, previous_rows[index] + 1) for index in matched]
            _patch_cells(ws, snap, df, original.df.iloc[matched].reset_index(drop=True), remaining_rows, mapping,
                         relaxed=relaxed)
            return
    merged_data = any(merged.max_row > tpl.header_row for merged in ws.merged_cells.ranges)
    if not relaxed and (_has_formulas(ws.parent) or merged_data or (tpl.header_rows > 1 and not same_columns)
            or ws.conditional_formatting or ws.data_validations.count or ws._charts or ws._images or ws._pivots
            or ws.tables or _has_special_layout(ws, snap, original.header.row_index or [])
            or (not same_columns and any(merged.max_row == tpl.header_row for merged in ws.merged_cells.ranges))
            or ws.auto_filter.filterColumn or ws.auto_filter.sortState):
        raise TemplatePreservationError(
            "本次行列重排会影响原表的公式、合并结构或其他格式，暂不能安全保留；已停止输出，原文件未改动"
        )

    # 1. 数据区里的合并单元格先拆掉（否则删行会留下残骸），然后整块删除
    for rng in list(ws.merged_cells.ranges):
        if rng.min_row >= first_data or (relaxed and rng.max_row >= first_data):
            ws.unmerge_cells(str(rng))
    if (ws.max_row or 0) >= first_data:
        ws.delete_rows(first_data, ws.max_row - first_data + 1)
    for r in [r for r in ws.row_dimensions if r >= first_data]:
        del ws.row_dimensions[r]

    # 2. 表头：列没变就一个字不动；列变了就按新列重排，样式跟着列走
    if not same_columns:
        header_row, first_data = _rewrite_header(ws, snap, df, mapping, columns)
        _apply_dims(ws, snap, df, mapping)

    # 3. 数据行：沿用原数据行的样式
    styles = _column_styles(snap, df, mapping)
    for k, row in enumerate(df.itertuples(index=False, name=None)):
        excel_row = first_data + k
        for j, value in enumerate(row):
            _put(ws.cell(row=excel_row, column=j + 1), _excel_value(value), styles[j][k % len(snap.data_styles)])
        height = snap.height(k)
        if height:
            ws.row_dimensions[excel_row].height = height

    if relaxed:
        _resize_table_objects(ws, header_row, max(len(df.columns), 1), first_data + max(len(df) - 1, 0))
    else:
        _fix_table_objects(ws, header_row, max(len(df.columns), 1), first_data + max(len(df) - 1, 0))


def _match_original_rows(frame: pd.DataFrame, original: pd.DataFrame) -> list[int] | None:
    """Return an unambiguous ordered subsequence of original rows for a filtered result."""
    source_records = [tuple(_excel_value(value) for value in record)
                      for record in original.itertuples(index=False, name=None)]
    result_records = [tuple(_excel_value(value) for value in record)
                      for record in frame.itertuples(index=False, name=None)]
    earliest: list[int] = []
    wanted = 0
    for index, record in enumerate(source_records):
        if wanted < len(result_records) and record == result_records[wanted]:
            earliest.append(index)
            wanted += 1
    if wanted != len(result_records):
        return None
    latest: list[int] = []
    wanted = len(result_records) - 1
    for index in range(len(source_records) - 1, -1, -1):
        if wanted >= 0 and source_records[index] == result_records[wanted]:
            latest.append(index)
            wanted -= 1
    if earliest != list(reversed(latest)):
        raise TemplatePreservationError("结果包含无法唯一定位的重复行，已停止输出以避免删除错误的原始行")
    return earliest


def _match_edited_rows(frame: pd.DataFrame, original: pd.DataFrame) -> list[int] | None:
    """Use surviving unique fields to locate rows even when other fields were edited."""
    source = [tuple(_excel_value(value) for value in record) for record in original.itertuples(index=False, name=None)]
    result = [tuple(_excel_value(value) for value in record) for record in frame.itertuples(index=False, name=None)]
    best: tuple[tuple[int, int], list[int]] | None = None
    for column in range(len(original.columns)):
        values = [record[column] for record in source]
        if None in values or len(set(values)) != len(values):
            continue
        lookup = {value: index for index, value in enumerate(values)}
        if any(record[column] not in lookup for record in result):
            continue
        positions = [lookup[record[column]] for record in result]
        if len(set(positions)) != len(positions) or positions != sorted(positions):
            continue
        score = sum(left == right for record, position in zip(result, positions)
                    for left, right in zip(record, source[position]))
        result_values = [record[column] for record in result]
        first_number = 0 if values[0] == 0 else 1
        consecutive = (
            values[0] in (0, 1)
            and values == list(range(first_number, first_number + len(values)))
            and result_values == list(range(first_number, first_number + len(result)))
        )
        rank = score, int(not consecutive)
        if best is None or rank > best[0]:
            best = rank, positions
    return best[1] if best else None


def _delete_original_rows(ws: Worksheet, original: Any, matched: list[int], *, relaxed: bool = False,
                          notices: list[str] | None = None) -> None:
    """Delete filtered-out source rows while retaining the original cell/style objects."""
    row_index = original.header.row_index or []
    if len(row_index) != len(original.df):
        raise TemplatePreservationError("无法定位原表数据行，已停止输出以避免破坏排版")
    retained = set(matched)
    deleted_rows = [row_index[index] + 1 for index in range(len(row_index)) if index not in retained]
    if not deleted_rows:
        return
    deleted_set = set(deleted_rows)
    if not relaxed and (any(sheet._charts or sheet._pivots for sheet in ws.parent) or ws._images):
        raise TemplatePreservationError("原表包含暂不能随行安全移动的图表或透视对象，已停止输出，原文件未改动")
    try:
        _adjust_workbook_formulas(ws.parent, {ws.title: deleted_set}, ws, deleted_set, notices=notices if relaxed else None)
        try:
            _adjust_sheet_ranges(ws, deleted_set)
        except ValueError as exc:
            if not relaxed:
                raise
            if notices is not None:
                notices.append(f"部分工作表范围无法自动调整，已继续修改副本：{exc}")
        _move_original_cells(ws, deleted_rows)
    except ValueError as exc:
        raise TemplatePreservationError(f"无法安全保留原表引用或结构：{exc}；已停止输出，原文件未改动") from exc


def _move_original_cells(ws: Worksheet, deleted_rows: list[int]) -> None:
    deleted = set(deleted_rows)
    merged_ranges: list[str] = []
    anchors: list[tuple[CellRange, Any]] = []
    for merged in ws.merged_cells.ranges:
        reference = shift_range_ref(str(merged), deleted)
        if not reference:
            continue
        updated = CellRange(reference)
        if updated.min_row != updated.max_row or updated.min_col != updated.max_col:
            merged_ranges.append(reference)
        if merged.min_row in deleted:
            anchors.append((updated, copy(ws.cell(merged.min_row, merged.min_col))))
    cells = {}
    for (row, column), cell in ws._cells.items():
        if row in deleted:
            continue
        cell.row = row - bisect_left(deleted_rows, row)
        if not isinstance(cell, MergedCell) and cell.hyperlink:
            cell.hyperlink.ref = cell.coordinate
        cells[(cell.row, column)] = cell
    for merged, anchor in anchors:
        anchor.row = merged.min_row
        if anchor.comment:
            anchor.comment = copy(anchor.comment)
        if anchor.hyperlink:
            anchor.hyperlink.ref = anchor.coordinate
        cells[(anchor.row, anchor.column)] = anchor
    ws._cells = cells
    ws.merged_cells = MultiCellRange(merged_ranges)
    dimensions = {index: copy(dimension) for index, dimension in ws.row_dimensions.items()}
    ws.row_dimensions.clear()
    for index, dimension in dimensions.items():
        if index in deleted:
            continue
        dimension.index = index - bisect_left(deleted_rows, index)
        ws.row_dimensions[dimension.index] = dimension
    ws._current_row = ws.max_row if ws._cells else 0


def _adjust_workbook_formulas(workbook: openpyxl.Workbook, deleted: dict[str, set[int]],
                              edited: Worksheet, removed: set[int], *, notices: list[str] | None = None) -> None:
    def update(formula: str, sheet_name: str) -> str:
        try:
            return adjust_formula(formula, sheet_name, deleted)
        except ValueError as exc:
            if notices is None:
                raise
            notices.append(f"部分公式引用未能自动调整，已保留原表达式并继续输出：{exc}")
            return formula

    for sheet in workbook.worksheets:
        for cell in sheet._cells.values():
            if cell.data_type == "f" and not isinstance(cell.value, str):
                if notices is None:
                    raise ValueError("暂不能移动数组公式或数据表公式")
                notices.append("数组公式或数据表公式已随副本保留，其引用范围可能需要核对。")
                continue
            if sheet is edited and cell.row in removed:
                continue
            if cell.data_type == "f":
                cell.value = update(cell.value, sheet.title)
            if not isinstance(cell, MergedCell) and cell.hyperlink and cell.hyperlink.location:
                cell.hyperlink.location = update(cell.hyperlink.location, sheet.title)
        for validation in sheet.data_validations.dataValidation:
            if sheet is edited and not shift_range_ref(str(validation.sqref), removed):
                continue
            for attribute in ("formula1", "formula2"):
                formula = getattr(validation, attribute)
                if formula:
                    if sheet is edited:
                        formula = _move_rule_origin(formula, validation.sqref, removed)
                    setattr(validation, attribute, update(formula, sheet.title))
        for formatting in sheet.conditional_formatting:
            if sheet is edited and not shift_range_ref(str(formatting.sqref), removed):
                continue
            for rule in formatting.rules:
                formulas = [_move_rule_origin(formula, formatting.sqref, removed)
                            if sheet is edited else formula for formula in rule.formula]
                rule.formula = [update(formula, sheet.title) for formula in formulas]
        for table in sheet.tables.values():
            for column in table.tableColumns:
                for attribute in ("calculatedColumnFormula", "totalsRowFormula"):
                    formula = getattr(column, attribute)
                    if formula and formula.text:
                        formula.text = update(formula.text, sheet.title)
        for definition in sheet.defined_names.values():
            if definition.attr_text:
                definition.attr_text = update(definition.attr_text, sheet.title)
    for definition in workbook.defined_names.values():
        if definition.attr_text:
            definition.attr_text = update(definition.attr_text, "")


def _move_rule_origin(formula: str, references: MultiCellRange, deleted: set[int]) -> str:
    ranges = sorted(references.ranges, key=lambda reference: (reference.min_row, reference.min_col))
    if not ranges:
        return formula
    original = ranges[0]
    if original.min_row not in deleted:
        return formula
    for reference in ranges:
        first = reference.min_row
        while first in deleted and first <= reference.max_row:
            first += 1
        if first <= reference.max_row:
            origin = f"{get_column_letter(original.min_col)}{original.min_row}"
            target = f"{get_column_letter(reference.min_col)}{first}"
            translated = Translator(formula if formula.startswith("=") else f"={formula}", origin=origin).translate_formula(target)
            return translated if formula.startswith("=") else translated[1:]
    return formula


def _adjust_sheet_ranges(ws: Worksheet, deleted: set[int]) -> None:
    if ws.auto_filter is not None and ws.auto_filter.ref:
        ws.auto_filter.ref = shift_range_ref(ws.auto_filter.ref, deleted) or None
    for owner in [ws.auto_filter, *(table.autoFilter for table in ws.tables.values() if table.autoFilter)]:
        if owner.sortState:
            owner.sortState.ref = shift_range_ref(owner.sortState.ref, deleted)
            for condition in owner.sortState.sortCondition:
                condition.ref = shift_range_ref(condition.ref, deleted)
    validations = []
    for validation in ws.data_validations.dataValidation:
        reference = shift_range_ref(str(validation.sqref), deleted)
        if reference:
            validation.sqref = reference
            validations.append(validation)
    ws.data_validations.dataValidation = validations
    formatting_list = ConditionalFormattingList()
    for formatting in ws.conditional_formatting:
        reference = shift_range_ref(str(formatting.sqref), deleted)
        if reference:
            updated = copy(formatting)
            updated.sqref = reference
            for rule in formatting.rules:
                formatting_list.add(updated, rule)
    ws.conditional_formatting = formatting_list
    for table in ws.tables.values():
        original = CellRange(table.ref)
        if (table.headerRowCount and original.min_row in deleted
                or table.totalsRowCount and original.max_row in deleted):
            raise ValueError("不能删除 Excel 表格的表头或合计行")
        table.ref = shift_range_ref(table.ref, deleted)
        if table.autoFilter is not None and table.autoFilter.ref:
            table.autoFilter.ref = table.ref
        if table.sortState:
            table.sortState.ref = shift_range_ref(table.sortState.ref, deleted)
            for condition in table.sortState.sortCondition:
                condition.ref = shift_range_ref(condition.ref, deleted)
    if ws.print_area:
        ws.print_area = [shift_range_ref(str(reference), deleted) for reference in ws._print_area.ranges
                         if shift_range_ref(str(reference), deleted)]
    if ws.print_title_rows:
        ws.print_title_rows = shift_range_ref(ws.print_title_rows, deleted) or None
    for page_break in ws.row_breaks.brk:
        page_break.id -= sum(row <= page_break.id for row in deleted)
    for view in ws.views.sheetView:
        if view.topLeftCell:
            view.topLeftCell = _shift_view_cell(view.topLeftCell, deleted)
        if view.pane:
            if view.pane.topLeftCell:
                view.pane.topLeftCell = _shift_view_cell(view.pane.topLeftCell, deleted)
            if view.pane.state in {"frozen", "frozenSplit"} and view.pane.ySplit:
                view.pane.ySplit -= sum(row <= view.pane.ySplit for row in deleted)
        for selection in view.selection:
            if selection.activeCell:
                selection.activeCell = _shift_view_cell(selection.activeCell, deleted)
            if selection.sqref:
                selection.sqref = shift_range_ref(selection.sqref, deleted) or selection.activeCell or "A1"


def _shift_view_cell(reference: str, deleted: set[int]) -> str:
    row, column = coordinate_to_tuple(reference)
    return f"{get_column_letter(column)}{max(1, row - sum(item < row for item in deleted))}"


def _has_special_layout(ws: Worksheet, snap: _Snapshot, rows: list[int]) -> bool:
    if snap.tpl.col_index != list(range(len(snap.tpl.columns))):
        return True
    if rows != list(range(snap.tpl.header_row, ws.max_row)):
        return True
    for index, row in enumerate(rows):
        dimension = ws.row_dimensions.get(row + 1)
        if dimension is not None and (dimension.hidden or dimension.outlineLevel or dimension.collapsed
                                      or dimension.has_style or dimension.thickTop or dimension.thickBot
                                      or dimension.height != snap.height(index)):
            return True
        for cell in ws[row + 1]:
            if cell.comment or cell.hyperlink or cell._style != snap.data_style(index, cell.column):
                return True
    return False


def _patch_cells(ws: Worksheet, snap: _Snapshot, frame: pd.DataFrame, original: pd.DataFrame,
                 rows: list[int], mapping: list[int | None], *, relaxed: bool = False) -> None:
    if len(rows) != len(frame):
        raise TemplatePreservationError("无法定位原表数据行，已停止输出以避免破坏排版")
    positions = [index + 1 for index in snap.tpl.col_index]
    added = len(frame.columns) - len(positions)
    positions.extend(range(ws.max_column + 1, ws.max_column + 1 + added))
    styles = _column_styles(snap, frame, mapping)
    values = {(row + 1, positions[index]): _excel_value(value)
              for row, record in zip(rows, frame.itertuples(index=False, name=None)) for index, value in enumerate(record)}
    for merged in list(ws.merged_cells.ranges):
        affected = [value for (row, column), value in values.items()
                    if merged.min_row <= row <= merged.max_row and merged.min_col <= column <= merged.max_col]
        if affected and any(value != affected[0] for value in affected):
            if not relaxed:
                raise TemplatePreservationError(f"修改后的内容无法放入原合并单元格 {merged}，已停止输出以保留原格式")
            anchor = ws.cell(merged.min_row, merged.min_col)
            style = copy(anchor._style)
            value = anchor.value
            ws.unmerge_cells(str(merged))
            for merged_row in range(merged.min_row, merged.max_row + 1):
                for merged_column in range(merged.min_col, merged.max_col + 1):
                    cell = ws.cell(merged_row, merged_column)
                    cell._style = copy(style)
                    cell.value = value
    for row_index, excel_index in enumerate(rows):
        for column_index, column in enumerate(positions):
            cell = ws.cell(row=excel_index + 1, column=column)
            value = values[(excel_index + 1, column)]
            if isinstance(cell, MergedCell):
                continue
            if column_index < len(snap.tpl.columns):
                previous = _excel_value(original.iloc[row_index, column_index])
                if value == previous:
                    continue
                if cell.data_type == "f" and not relaxed:
                    raise TemplatePreservationError(f"本次处理会把公式 {cell.coordinate} 替换成数值，已停止输出以保护原公式")
                cell.value = value
            else:
                _put(cell, value, styles[column_index][row_index % len(snap.data_styles)])
    for index in range(len(snap.tpl.columns), len(positions)):
        column = positions[index]
        left = ws.cell(row=snap.tpl.header_row, column=column - 1)
        cell = ws.cell(row=snap.tpl.header_row, column=column)
        cell.value = str(frame.columns[index])
        cell._style = copy(left._style)
        ws.column_dimensions[get_column_letter(column)].width = _auto_width(frame.iloc[:, index], str(frame.columns[index]))
    if added and (ws.tables or ws.auto_filter.ref):
        resize = _resize_table_objects if relaxed else _fix_table_objects
        resize(ws, snap.tpl.header_row, positions[-1], max(rows, default=snap.tpl.header_row) + 1)


def _rewrite_header(ws: Worksheet, snap: _Snapshot, df: pd.DataFrame, mapping: list[int | None], columns: list[str]) -> tuple[int, int]:
    """列有增减或换了顺序时重写表头。返回新的 (表头行号, 数据起始行号)。"""
    tpl = snap.tpl
    header_row = tpl.header_row
    # 多行表头（分组表头）在列变了之后没法再对齐：拆掉合并，只保留最后一行当表头
    for rng in list(ws.merged_cells.ranges):
        if tpl.header_row - tpl.header_rows < rng.min_row <= tpl.header_row:
            ws.unmerge_cells(str(rng))
    if tpl.header_rows > 1:
        upper = tpl.header_rows - 1
        ws.delete_rows(tpl.header_row - upper, upper)
        header_row -= upper
    neighbor_header = snap.header_style.get(1)
    width = max(snap.max_col, len(columns))
    for c in range(1, width + 1):  # 先清空整行（原表头可能比新表头宽）
        cell = ws.cell(row=header_row, column=c)
        cell.value = None
        cell._style = StyleArray()
    left_style = neighbor_header
    for j, (name, sc) in enumerate(zip(columns, mapping), start=1):
        cell = ws.cell(row=header_row, column=j)
        if sc is not None:  # 原来就有的列：表头文字和样式都照原样
            cell.value = snap.header_value.get(sc)
            cell._style = copy(snap.header_style[sc])
            left_style = snap.header_style[sc]
        else:  # 新增的列：表头样式照左边那一列
            cell.value = name
            if left_style is not None:
                cell._style = copy(left_style)
    return header_row, header_row + 1


def _column_styles(snap: _Snapshot, df: pd.DataFrame, mapping: list[int | None]) -> list[list[StyleArray | None]]:
    """每一列、每套斑马纹样式。新增的列：优先用类型相同的原列的样式，没有就用左边的列。"""
    tpl = snap.tpl
    by_type: dict[str, int] = {}
    for name, idx in zip(tpl.columns, tpl.col_index):
        by_type.setdefault(tpl.dtypes.get(name, ""), idx + 1)
    result: list[list[StyleArray | None]] = []
    left: int | None = None  # 左边最近一个「原来就有的列」
    for j, sc in enumerate(mapping):
        if sc is not None:
            source = sc
            left = sc
        else:
            source = by_type.get(dtype_label(df.iloc[:, j])) or left
        result.append([snap.data_style(k, source) for k in range(len(snap.data_styles))])
    return result


def _put(cell: Any, value: Any, style: StyleArray | None) -> None:
    if style is not None:
        cell._style = copy(style)
    cell.value = value
    # 新增列借用的样式可能是文本格式 / 常规格式：数字别显示成文本，日期别显示成序列号
    if isinstance(value, (int, float)) and not isinstance(value, bool) and cell.number_format == "@":
        cell.number_format = "General"
    elif isinstance(value, (dt.datetime, dt.date)) and cell.number_format == "General":
        cell.number_format = "yyyy-mm-dd" if not isinstance(value, dt.datetime) or value.time() == dt.time() else "yyyy-mm-dd hh:mm:ss"


def _apply_dims(ws: Worksheet, snap: _Snapshot, df: pd.DataFrame, mapping: list[int | None]) -> None:
    """列宽、隐藏列跟着列走；新增的列按内容自动适配宽度。"""
    ws.column_dimensions.clear()
    for j, sc in enumerate(mapping, start=1):
        letter = get_column_letter(j)
        dim = snap.dims.get(sc) if sc is not None else None
        cd = ws.column_dimensions[letter]
        if dim is not None and dim.width:
            cd.width = dim.width
        else:
            cd.width = _auto_width(df.iloc[:, j - 1], str(df.columns[j - 1]))
        if dim is not None:
            cd.hidden = dim.hidden
            cd.outlineLevel = dim.outline


def _auto_width(series: pd.Series, name: str) -> float:
    widest = max([_display_width(name)] + [_display_width(_excel_value(v)) for v in series.head(300).tolist()])
    return float(min(max(widest + 2, 8), 60))


def _resize_table_objects(ws: Worksheet, header_row: int, width: int, last_row: int) -> None:
    """Keep ordinary filter/table metadata usable after an unrestricted data rewrite."""
    last_row = max(header_row + 1, last_row)
    if ws.auto_filter.ref:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(width)}{last_row}"
        ws.auto_filter.filterColumn = [item for item in ws.auto_filter.filterColumn if item.colId < width]
        ws.auto_filter.sortState = None
    for name in list(ws.tables):
        table = ws.tables[name]
        left, top, right, _ = openpyxl.utils.range_boundaries(table.ref)
        if top != header_row or left > width:
            del ws.tables[name]
            continue
        right = width if len(ws.tables) == 1 else min(right, width)
        table.ref = f"{get_column_letter(left)}{top}:{get_column_letter(right)}{last_row}"
        table.tableColumns = [TableColumn(id=column - left + 1, name=str(ws.cell(header_row, column).value or f"列{column}"))
                              for column in range(left, right + 1)]
        table.totalsRowCount = 0
        table.totalsRowShown = False
        table.sortState = None
        if table.autoFilter is not None:
            table.autoFilter.ref = table.ref
            table.autoFilter.filterColumn = []
            table.autoFilter.sortState = None


def _fix_table_objects(ws: Worksheet, header_row: int, width: int, last_row: int) -> None:
    """原表如果是「Excel 表格」对象或有筛选，范围要跟着新数据区走，否则 Excel 打开会报修复。"""
    for name in list(ws.tables):
        table = ws.tables[name]
        left, top, right, bottom = openpyxl.utils.range_boundaries(table.ref)
        if (len(ws.tables) != 1 or top != header_row or bottom != last_row or table.totalsRowCount
                or len(table.tableColumns) != right - left + 1):
            raise TemplatePreservationError("无法安全扩展原表的 Excel 表格范围，已停止输出")
        for column in range(right + 1, width + 1):
            table.tableColumns.append(TableColumn(id=max(item.id for item in table.tableColumns) + 1,
                                                   name=str(ws.cell(header_row, column).value)))
        table.ref = f"{get_column_letter(left)}{top}:{get_column_letter(width)}{bottom}"
        if table.autoFilter is not None:
            table.autoFilter.ref = table.ref
    if ws.auto_filter is not None and ws.auto_filter.ref:
        left, top, _, _ = openpyxl.utils.range_boundaries(ws.auto_filter.ref)
        if top != header_row:
            raise TemplatePreservationError("筛选区域与原表表头不一致，已停止输出以保留原格式")
        ws.auto_filter.ref = f"{get_column_letter(left)}{header_row}:{get_column_letter(width)}{max(last_row, header_row + 1)}"


# ---------------------------------------------------------------------------
# 新增 sheet（汇总表等）
# ---------------------------------------------------------------------------


def _append_sheet(wb: openpyxl.Workbook, title: str, snap: _Snapshot, df: pd.DataFrame) -> None:
    """在副本里追加一个 sheet，表头和数据样式照着原表。"""
    ws = wb.create_sheet(title=title)
    mapping = _column_map(snap, df)
    styles = _column_styles(snap, df, mapping)
    tpl = snap.tpl
    first_header = snap.header_style.get(tpl.col_index[0] + 1) if tpl.col_index else None
    for j, (name, sc) in enumerate(zip(df.columns, mapping), start=1):
        cell = ws.cell(row=1, column=j, value=str(name))
        style = snap.header_style.get(sc) if sc is not None else first_header
        if style is not None:
            cell._style = copy(style)
        dim = snap.dims.get(sc) if sc is not None else None
        letter = get_column_letter(j)
        ws.column_dimensions[letter].width = dim.width if dim is not None and dim.width else _auto_width(df.iloc[:, j - 1], str(name))
        if dim is not None and dim.hidden:
            ws.column_dimensions[letter].hidden = True
    for k, row in enumerate(df.itertuples(index=False, name=None)):
        for j, value in enumerate(row):
            _put(ws.cell(row=2 + k, column=j + 1), _excel_value(value), styles[j][k % len(snap.data_styles)])
        height = snap.height(k)
        if height:
            ws.row_dimensions[2 + k].height = height
    ws.freeze_panes = "A2"
