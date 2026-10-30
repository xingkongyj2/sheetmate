"""按原文件样式写出结果：复制一份原始 Excel，在里面修改 / 删除 / 新增。

财务同事要的是「输入什么样，输出还是什么样」，而不是一张格式全新的表。所以输出不是新建一个空白 Excel，
而是把原始文件复制一份，然后：

- 标题行、合并单元格、表头样式、列宽、隐藏列、冻结窗格、数字格式、斑马纹……都沿用原文件
- 被处理的那张 sheet：表头以下的数据区整体换成结果（行数变多变少都行），每一行沿用原来数据行的样式
- 新增的列：表头和数据样式照着相邻列（或同类型的列）来；删掉的列直接去掉；列顺序变了就跟着变
- 结果里多出来的 sheet（例如汇总表）：追加到这份副本里，表头和数据样式照着原表
- 原文件里没被动过的 sheet 原样保留

只支持 .xlsx / .xlsm 的原文件（openpyxl 能读写的格式）；.xls、.csv 或者原文件打不开时返回 None，由调用方退回默认样式。
已知限制：openpyxl 不保留图表、数据透视表、宏；这些在输出里会丢掉（单元格数据和格式不受影响）。
"""

from __future__ import annotations

import datetime as dt
from copy import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd
from openpyxl.styles.cell_style import StyleArray
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.filters import AutoFilter
from openpyxl.worksheet.worksheet import Worksheet

from sheetmate.engine.profile import dtype_label
from sheetmate.engine.writer import _display_width, _excel_value, safe_sheet_name

TEMPLATE_SUFFIXES = {".xlsx", ".xlsm"}


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
    return all(tuple(a[c]) == tuple(b.get(c, ())) for c in a)


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
    """复制原文件、改写、保存到 path。返回每个 sheet 的 {sheet, rows, columns}；不能按原样式写出时返回 None。"""
    base = next((p for p in plans if p.edit and p.template and p.template.usable()), None)
    if base is None:
        return None
    tpl = base.template
    assert tpl is not None
    try:
        wb = openpyxl.load_workbook(tpl.path)  # 不用 data_only：没动过的 sheet 里的公式要原样保留
    except Exception:
        return None
    if tpl.sheet not in wb.sheetnames:
        return None
    ws = wb[tpl.sheet]
    snap = _Snapshot.capture(ws, tpl)

    written: list[dict[str, Any]] = []
    used = set(wb.sheetnames)
    for plan in plans:
        if plan is base:
            title = _unique_title(plan.name, used - {ws.title}) if plan.name != ws.title else ws.title
            used.discard(ws.title)
            used.add(title)
            _rewrite_sheet(ws, snap, plan.df)
            ws.title = title
            written.append(_info(title, plan.df))
        else:
            title = safe_sheet_name(plan.name, used)
            _append_sheet(wb, title, snap, plan.df)
            written.append(_info(title, plan.df))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return written


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


def _rewrite_sheet(ws: Worksheet, snap: _Snapshot, df: pd.DataFrame) -> None:
    tpl = snap.tpl
    mapping = _column_map(snap, df)
    columns = [str(c) for c in df.columns]
    same_columns = columns == tpl.columns
    header_row, first_data = tpl.header_row, tpl.header_row + 1

    # 1. 数据区里的合并单元格先拆掉（否则删行会留下残骸），然后整块删除
    for rng in list(ws.merged_cells.ranges):
        if rng.min_row >= first_data:
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

    _fix_table_objects(ws, header_row, max(len(df.columns), 1), first_data + max(len(df) - 1, 0))


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


def _fix_table_objects(ws: Worksheet, header_row: int, width: int, last_row: int) -> None:
    """原表如果是「Excel 表格」对象或有筛选，范围要跟着新数据区走，否则 Excel 打开会报修复。"""
    ref = f"A{header_row}:{get_column_letter(width)}{max(last_row, header_row + 1)}"
    for name in list(ws.tables):
        table = ws.tables[name]
        try:
            table.ref = ref
            table.autoFilter = AutoFilter(ref=ref) if table.autoFilter is not None else None
            table.tableColumns = []
            table._initialise_columns()
            for column, cell in zip(table.tableColumns, ws[header_row]):
                column.name = str(cell.value if cell.value is not None else column.name)
        except Exception:
            del ws.tables[name]  # 实在对不上就去掉表格对象，单元格样式不受影响
    if ws.auto_filter is not None and ws.auto_filter.ref:
        ws.auto_filter.ref = ref


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
