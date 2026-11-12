"""按原文件样式写出结果：复制一份原始 Excel，在里面修改 / 删除 / 新增。

财务同事要的是「输入什么样，输出还是什么样」，而不是一张格式全新的表。所以输出不是新建一个空白 Excel，
而是把原始文件复制一份，然后：

- 标题行、合并单元格、表头样式、列宽、隐藏列、冻结窗格、数字格式、斑马纹……都沿用原文件
- 可定位原始记录时，值、样式、批注、链接和行属性随记录移动；静态底色不会重新交替套用
- 不能逐行定位或改变列结构时，沿用模板重建并按保真检查报告可能的格式变化
- 新增的列：表头和数据样式照着相邻列（或同类型的列）来；删掉的列直接去掉；列顺序变了就跟着变
- 结果里多出来的 sheet（例如汇总表）：追加到这份副本里，表头和数据样式照着原表
- 原文件里没被动过的 sheet 原样保留

支持 .xlsx / .xlsm 的原文件。优先保留原格式；结构不兼容时重新加载副本基线继续修改，并报告格式变化。
"""

from __future__ import annotations

import datetime as dt
import difflib
import re
import warnings
from bisect import bisect_left
from collections import Counter
from copy import copy
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from xml.etree import ElementTree
from zipfile import ZipFile

import numpy as np
import openpyxl
import pandas as pd
from openpyxl.cell.cell import MergedCell
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.formula.translate import Translator
from openpyxl.styles import PatternFill
from openpyxl.styles.cell_style import StyleArray
from openpyxl.utils import coordinate_to_tuple, get_column_letter
from openpyxl.worksheet.cell_range import CellRange, MultiCellRange
from openpyxl.worksheet.table import TableColumn
from openpyxl.worksheet.worksheet import Worksheet

from sheetmate.engine.workbooks.profile import dtype_label
from sheetmate.engine.workbooks.reader import ReadError, SheetData, read_workbook
from sheetmate.engine.workbooks.row_references import InsertedRows, adjust_formula, as_row_map, shift_range_ref
from sheetmate.engine.workbooks.ooxml_writer import edit_with_ooxml
from sheetmate.engine.workbooks.writer import _display_width, _excel_value, safe_sheet_name

TEMPLATE_SUFFIXES = {".xlsx", ".xlsm"}


class TemplatePreservationError(ValueError):
    """保真写回失败。sheet 为出问题的原工作表名（已知时）：只让这张表降级到兼容模式，其余表仍精确写回。"""

    sheet: str | None = None


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
    lineage_id: str = ""  # 本次读取的原始行身份；跨步骤/断点恢复时保持不变

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
            "lineage_id": self.lineage_id,
        }

    @classmethod
    def from_persist(cls, alias: str, data: dict[str, Any], path: Path) -> TemplateSource:
        return cls(
            alias=alias, path=path, sheet=data["sheet"], header_row=int(data["header_row"]),
            header_rows=int(data.get("header_rows", 1)), col_index=list(data["col_index"]), columns=list(data["columns"]),
            dtypes=dict(data.get("dtypes", {})), file_id=data.get("file_id"), lineage_id=data.get("lineage_id", ""),
        )


@dataclass
class OutputPlan:
    """一个要输出的 sheet。edit=True：改写原文件里 template 对应的 sheet；否则新建一个 sheet。"""

    name: str
    df: pd.DataFrame
    template: TemplateSource | None = None
    edit: bool = False
    source_rows: list[int] | None = None  # 每个结果行对应的原始数据行序号（0 起）


class _OriginalReadCache:
    """One source scan per file, with separate views for each header hint."""

    def __init__(self, sources: list[TemplateSource] | None = None):
        self.raws: dict[Path, list[Any]] = {}
        self.sheets: dict[tuple[Path, str, int, int], SheetData] = {}
        self.hints: dict[Path, dict[str, dict[str, int]]] = {}
        for source in sources or []:
            self.hints.setdefault(source.path.resolve(), {})[source.sheet] = {
                "header_row": source.header_row, "header_rows": source.header_rows,
            }

    def read(self, source: TemplateSource) -> SheetData:
        path = source.path.resolve()
        key = path, source.sheet, source.header_row, source.header_rows
        if key not in self.sheets:
            hints = {**self.hints.get(path, {}), source.sheet: {
                "header_row": source.header_row, "header_rows": source.header_rows,
            }}
            sheets = read_workbook(path, header_hints=hints, include_hidden=True, raw_cache=self.raws,
                                   sheet_names=frozenset(hints))
            for sheet in sheets:
                hint = hints.get(sheet.name)
                if hint is not None:
                    self.sheets[path, sheet.name, hint["header_row"], hint["header_rows"]] = sheet
            if key not in self.sheets:
                names = "、".join(raw.name for raw in self.raws.get(path, [])) or "、".join(sheet.name for sheet in sheets)
                raise ReadError(f"文件「{path.name}」里没有工作表「{source.sheet}」，现有：{names}")
        return self.sheets[key]


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
    col_formats: dict[int, str] = field(default_factory=dict)  # 工作表列号 → 数据行的数字格式（给新增列选格式用）

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
        col_formats = {c: ws.cell(row=first, column=c).number_format for c in range(1, max_col + 1)} \
            if first <= (ws.max_row or 0) else {}
        return cls(tpl=tpl, header_value=header_value, header_style=header_style, data_styles=variants or [{}],
                   row_heights=heights or [None], dims=_read_dims(ws), max_col=max_col, col_formats=col_formats)

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
    read_cache = _OriginalReadCache([plan.template for plan in plans if plan.template])
    lossy = False
    try:
        _check_package(tpl.path)
    except TemplatePreservationError:
        lossy = True
    mode = _ooxml_mode()
    if mode != "off" and (lossy or mode == "always"):
        # 直接编辑 XML：未改动的部件（切片器、控件、形状等）按字节原样保留，无需"可能变化"的提示。
        edited = edit_with_ooxml(tpl.path, path, plans, read_cache.read)
        if edited is not None:
            return edited
    if lossy:
        notices.append("工作簿包含当前编辑引擎不能完整保留的扩展对象，已在副本继续处理，这些对象可能变化。")
    relaxed_sheets: set[str] = set()
    relax_all = False
    while True:  # 每次失败要么新增一张降级的工作表、要么整体降级，次数有限
        with warnings.catch_warnings(record=True) as observed:
            warnings.simplefilter("always", UserWarning)
            workbook = openpyxl.load_workbook(tpl.path, keep_vba=tpl.path.suffix.lower() == ".xlsm", rich_text=True)
            try:
                written = _write_workbook(workbook, path, plans, tpl, relaxed=relax_all, relaxed_sheets=relaxed_sheets,
                                          notices=notices, read_cache=read_cache)
            except TemplatePreservationError as exc:
                if relax_all:
                    raise
                if exc.sheet is not None and exc.sheet not in relaxed_sheets:
                    relaxed_sheets.add(exc.sheet)
                    notices.append(f"工作表「{exc.sheet}」的部分修改与原有模板结构不兼容，已重新加载干净副本并对该工作表"
                                   "采用兼容方式完成修改；该工作表受影响区域的格式或引用可能变化，其余工作表不受影响。")
                else:
                    relax_all = True
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


