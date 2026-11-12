"""落盘后验收：回读实际生成的文件，确认它真的包含声明/计划要求的内容。

写入器自己的"成功"不等于文件正确（写入逻辑、样式搬运或第三方库行为都可能出错）。这里不依赖写入器内部状态，
只把磁盘上的文件读回来，与"应有的内容"逐项比对，并如实给出状态：
  pass —— 已核对且一致；fail —— 已核对且不一致；warn —— 因数据量太大等原因没有逐格核对（不冒充通过）。
检查项的字段与 Recipe 校验报告一致，可以直接并入 validation["checks"]。
"""

from __future__ import annotations

import datetime as dt
import math
import random
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd
from openpyxl.cell.cell import MergedCell

from sheetmate.engine.workbooks.reader import ReadError, read_workbook
from sheetmate.engine.workbooks.writer import _excel_value

MAX_VERIFY_CELLS = 200_000
MAX_SAMPLED_CELLS = 3_000_000  # 超过逐格核对上限、但还能承受一次回读时，改为抽样核对
SAMPLE_EDGE_ROWS = 25
SAMPLE_RANDOM_ROWS = 200
MAX_REPORTED = 5
_EXCEL_SUFFIXES = {".xlsx", ".xlsm"}


def _check(rule_id: str, description: str, status: str, message: str = "", actual: Any = None) -> dict[str, Any]:
    return {"rule_id": rule_id, "rule_type": "output_verify", "description": description, "status": status,
            "severity": "error" if status == "fail" else "warning", "expected": None, "actual": actual, "message": message}


def values_equal(expected: Any, actual: Any) -> bool:
    """比较"应写入的值"与"读回的值"：数值允许 Excel 15 位精度内的误差，日期与零点时间等价，空值与空文本等价。"""
    expected = _excel_value(expected)
    if expected is None or actual is None:
        return expected in (None, "") and actual in (None, "")
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if isinstance(expected, (int, float)):
        return isinstance(actual, (int, float)) and math.isclose(expected, actual, rel_tol=1e-9, abs_tol=1e-9)
    if isinstance(expected, dt.datetime):
        return isinstance(actual, dt.datetime) and expected == actual
    if isinstance(expected, dt.date):
        return actual == expected or (isinstance(actual, dt.datetime) and actual == dt.datetime.combine(expected, dt.time()))
    return str(expected) == str(actual)


def _sample_offsets(count: int) -> list[int]:
    """首尾各若干行加固定种子的随机行：结果可复现，覆盖写入最容易出错的边界。"""
    if count <= SAMPLE_EDGE_ROWS * 2 + SAMPLE_RANDOM_ROWS:
        return list(range(count))
    chosen = set(range(SAMPLE_EDGE_ROWS)) | set(range(count - SAMPLE_EDGE_ROWS, count))
    chosen.update(random.Random(count).sample(range(count), SAMPLE_RANDOM_ROWS))
    return sorted(chosen)


def _describe(mismatches: list[str], total: int) -> str:
    shown = "；".join(mismatches[:MAX_REPORTED])
    return shown + (f"；另有 {total - MAX_REPORTED} 处" if total > MAX_REPORTED else "")


def file_is_openable(path: Path) -> dict[str, Any]:
    """交付的 Excel 文件必须能被重新打开（脚本直接生成的文件也走这个检查）。"""
    if path.suffix.lower() not in _EXCEL_SUFFIXES:
        return _check("auto_file_openable", f"「{path.name}」可打开", "pass")
    try:
        workbook = openpyxl.load_workbook(path, read_only=True)
        workbook.close()
    except Exception as exc:
        return _check("auto_file_openable", f"「{path.name}」可打开", "fail", f"生成的文件无法重新打开：{exc}")
    return _check("auto_file_openable", f"「{path.name}」可打开", "pass")