def _ooxml_mode() -> str:
    from sheetmate.core.config.settings import settings

    return settings.ooxml_edit


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
                    *, relaxed: bool = False, relaxed_sheets: set[str] | frozenset[str] = frozenset(),
                    notices: list[str] | None = None,
                    read_cache: _OriginalReadCache | None = None) -> list[dict[str, Any]]:
    notices = notices if notices is not None else []
    read_cache = read_cache if read_cache is not None else _OriginalReadCache(
        [base, *(plan.template for plan in plans if plan.template)])
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
            sheet_relaxed = relaxed or source.sheet in relaxed_sheets
            try:
                if title != sheet.title and _has_formulas(workbook) and not sheet_relaxed:
                    raise TemplatePreservationError("重命名工作表可能破坏现有公式引用，请保留原工作表名称")
                _rewrite_sheet(sheet, snap, plan.df, relaxed=sheet_relaxed, notices=notices, read_cache=read_cache,
                               source_rows=plan.source_rows)
            except TemplatePreservationError as exc:
                if exc.sheet is None:
                    exc.sheet = source.sheet
                raise
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
    # 只看已存在的单元格；迭代 sheet 的行会为整个外框范围逐格创建空单元格，大表上又慢又吃内存。
    return any(cell.data_type == "f" for sheet in workbook for cell in sheet._cells.values())


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
                   notices: list[str] | None = None, read_cache: _OriginalReadCache | None = None,
                   source_rows: list[int] | None = None) -> None:
    notices = notices if notices is not None else []
    tpl = snap.tpl
    mapping = _column_map(snap, df)
    columns = [str(c) for c in df.columns]
    same_columns = columns == tpl.columns
    header_row, first_data = tpl.header_row, tpl.header_row + 1

    original = (read_cache if read_cache is not None else _OriginalReadCache()).read(tpl)
    if len(original.df.columns) != len(tpl.columns) and not relaxed:
        raise TemplatePreservationError("原工作表列结构已变化，无法安全编辑")
    profile = _style_profile(ws, [row + 1 for row in (original.header.row_index or [])], snap.max_col)
    if profile is not None:  # 用全部数据行的众数样式，而不是只看前两行
        snap = replace(snap, data_styles=profile[0], row_heights=profile[1])
    compatible_columns = columns[:len(tpl.columns)] == tpl.columns and len(original.df.columns) == len(tpl.columns)
    if source_rows is not None:
        if (not isinstance(source_rows, list) or len(source_rows) != len(df)
                or any(type(index) is not int or not 0 <= index < len(original.df) for index in source_rows)
                or len(set(source_rows)) != len(source_rows)):
            raise TemplatePreservationError("结果的原始行身份不完整或不唯一，无法安全保留逐行格式")
        if compatible_columns:
            _patch_identified_rows(ws, snap, df, original, source_rows, mapping, relaxed=relaxed, notices=notices)
            return
    elif compatible_columns:
        # 兼容旧调用方：只在完整记录能唯一定位时识别重排，不能把等行数当作等行顺序。
        matched = _match_unique_records(df.iloc[:, :len(tpl.columns)], original.df)
        if matched is not None:
            _patch_identified_rows(ws, snap, df, original, matched, mapping, relaxed=relaxed, notices=notices)
            return
    if source_rows is None and compatible_columns and original.header.row_index:
        operations = _align_records(df, original.df, len(tpl.columns))
        if operations is not None and not _is_positional(operations, len(original.df)):
            _patch_aligned_rows(ws, snap, df, original, operations, mapping, relaxed=relaxed, notices=notices)
            return
    if (columns[:len(tpl.columns)] == tpl.columns and len(df) == len(original.df)
            and len(original.df.columns) == len(tpl.columns)):
        notices.append("未能验证结果与原始记录的逐行对应关系，已按现有行位置写入；涉及重排时，行级格式、批注和链接需核对。")
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
            retained = set(matched)
            removed = sorted(previous_rows[index] + 1 for index in range(len(previous_rows)) if index not in retained)
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

    footer = _capture_footer(ws, original)  # 重建会删掉表头以下所有行，合计行先记下来，数据写完后放回去
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

    if footer is not None:
        _restore_footer(ws, footer, mapping, len(df), first_data, notices)

    if relaxed:
        _resize_table_objects(ws, header_row, max(len(df.columns), 1), first_data + max(len(df) - 1, 0))
    else:
        _fix_table_objects(ws, header_row, max(len(df.columns), 1), first_data + max(len(df) - 1, 0))


# ---------------------------------------------------------------------------
# 重建数据区时保留页脚合计行
# ---------------------------------------------------------------------------


@dataclass
class _Footer:
    cells: list[tuple[int, int, Any, str, Any, Any]]  # (原行号, 原列号, 值, 数据类型, 样式, 批注)
    heights: dict[int, float | None]
    merges: list[tuple[int, int, int, int]]  # min_row, min_col, max_row, max_col（只含整块落在页脚行内的）
    first_row: int
    old_first_data: int
    old_last_data: int  # 原数据区最后一行（Excel 行号）
    old_rows: int


def _capture_footer(ws: Worksheet, original: SheetData) -> _Footer | None:
    footer_index = original.header.footer_rows or []
    data_index = original.header.row_index or []
    if not footer_index:
        return None
    rows = {index + 1 for index in footer_index}
    cells = [(row, column, cell.value, cell.data_type, copy(cell._style), copy(cell.comment) if cell.comment else None)
             for (row, column), cell in ws._cells.items() if row in rows and not isinstance(cell, MergedCell)]
    merges = [(m.min_row, m.min_col, m.max_row, m.max_col) for m in ws.merged_cells.ranges
              if m.min_row >= min(rows) and m.max_row <= max(rows)]
    heights = {row: ws.row_dimensions[row].height if row in ws.row_dimensions else None for row in rows}
    last_data = data_index[-1] + 1 if data_index else min(rows) - 1
    first_data = data_index[0] + 1 if data_index else min(rows)
    return _Footer(cells, heights, merges, min(rows), first_data, last_data, len(data_index))


def _remap_columns(formula: str, columns: dict[int, int]) -> str | None:
    """按“原列号 → 新列号”改写公式里本表的列引用；有引用落在已不存在的列上时返回 None（不猜）。"""
    from openpyxl.formula import Tokenizer
    from openpyxl.utils import column_index_from_string

    part = re.compile(r"^(\$?)([A-Za-z]{1,3})(\$?\d*)$")

    def convert(text: str) -> str | None:
        match = part.match(text)
        if not match:
            return None
        new = columns.get(column_index_from_string(match.group(2).upper()))
        return None if new is None else f"{match.group(1)}{get_column_letter(new)}{match.group(3)}"

    try:
        tokens = Tokenizer(formula if formula.startswith("=") else "=" + formula).items
    except Exception:
        return None
    pieces: list[str] = []
    for token in tokens:
        if token.type == "OPERAND" and token.subtype == "RANGE" and "!" not in token.value:
            converted = [convert(item) for item in token.value.split(":")]
            if any(item is None for item in converted):
                return None
            pieces.append(":".join(converted))  # type: ignore[arg-type]
        else:
            pieces.append(token.value)
    return "=" + "".join(pieces)


def _restore_footer(ws: Worksheet, footer: _Footer, mapping: list[int | None], new_rows: int, first_data: int,
                    notices: list[str]) -> None:
    """把页脚放回数据区下面：列位置跟着列映射走，公式的列引用随之改写，行范围随数据行的增减扩缩。"""
    columns = {source: index + 1 for index, source in enumerate(mapping) if source is not None}
    last_new = first_data + new_rows - 1
    gap = footer.first_row - footer.old_last_data - 1
    base = last_new + 1 + gap
    row_map: Any = None
    if new_rows > footer.old_rows:
        row_map = InsertedRows(footer.old_last_data + 1, new_rows - footer.old_rows, True)
    elif new_rows < footer.old_rows:
        row_map = as_row_map(set(range(first_data + new_rows, footer.old_last_data + 1)))
    for row, column, value, kind, style, comment in footer.cells:
        target_column = columns.get(column)
        if target_column is None:
            continue  # 这一列已经不在结果里了
        cell = ws.cell(base + (row - footer.first_row), target_column)
        cell._style = style
        if kind == "f" and isinstance(value, str):
            remapped = _remap_columns(value, columns)
            if remapped is None:
                notices.append(f"合计行里的公式 {value} 引用了已经不在结果里的列，请核对。")
                remapped = value
            elif row_map is not None:
                try:
                    remapped = adjust_formula(remapped, ws.title, {ws.title: row_map})
                except ValueError:
                    notices.append(f"合计行里的公式 {value} 的范围未能随数据行自动调整，请核对。")
            cell.value = remapped
        else:
            cell.value = value
        if comment is not None:
            cell.comment = comment
    for row, height in footer.heights.items():
        if height:
            ws.row_dimensions[base + (row - footer.first_row)].height = height
    for min_row, min_col, max_row, max_col in footer.merges:
        new_min, new_max = columns.get(min_col), columns.get(max_col)
        if new_min is not None and new_max is not None and new_min <= new_max:
            offset = base - footer.first_row
            ws.merge_cells(start_row=min_row + offset, start_column=new_min, end_row=max_row + offset, end_column=new_max)