def verify_template_output(path: Path, plans: list[Any], written: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """回读沿用原格式写出的文件：目标工作表的数据与计划一致，未涉及的工作表保持原样。"""
    try:
        workbook = openpyxl.load_workbook(path, rich_text=True)
    except Exception as exc:
        return [_check("verify_openable", "输出文件可重新打开", "fail", f"生成的文件无法重新打开：{exc}")]
    checks: list[dict[str, Any]] = []
    try:
        touched = {item["sheet"] for item in written}
        raw_cache: dict[Path, list[Any]] = {}  # 多个工作表共享同一次文件解析，而不是每个工作表各读一遍
        for plan, info in zip(plans, written):
            checks.append(_verify_sheet(path, workbook, plan, info["sheet"], raw_cache))
        checks.append(_verify_protected_sheets(workbook, plans, touched))
    finally:
        workbook.close()
    return [item for item in checks if item is not None]


def _verify_sheet(path: Path, workbook: openpyxl.Workbook, plan: Any, title: str,
                  raw_cache: dict[Path, list[Any]] | None = None) -> dict[str, Any]:
    rule, description = f"verify_sheet_{title}", f"「{title}」的数据与计划一致"
    frame: pd.DataFrame = plan.df
    if title not in workbook.sheetnames:
        return _check(rule, description, "fail", f"输出文件里没有工作表「{title}」")
    ws = workbook[title]
    sampled = frame.size > MAX_VERIFY_CELLS
    if frame.size > MAX_SAMPLED_CELLS:
        return _check(rule, description, "warn", f"「{title}」数据量较大（{frame.size} 个单元格），只确认了文件可打开，没有逐格核对")
    hints = None
    if plan.edit and plan.template is not None:
        hints = {title: {"header_row": plan.template.header_row, "header_rows": plan.template.header_rows}}
    try:
        sheet = next(item for item in read_workbook(path, header_hints=hints, include_hidden=True, sheet_names=frozenset({title}),
                                      raw_cache=raw_cache)
                     if item.name == title)
    except (ReadError, StopIteration) as exc:
        return _check(rule, description, "fail", f"无法按表头读回「{title}」：{exc}")
    expected_columns = [str(column) for column in frame.columns]
    actual_columns = [str(column) for column in sheet.df.columns]
    # 沿用原表头时，字段映射会让计划里的列名（逻辑名）与文件里保留的原表头不同；原有列按位置对应，
    # 只有新增的列表头是我们写的，必须与计划一致。追加的新 sheet 表头全部由我们写入。
    known = set(plan.template.columns) if (plan.edit and plan.template is not None) else set()
    if len(actual_columns) != len(expected_columns) or any(
            name not in known and name != actual for name, actual in zip(expected_columns, actual_columns)):
        return _check(rule, description, "fail", f"「{title}」的列与计划不一致：应为 {expected_columns}，实际 {actual_columns}",
                      actual=actual_columns)
    rows, columns = list(sheet.header.row_index or []), sheet.header.col_index or []
    footers = list(sheet.header.footer_rows or [])
    if len(rows) != len(frame) and len(rows) + len(footers) == len(frame):
        # 计划末尾的“合计”行写出后，读取层会把它归为页脚而不是记录；它仍是计划里的一行，按位置一起核对。
        rows += footers
    elif len(sheet.df) != len(frame):
        return _check(rule, description, "fail", f"「{title}」应有 {len(frame)} 行数据，实际读回 {len(sheet.df)} 行", actual=len(sheet.df))
    if len(rows) != len(frame) or len(columns) != len(expected_columns):
        return _check(rule, description, "warn", f"「{title}」无法定位数据单元格，没有逐格核对")
    mismatches: list[str] = []
    total = 0
    check_offsets = _sample_offsets(len(rows)) if sampled else range(len(rows))
    values = frame.to_numpy(dtype=object)
    for row_offset in check_offsets:
        excel_row = rows[row_offset]
        for column_offset, excel_column in enumerate(columns):
            cell = ws.cell(excel_row + 1, excel_column + 1)
            if isinstance(cell, MergedCell) or cell.data_type == "f":
                continue  # 合并区域的非锚点格不存值；公式的结果要等 Excel 重算，都不能当作值比较
            if not values_equal(values[row_offset, column_offset], cell.value):
                total += 1
                if len(mismatches) < MAX_REPORTED:
                    mismatches.append(f"{cell.coordinate} 应为 {_excel_value(values[row_offset, column_offset])!r}，实际 {cell.value!r}")
    if total:
        scope = f"抽样的 {len(check_offsets)} 行里" if sampled else ""
        return _check(rule, description, "fail", f"「{title}」{scope}有 {total} 个单元格与计划不一致：{_describe(mismatches, total)}", actual=total)
    if sampled:
        # 抽样一致只能说明没发现问题，不冒充逐格通过。
        return _check(rule, description, "warn",
                      f"「{title}」数据量较大（{frame.size} 个单元格），已核对列、行数并抽样核对 {len(check_offsets)} 行，没有逐格核对")
    return _check(rule, description, "pass")


def _verify_protected_sheets(workbook: openpyxl.Workbook, plans: list[Any], touched: set[str]) -> dict[str, Any] | None:
    """没有被计划涉及的工作表必须和原文件完全一致（值、公式、合并区域）。"""
    templates = {plan.template.path for plan in plans if plan.template is not None}
    if len(templates) != 1:
        return None
    if all(name in touched for name in workbook.sheetnames):
        return None  # 所有工作表都在计划内，没有“未涉及的工作表”需要核对，也就不必再加载一遍原文件
    source_path = next(iter(templates))
    rule, description = "verify_protected_sheets", "未涉及的工作表保持原样"
    try:
        source = openpyxl.load_workbook(source_path, rich_text=True)
    except Exception:
        return _check(rule, description, "warn", "无法重新打开原文件副本，没有核对未涉及的工作表")
    try:
        edited_origins = {plan.template.sheet for plan in plans if plan.template is not None and plan.edit}
        edited_names = edited_origins | touched
        problems: list[str] = []
        for name in source.sheetnames:
            if name in edited_origins:
                continue
            if name not in workbook.sheetnames:
                problems.append(f"工作表「{name}」在输出里丢失")
                continue
            before, after = source[name], workbook[name]
            if before.max_row * max(before.max_column, 1) > MAX_VERIFY_CELLS:
                continue
            if {str(item) for item in before.merged_cells.ranges} != {str(item) for item in after.merged_cells.ranges}:
                problems.append(f"工作表「{name}」的合并区域发生了变化")
            for row in before.iter_rows():
                for cell in row:
                    other = after.cell(cell.row, cell.column)
                    if isinstance(cell, MergedCell) or isinstance(other, MergedCell):
                        continue
                    if cell.data_type == "f" and any(edited in str(cell.value) for edited in edited_names):
                        continue  # 指向被编辑工作表的公式，引用会随删行/排序被有意调整
                    if cell.value != other.value and not values_equal(cell.value, other.value):
                        problems.append(f"「{name}」!{cell.coordinate} 从 {cell.value!r} 变成了 {other.value!r}")
                        break
                if len(problems) >= MAX_REPORTED:
                    break
        if problems:
            return _check(rule, description, "fail", "未涉及的工作表被改动：" + "；".join(problems[:MAX_REPORTED]), actual=len(problems))
        return _check(rule, description, "pass")
    finally:
        source.close()


def verify_created_workbook(path: Path, spec: Any) -> list[str]:
    """回读新建的工作簿，返回与声明不一致之处（空列表表示一致）。只做确定性的比对，公式不计算。"""
    from sheetmate.engine.workbooks.builder import DateCell, FormulaCell

    problems: list[str] = []
    try:
        workbook = openpyxl.load_workbook(path, read_only=True)
    except Exception as exc:
        return [f"生成的文件无法重新打开：{exc}"]
    try:
        names = set(workbook.sheetnames)
        for sheet in spec.sheets:
            if sheet.name not in names:
                problems.append(f"工作表「{sheet.name}」没有写入文件")
                continue
            if sum(len(row) for row in sheet.rows) > MAX_VERIFY_CELLS:
                continue
            ws = workbook[sheet.name]
            actual = [list(row) for row in ws.iter_rows(values_only=False)]
            for row_index, row in enumerate(sheet.rows):
                for column_index, expected in enumerate(row):
                    if expected is None:
                        continue
                    cell = actual[row_index][column_index] if row_index < len(actual) and column_index < len(actual[row_index]) else None
                    coordinate = f"「{sheet.name}」!{openpyxl.utils.get_column_letter(column_index + 1)}{row_index + 1}"
                    value = None if cell is None else cell.value
                    if isinstance(expected, FormulaCell):
                        if not (isinstance(value, str) and value.startswith("=")):
                            problems.append(f"{coordinate} 应是公式，实际是 {value!r}")
                    elif isinstance(expected, DateCell):
                        if not isinstance(value, (dt.date, dt.datetime)):
                            problems.append(f"{coordinate} 应是日期，实际是 {value!r}")
                    elif isinstance(expected, str):
                        if value != expected:
                            problems.append(f"{coordinate} 的文本应为 {expected!r}，实际 {value!r}")
                    elif not values_equal(expected, value):
                        problems.append(f"{coordinate} 应为 {expected!r}，实际 {value!r}")
                    if len(problems) >= MAX_REPORTED:
                        return problems
    finally:
        workbook.close()
    return problems