# ---------------------------------------------------------------------------
# 新增 / 删除 / 修改行：按记录对齐，只动该动的行
# ---------------------------------------------------------------------------

_MAX_ALIGN_ROWS = 30_000


def _rows_equal_mask(frame: pd.DataFrame, original: pd.DataFrame) -> np.ndarray:
    """逐行是否与原行完全一致（数值允许 1e-12 的相对误差，两边都是空值算相等）。两份数据行列数必须相同。"""
    mask = np.ones(len(frame), dtype=bool)
    for column in range(frame.shape[1]):
        left, right = frame.iloc[:, column], original.iloc[:, column]
        both_missing = left.isna().to_numpy() & right.isna().to_numpy()
        try:
            if (pd.api.types.is_numeric_dtype(left) and pd.api.types.is_numeric_dtype(right)
                    and not pd.api.types.is_bool_dtype(left) and not pd.api.types.is_bool_dtype(right)):
                same = np.isclose(left.to_numpy(dtype=float, na_value=np.nan), right.to_numpy(dtype=float, na_value=np.nan),
                                  rtol=1e-12, atol=1e-12)
            else:
                same = np.asarray(left.astype(object).to_numpy() == right.astype(object).to_numpy(), dtype=bool)
        except Exception:
            same = np.zeros(len(left), dtype=bool)
        mask &= same | both_missing
    return mask


def _align_records(frame: pd.DataFrame, original: pd.DataFrame, columns: int) -> list[tuple[str, int | None]] | None:
    """把结果的每一行对应到原表：("keep", 原行) 没变、("edit", 原行) 改了内容、("new", None) 新增；
    没出现在结果里的原行视为删除。对不上、拿不准时返回 None，由调用方走原有的保守路径。"""
    result = frame.iloc[:, :columns].reset_index(drop=True)
    source = original.set_axis(result.columns, axis=1).reset_index(drop=True)
    total, previous = len(result), len(source)
    common = min(total, previous)
    if common:
        mask = _rows_equal_mask(result.iloc[:common], source.iloc[:common])
        if float(mask.mean()) >= 0.5:  # 大半行仍在原位：只是末尾追加/截断，其余按位置判断是否被改
            return ([("keep" if same else "edit", index) for index, same in enumerate(mask)]
                    + [("new", None)] * (total - common))
    if max(total, previous) > _MAX_ALIGN_ROWS:
        return None
    try:
        before = [tuple(_excel_value(value) for value in record) for record in source.itertuples(index=False, name=None)]
        after = [tuple(_excel_value(value) for value in record) for record in result.itertuples(index=False, name=None)]
        blocks = difflib.SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
    except TypeError:  # 含不可哈希的值，不能当作确定的记录键
        return None
    operations: list[tuple[str, int | None] | None] = [None] * total
    kept = 0
    for tag, start, stop, new_start, new_stop in blocks:
        if tag == "equal":
            for offset in range(new_stop - new_start):
                operations[new_start + offset] = ("keep", start + offset)
            kept += new_stop - new_start
        elif tag in {"replace", "insert"}:
            paired = min(stop - start, new_stop - new_start) if tag == "replace" else 0
            for offset in range(new_stop - new_start):
                operations[new_start + offset] = ("edit", start + offset) if offset < paired else ("new", None)
    if kept < 0.3 * min(total, previous):  # 几乎没有行对得上：不是增删改，而是另一份数据
        return None
    return [item for item in operations if item is not None]


def _is_positional(operations: list[tuple[str, int | None]], previous: int) -> bool:
    return len(operations) == previous and all(kind != "new" and index == position
                                               for position, (kind, index) in enumerate(operations))


def _shift_cells_down(ws: Worksheet, mapping: InsertedRows) -> None:
    """把 at 行及以下整体下移 count 行：单元格（含样式、批注、链接）、合并区域、行属性一起走。"""
    for merged in ws.merged_cells.ranges:
        if merged.min_row < mapping.at <= merged.max_row:
            raise TemplatePreservationError(f"不能在纵向合并单元格 {merged} 中间插入新行，已停止输出，原文件未改动")
    plain = InsertedRows(mapping.at, mapping.count, False)  # 合并区域不随“末尾追加”扩展
    merged_ranges = [shift_range_ref(str(merged), plain) for merged in ws.merged_cells.ranges]
    cells = {}
    for (row, column), cell in ws._cells.items():
        if row >= mapping.at:
            cell.row = row + mapping.count
            if not isinstance(cell, MergedCell) and cell.hyperlink:
                cell.hyperlink.ref = cell.coordinate
        cells[(cell.row, column)] = cell
    ws._cells = cells
    ws.merged_cells = MultiCellRange(merged_ranges)
    dimensions = {index: copy(dimension) for index, dimension in ws.row_dimensions.items()}
    ws.row_dimensions.clear()
    for index, dimension in dimensions.items():
        dimension.index = plain.map_row(index)
        ws.row_dimensions[dimension.index] = dimension
    ws._current_row = ws.max_row if ws._cells else 0


def _insert_blank_rows(ws: Worksheet, at: int, count: int, *, extend_end: bool, relaxed: bool,
                       notices: list[str]) -> None:
    """在第 at 行前插入 count 个空行，并像 Excel 一样调整公式、筛选、条件格式、数据验证等引用。
    extend_end 为真（在数据区末尾追加）时，恰好止于数据区末行的区域引用一并扩展，新行自动被合计覆盖。"""
    if count <= 0:
        return
    if not relaxed and (any(sheet._charts or sheet._pivots for sheet in ws.parent) or ws._images):
        raise TemplatePreservationError("原表包含暂不能随行安全移动的图表或透视对象，已停止输出，原文件未改动")
    mapping = InsertedRows(at, count, extend_end)
    try:
        _adjust_workbook_formulas(ws.parent, {ws.title: mapping}, ws, set(), notices=notices if relaxed else None)
        try:
            _adjust_sheet_ranges(ws, mapping)
        except ValueError as exc:
            if not relaxed:
                raise
            notices.append(f"部分工作表范围无法自动调整，已继续修改副本：{exc}")
    except ValueError as exc:
        raise TemplatePreservationError(f"无法安全保留原表引用或结构：{exc}；已停止输出，原文件未改动") from exc
    _shift_cells_down(ws, mapping)


def _write_new_rows(ws: Worksheet, snap: _Snapshot, frame: pd.DataFrame, rows: list[int], ordinals: list[int],
                    mapping: list[int | None], positions: list[int]) -> None:
    """把新增的记录写进已经腾出来的空行，样式取原表数据行的众数样式（斑马纹按位置交替）。"""
    styles = _column_styles(snap, frame, mapping)
    for excel_row, ordinal, record in zip(rows, ordinals, frame.itertuples(index=False, name=None)):
        for column_index, column in enumerate(positions):
            cell = ws.cell(row=excel_row, column=column)
            if isinstance(cell, MergedCell):
                continue
            _put(cell, _excel_value(record[column_index]), styles[column_index][ordinal % len(snap.data_styles)])
        height = snap.height(ordinal)
        if height:
            ws.row_dimensions[excel_row].height = height


def _patch_aligned_rows(ws: Worksheet, snap: _Snapshot, frame: pd.DataFrame, original: SheetData,
                        operations: list[tuple[str, int | None]], mapping: list[int | None], *, relaxed: bool,
                        notices: list[str]) -> None:
    """新增、删除、修改任意组合：没变的行原样不动（格式、批注、链接、行高都在），
    删掉的行按 Excel 的方式删除并调整引用，新增的行插在正确位置并沿用原表样式，改过的行只改变化的单元格。"""
    previous_rows = original.header.row_index or []
    if len(previous_rows) != len(original.df):
        raise TemplatePreservationError("无法定位原表数据行，已停止输出以避免破坏排版")
    retained = sorted(index for kind, index in operations if kind != "new" and index is not None)
    kept = set(retained)
    removed = sorted(row + 1 for position, row in enumerate(previous_rows) if position not in kept)
    banding = _detect_banding(ws, [row + 1 for row in previous_rows]) if any(kind == "new" for kind, _ in operations) else None
    positions = _column_positions(ws, snap, frame)  # 先算：插入/删除行不改变列，但必须在任何改动前确定新增列的位置
    if removed:
        _delete_original_rows(ws, original, retained, relaxed=relaxed, notices=notices)
    after_delete = {index: previous_rows[index] + 1 - bisect_left(removed, previous_rows[index] + 1) for index in retained}
    last_data = max(after_delete.values(), default=None)

    groups: list[tuple[int, list[int], bool]] = []  # (插入位置, 结果行号列表, 是否在数据区末尾追加)
    pending: list[int] = []
    previous_row: int | None = None
    for position, (kind, index) in enumerate(operations):
        if kind == "new":
            pending.append(position)
            continue
        row = after_delete[index]
        if pending:
            groups.append((row if previous_row is None else previous_row + 1, pending, False))
            pending = []
        previous_row = row
    if pending:
        at = snap.tpl.header_row + 1 if previous_row is None else previous_row + 1
        groups.append((at, pending, previous_row is not None and previous_row == last_data))
    for at, indices, extend_end in sorted(groups, key=lambda group: group[0], reverse=True):
        _insert_blank_rows(ws, at, len(indices), extend_end=extend_end, relaxed=relaxed, notices=notices)

    inserted = [(at, len(indices)) for at, indices, _ in groups]
    final: dict[int, int] = {}
    for position, (kind, index) in enumerate(operations):
        if kind != "new":
            row = after_delete[index]
            final[position] = row + sum(count for at, count in inserted if at <= row)
    for at, indices, _ in groups:
        base = at + sum(count for other, count in inserted if other < at)
        for offset, position in enumerate(indices):
            final[position] = base + offset

    kept_positions = [position for position, (kind, _) in enumerate(operations) if kind != "new"]
    new_positions = [position for position, (kind, _) in enumerate(operations) if kind == "new"]
    _patch_cells(ws, snap, frame.iloc[kept_positions].reset_index(drop=True),
                 original.df.iloc[[operations[position][1] for position in kept_positions]].reset_index(drop=True),
                 [final[position] - 1 for position in kept_positions], mapping, relaxed=relaxed, positions=positions)
    if new_positions:
        _write_new_rows(ws, snap, frame.iloc[new_positions].reset_index(drop=True), [final[position] for position in new_positions],
                        new_positions, mapping, positions)
    if banding:
        _apply_banding(ws, banding, sorted(final.values()), [])  # 增删行时重排条纹是正常处理，不当作需要核对的提示


def _match_unique_records(frame: pd.DataFrame, original: pd.DataFrame) -> list[int] | None:
    """旧调用方没有行身份时，只接受完整记录的唯一匹配。"""
    if len(frame) == len(original) and len(frame) > 0:
        # 向量化预判：大半数行仍在原位置，说明是就地改值而不是重排，不必逐格归一化整张表。
        try:
            right = original.set_axis(frame.columns, axis=1)
            equal = frame.reset_index(drop=True).eq(right.reset_index(drop=True))
            equal |= frame.reset_index(drop=True).isna() & right.reset_index(drop=True).isna()
            in_place = equal.all(axis=1)
            if bool(in_place.all()):
                return list(range(len(frame)))
            if float(in_place.mean()) >= 0.5:
                return None
        except Exception:  # 类型不可比较时回到逐行归一化路径
            pass
    source = [tuple(_excel_value(value) for value in record)
              for record in original.itertuples(index=False, name=None)]
    result = [tuple(_excel_value(value) for value in record)
              for record in frame.itertuples(index=False, name=None)]
    if source == result:
        return list(range(len(source)))
    try:
        positions: dict[tuple, int] = {}
        duplicates: set[tuple] = set()
        for index, record in enumerate(source):
            if record in positions:
                duplicates.add(record)
            positions[record] = index
        if len(set(result)) != len(result) or any(record not in positions or record in duplicates for record in result):
            return None
        return [positions[record] for record in result]
    except TypeError:  # 非标量值不能当作确定的记录键
        return None


def _patch_identified_rows(ws: Worksheet, snap: _Snapshot, frame: pd.DataFrame, original: SheetData,
                           matched: list[int], mapping: list[int | None], *, relaxed: bool,
                           notices: list[str]) -> None:
    previous_rows = original.header.row_index or []
    if len(previous_rows) != len(original.df):
        raise TemplatePreservationError("无法定位原表数据行，已停止输出以避免破坏排版")
    retained = set(matched)
    removed = sorted(row + 1 for index, row in enumerate(previous_rows) if index not in retained)
    # 只有行被重新排序时才重排斑马纹（底色被搬乱）；纯筛选/删行沿用“静态底色跟随记录”的既有约定。
    banding = _detect_banding(ws, [row + 1 for row in previous_rows]) if matched != sorted(matched) else None
    _delete_original_rows(ws, original, sorted(matched), relaxed=relaxed, notices=notices)
    remaining = [previous_rows[index] + 1 - bisect_left(removed, previous_rows[index] + 1) for index in matched]
    destinations = sorted(remaining)
    row_map = dict(zip(remaining, destinations))
    if any(source != target for source, target in row_map.items()):
        _permute_original_rows(ws, row_map, notices)
    if banding:
        _apply_banding(ws, banding, destinations, notices)
    _patch_cells(ws, snap, frame, original.df.iloc[matched].reset_index(drop=True),
                 [row - 1 for row in destinations], mapping, relaxed=relaxed)


def _fill_key(cell: Any) -> tuple | None:
    """单元格底色的可比较标识；无填充为 None。"""
    fill = cell.fill
    if fill is None or fill.fill_type in (None, "none"):
        return None
    color = fill.fgColor
    return (fill.fill_type, color.type, color.rgb if color.type == "rgb" else color.theme if color.type == "theme" else color.indexed,
            round(float(color.tint or 0), 4))


def _detect_banding(ws: Worksheet, rows: list[int]) -> dict[str, Any] | None:
    """识别隔行斑马纹：数据行的底色整体在两种样式间交替。

    斑马纹属于布局，排序/重排后应按位置重新交替；人工给个别记录涂的特殊底色属于记录，随记录移动。
    返回 {"keys": {列: (甲色, 乙色)}, "fills": {(奇偶, 列): 底色对象}, "index": {行号: 位置序号}}，识别不出则为 None。
    """
    if len(rows) < 4:
        return None
    row_set = set(rows)
    columns = sorted({column for (row, column) in ws._cells if row in row_set})
    signatures = [tuple(_fill_key(ws._cells[row, column]) if (row, column) in ws._cells else None for column in columns)
                  for row in rows]
    counts: dict[tuple, int] = {}
    for signature in signatures:
        counts[signature] = counts.get(signature, 0) + 1
    ranked = sorted(counts, key=counts.get, reverse=True)
    if len(ranked) < 2 or counts[ranked[0]] + counts[ranked[1]] < 0.8 * len(rows):
        return None
    pair = (ranked[0], ranked[1])
    # 每个奇偶位置上最常见的条纹样式；两个奇偶必须不同，才是交替而不是随机分布。
    stripe: dict[int, tuple] = {}
    for parity in (0, 1):
        here = [signature for index, signature in enumerate(signatures) if index % 2 == parity and signature in pair]
        if not here:
            return None
        stripe[parity] = max(set(here), key=here.count)
    if stripe[0] == stripe[1]:
        return None
    # 绝大多数行都符合"按奇偶位置交替"；个别特殊标记行（红底等）不破坏判断。
    if sum(signature == stripe[index % 2] for index, signature in enumerate(signatures)) < 0.8 * len(rows):
        return None
    fills: dict[tuple[int, int], Any] = {}
    for parity in (0, 1):
        for index, signature in enumerate(signatures):
            if index % 2 != parity or signature != stripe[parity]:
                continue
            for column in columns:
                cell = ws._cells.get((rows[index], column))
                fills.setdefault((parity, column), copy(cell.fill) if cell is not None else PatternFill())
    keys = {column: (stripe[0][position], stripe[1][position]) for position, column in enumerate(columns)}
    return {"keys": keys, "fills": fills}


def _apply_banding(ws: Worksheet, banding: dict[str, Any], destinations: list[int], notices: list[str]) -> None:
    """按最终的数据行顺序重新交替斑马纹；不属于两种条纹的特殊底色保持跟随记录。"""
    position = {row: index for index, row in enumerate(destinations)}
    restored = 0
    for (row, column), cell in ws._cells.items():
        index = position.get(row)
        if index is None or isinstance(cell, MergedCell) or (index % 2, column) not in banding["fills"]:
            continue
        if _fill_key(cell) in banding["keys"].get(column, ()):
            cell.fill = copy(banding["fills"][index % 2, column])
            restored += 1
    if restored:
        notices.append("检测到隔行斑马纹，已按最终的行顺序重新交替；记录自带的特殊底色仍随记录移动。")


def _permute_original_rows(ws: Worksheet, row_map: dict[int, int], notices: list[str]) -> None:
    """按经过验证的行身份移动原单元格；值、样式、批注、链接及行属性一起移动。"""
    merged_ranges: list[str] = []
    for merged in ws.merged_cells.ranges:
        mapped = [row_map.get(row, row) for row in range(merged.min_row, merged.max_row + 1)]
        if mapped != list(range(mapped[0], mapped[0] + len(mapped))):
            raise TemplatePreservationError(f"排序会拆散纵向合并单元格 {merged}，无法安全保留原结构")
        updated = CellRange(str(merged))
        updated.min_row, updated.max_row = mapped[0], mapped[-1]
        merged_ranges.append(str(updated))
    formulas: dict[tuple[int, int], str] = {}
    for (row, column), cell in ws._cells.items():
        target = row_map.get(row, row)
        if target == row or cell.data_type != "f":
            continue
        if not isinstance(cell.value, str):
            raise TemplatePreservationError("暂不能安全排序含数组公式或数据表公式的记录")
        try:
            formulas[row, column] = Translator(cell.value, origin=cell.coordinate).translate_formula(
                f"{get_column_letter(column)}{target}")
        except Exception as exc:
            raise TemplatePreservationError(f"无法安全移动公式 {cell.coordinate}：{exc}") from exc
    if any(cell.data_type == "f" for sheet in ws.parent for cell in sheet._cells.values()):
        notices.append("排序已平移记录内公式的相对引用；其他单元格的公式仍按原坐标引用，请核对跨记录或跨表计算。")
    if ws.conditional_formatting or ws.data_validations.count:
        notices.append("排序保留了条件格式和数据验证的原作用范围；仅针对个别记录的规则需核对。")
    if any(sheet._charts or sheet._pivots for sheet in ws.parent) or ws._images:
        notices.append("排序保留了图表、图片及透视对象的原锚点和引用范围，请核对其与记录的对应关系。")
    cells = {}
    for (row, column), cell in ws._cells.items():
        if (row, column) in formulas:
            cell.value = formulas[row, column]
        cell.row = row_map.get(row, row)
        if not isinstance(cell, MergedCell) and cell.hyperlink:
            cell.hyperlink.ref = cell.coordinate
        cells[cell.row, column] = cell
    ws._cells = cells
    ws.merged_cells = MultiCellRange(merged_ranges)
    dimensions = {index: copy(dimension) for index, dimension in ws.row_dimensions.items()}
    ws.row_dimensions.clear()
    for index, dimension in dimensions.items():
        dimension.index = row_map.get(index, index)
        ws.row_dimensions[dimension.index] = dimension
    if formulas and ws.parent.calculation is not None:
        ws.parent.calculation.fullCalcOnLoad = True


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


def _adjust_sheet_ranges(ws: Worksheet, deleted: "set[int] | InsertedRows") -> None:
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
        if isinstance(deleted, set):
            page_break.id -= sum(row <= page_break.id for row in deleted)
        else:
            page_break.id = deleted.map_row(page_break.id)
    for view in ([] if not isinstance(deleted, set) else ws.views.sheetView):
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


def _column_positions(ws: Worksheet, snap: _Snapshot, frame: pd.DataFrame) -> list[int]:
    """结果每一列在工作表里的列号：原有列沿用原位置，新增列接在最右边。"""
    positions = [index + 1 for index in snap.tpl.col_index]
    added = len(frame.columns) - len(positions)
    positions.extend(range(ws.max_column + 1, ws.max_column + 1 + added))
    return positions


def _patch_cells(ws: Worksheet, snap: _Snapshot, frame: pd.DataFrame, original: pd.DataFrame,
                 rows: list[int], mapping: list[int | None], *, relaxed: bool = False,
                 positions: list[int] | None = None) -> None:
    if len(rows) != len(frame):
        raise TemplatePreservationError("无法定位原表数据行，已停止输出以避免破坏排版")
    if positions is None:
        positions = _column_positions(ws, snap, frame)
    added = len(frame.columns) - len(snap.tpl.col_index)
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
    existing_columns = positions[:len(snap.tpl.columns)]
    row_fills = {row + 1: _row_fill_id(ws, row + 1, existing_columns) for row in rows} if added > 0 else {}
    for row_index, (excel_index, previous_record) in enumerate(zip(rows, original.itertuples(index=False, name=None))):
        for column_index, column in enumerate(positions):
            cell = ws.cell(row=excel_index + 1, column=column)
            value = values[(excel_index + 1, column)]
            if isinstance(cell, MergedCell):
                continue
            if column_index < len(snap.tpl.columns):
                previous = _excel_value(previous_record[column_index])
                if value == previous:
                    continue
                if cell.data_type == "f" and not relaxed:
                    raise TemplatePreservationError(f"本次处理会把公式 {cell.coordinate} 替换成数值，已停止输出以保护原公式")
                cell.value = value
            else:
                _put(cell, value, styles[column_index][row_index % len(snap.data_styles)])
                fill = row_fills.get(excel_index + 1)
                if fill is not None:
                    cell._style.fillId = fill
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


_PERCENT_HINTS = ("率", "占比", "比例", "比重", "百分", "%", "pct", "ratio", "rate", "percent")


def _is_percent_like(series: pd.Series, name: str) -> bool:
    """列名带比例含义且取值都在 [-1, 1]：这是以小数存放的百分比，应显示成 12.5% 而不是 0.125。"""
    if dtype_label(series) not in {"float", "integer"} or not any(hint in name.lower() for hint in _PERCENT_HINTS):
        return False
    values = pd.to_numeric(series, errors="coerce").dropna()
    return bool(len(values)) and bool(values.between(-1, 1).all())


def _column_styles(snap: _Snapshot, df: pd.DataFrame, mapping: list[int | None]) -> list[list[StyleArray | None]]:
    """每一列、每套斑马纹样式。新增的列按语义找“样子最像”的原列借样式：
    比例列借百分比格式的列，其他数值列借非百分比的同类型列，再没有就用左边的列。"""
    tpl = snap.tpl
    by_type: dict[str, int] = {}
    plain_numeric: dict[str, int] = {}
    percent_donor: int | None = None
    for name, idx in zip(tpl.columns, tpl.col_index):
        kind = tpl.dtypes.get(name, "")
        by_type.setdefault(kind, idx + 1)
        is_percent = "%" in snap.col_formats.get(idx + 1, "")
        if is_percent and percent_donor is None:
            percent_donor = idx + 1
        if not is_percent:
            plain_numeric.setdefault(kind, idx + 1)
    result: list[list[StyleArray | None]] = []
    left: int | None = None  # 左边最近一个「原来就有的列」
    for j, sc in enumerate(mapping):
        if sc is not None:
            source = sc
            left = sc
        else:
            series = df.iloc[:, j]
            kind = dtype_label(series)
            if _is_percent_like(series, str(df.columns[j])) and percent_donor is not None:
                source = percent_donor
            elif kind in {"float", "integer"}:
                source = plain_numeric.get(kind) or by_type.get(kind) or left
            else:
                source = by_type.get(kind) or left
        result.append([snap.data_style(k, source) for k in range(len(snap.data_styles))])
    return result


def _style_profile(ws: Worksheet, rows: list[int], max_col: int) -> tuple[list[dict[int, StyleArray]], list[float | None]] | None:
    """用全部数据行的“众数样式”代替只看前两行：个别特殊行（标红、加粗）不会被当成数据行的样子。
    隔行斑马纹分奇偶两套；行高同样取众数。数据行太少时返回 None，沿用前两行样本。"""
    if len(rows) < 3:
        return None
    indexed = list(enumerate(rows))
    if len(indexed) > 4000:
        indexed = indexed[:2000] + indexed[-2000:]
    groups = 2 if _detect_banding(ws, rows) is not None else 1
    variants: list[dict[int, StyleArray]] = []
    heights: list[float | None] = []
    for group in range(groups):
        counters: dict[int, Counter] = {column: Counter() for column in range(1, max_col + 1)}
        representative: dict[tuple[int, tuple], StyleArray] = {}
        height_counter: Counter = Counter()
        for index, row in indexed:
            if groups == 2 and index % 2 != group:
                continue
            for column in range(1, max_col + 1):
                cell = ws._cells.get((row, column))
                style = cell._style if cell is not None and cell._style is not None else StyleArray()
                key = tuple(style)
                counters[column][key] += 1
                representative.setdefault((column, key), style)
            height_counter[ws.row_dimensions[row].height if row in ws.row_dimensions else None] += 1
        if not height_counter:
            return None
        variants.append({column: copy(representative[column, counters[column].most_common(1)[0][0]])
                         for column in counters})
        heights.append(height_counter.most_common(1)[0][0])
    return variants, heights


def _row_fill_id(ws: Worksheet, row: int, columns: list[int]) -> int | None:
    """这一行已有单元格的主导底色。新增列的单元格应与所在行同色（整行标红时新列也是红的）。"""
    counter = Counter(ws._cells[row, column]._style.fillId for column in columns
                      if (row, column) in ws._cells and ws._cells[row, column]._style is not None)
    return counter.most_common(1)[0][0] if counter else None


def _put(cell: Any, value: Any, style: StyleArray | None) -> None:
    if style is not None:
        cell._style = copy(style)
    cell.value = value
    # 新增列借用的样式可能是文本格式 / 常规格式：数字别显示成文本，日期别显示成序列号
    if isinstance(value, (int, float)) and not isinstance(value, bool) and cell.number_format == "@":
        cell.number_format = "General"
    elif isinstance(value, (dt.datetime, dt.date)) and cell.number_format == "General":
        cell.number_format = "yyyy-mm-dd" if not isinstance(value, dt.datetime) or value.time() == dt.time() else "yyyy-mm-dd hh:mm:ss"
    elif isinstance(value, dt.datetime) and value.time() != dt.time() and "h" not in cell.number_format.lower():
        # 借来的是纯日期格式：带时分秒的值不能把时间藏起来
        cell.number_format = "yyyy-mm-dd hh:mm:ss"


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
