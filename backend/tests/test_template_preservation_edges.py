from __future__ import annotations

import asyncio
from pathlib import Path

import openpyxl
import pandas as pd
import pytest
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workspace import Workspace
from sheetmate.recipe.executor import InputSource, RecipeRunner
from sheetmate.recipe.schema import Recipe


def _run_filter(source: Path, output: Path, *, condition: str = '"工时" > 0') -> RecipeRunner:
    recipe = Recipe(
        name="排班边界",
        inputs=[{"alias": "排班", "sheet": "明细", "expected_columns": ["编号", "名称", "工时", "备注"],
                 "required_columns": ["编号", "工时"], "header": {"header_row": 2, "header_rows": 1}}],
        steps=[{"id": 1, "name": "筛选", "formula": "filter_rows",
                "params": {"table": "排班", "condition": condition}, "output": "结果"}],
        output_spec=[{"name": "明细", "source": "结果"}],
    )
    runner = RecipeRunner(recipe, FormulaLibrary(), Workspace(None))

    async def go():
        return [event async for event in runner.run(
            {"排班": InputSource("排班", path=source, filename=source.name)}, output_path=output,
        )]

    asyncio.run(go())
    return runner


def _safe_failure(runner: RecipeRunner, source: Path, output: Path, before: bytes) -> None:
    assert runner.report["status"] == "failed", runner.report
    assert "TemplatePreservationError" in runner.report["error"]
    assert not output.exists()
    assert source.read_bytes() == before


def _horizontal_merges(path: Path) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "明细"
    sheet.append(["标题"])
    sheet.merge_cells("A1:D1")
    sheet.append(["编号", "名称", "工时", "备注"])
    rows = [("A", "甲", 8, "早班"), ("B", "乙", 0, "休息"), ("C", "丙", 7, "晚班"), ("D", "丁", 6, "早班")]
    for row_no, row in enumerate(rows, start=3):
        sheet.append(row)
        sheet.merge_cells(start_row=row_no, start_column=1, end_row=row_no, end_column=2)
        sheet.cell(row_no, 1).comment = Comment(f"备注-{row[0]}", "测试")
        sheet.row_dimensions[row_no].height = 20 + row_no
    sheet.freeze_panes = "A3"
    workbook.create_sheet("不受影响")["A1"] = "保持原样"
    workbook["不受影响"]["A1"].font = Font(bold=True, color="FFFF0000")
    workbook.save(path)


def test_delete_middle_row_preserves_horizontal_merges_styles_comments_and_other_sheet(tmp_path):
    source = tmp_path / "横向合并.xlsx"
    output = tmp_path / "结果.xlsx"
    _horizontal_merges(source)
    before = source.read_bytes()
    runner = _run_filter(source, output)
    if runner.report["status"] == "failed":
        _safe_failure(runner, source, output, before)
        return

    workbook = openpyxl.load_workbook(output)
    sheet = workbook["明细"]
    assert [sheet.cell(row, 1).value for row in range(3, 6)] == ["A", "C", "D"]
    assert {str(rng) for rng in sheet.merged_cells.ranges} >= {"A1:D1", "A3:B3", "A4:B4", "A5:B5"}
    assert [sheet.cell(row, 1).comment.text for row in range(3, 6)] == ["备注-A", "备注-C", "备注-D"]
    assert [sheet.row_dimensions[row].height for row in range(3, 6)] == [23, 25, 26]
    assert workbook["不受影响"]["A1"].value == "保持原样"
    assert workbook["不受影响"]["A1"].font.color.rgb == "FFFF0000"
    assert source.read_bytes() == before


def _vertical_merge(path: Path) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "明细"
    sheet.append(["标题"])
    sheet.append(["编号", "名称", "工时", "备注"])
    sheet.append(["A", "甲", 8, "组一"])
    sheet.append([None, "乙", 0, "组一"])
    sheet.append(["B", "丙", 7, "组二"])
    sheet.merge_cells("A3:A4")
    sheet["A3"].fill = PatternFill("solid", fgColor="FFFFFF00")
    sheet.row_dimensions[3].height = 31
    sheet.row_dimensions[4].height = 32
    workbook.save(path)


def test_delete_first_or_internal_row_of_vertical_merge_keeps_anchor_or_fails_safely(tmp_path):
    for condition, label in [('"工时" <> 8', "首行"), ('"工时" <> 0', "内部")]:
        source = tmp_path / f"纵向合并-{label}.xlsx"
        output = tmp_path / f"纵向结果-{label}.xlsx"
        _vertical_merge(source)
        before = source.read_bytes()
        runner = _run_filter(source, output, condition=condition)
        if runner.report["status"] == "failed":
            _safe_failure(runner, source, output, before)
            continue
        workbook = openpyxl.load_workbook(output)
        sheet = workbook["明细"]
        assert sheet["A3"].value in {"A", "B"}
        if sheet["A3"].value == "A":
            assert sheet["A3"].fill.fgColor.rgb == "FFFFFF00"
        assert source.read_bytes() == before


def _references_and_ranges(path: Path) -> None:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "明细"
    sheet.append(["标题"])
    sheet.append(["编号", "名称", "工时", "备注"])
    for row in [("A", "甲", 8, "早"), ("B", "乙", 0, "休"), ("C", "丙", 7, "晚"), ("D", "丁", 6, "早")]:
        sheet.append(row)
    sheet.auto_filter.ref = "A2:D6"
    sheet.print_area = "A1:D6"
    sheet.conditional_formatting.add("C3:C6", CellIsRule(operator="greaterThan", formula=[0],
                                                           fill=PatternFill("solid", fgColor="FF00FF00")))
    validation = DataValidation(type="whole", operator="greaterThanOrEqual", formula1=0)
    validation.add("C3:C6")
    sheet.add_data_validation(validation)
    notes = workbook.create_sheet("引用")
    notes["A1"] = "='明细'!C5"
    notes["B1"] = "不受影响"
    notes["B1"].font = Font(italic=True, color="FF0000FF")
    workbook.save(path)


def test_delete_middle_row_updates_cross_sheet_reference_and_ranges(tmp_path):
    source = tmp_path / "引用与范围.xlsx"
    output = tmp_path / "结果.xlsx"
    _references_and_ranges(source)
    before = source.read_bytes()
    runner = _run_filter(source, output)
    if runner.report["status"] == "failed":
        _safe_failure(runner, source, output, before)
        return

    workbook = openpyxl.load_workbook(output)
    sheet = workbook["明细"]
    assert sheet.auto_filter.ref == "A2:D5"
    assert str(next(iter(sheet.conditional_formatting)).sqref) == "C3:C5"
    assert str(sheet.data_validations.dataValidation[0].sqref) == "C3:C5"
    assert str(sheet.print_area) == "'明细'!$A$1:$D$5"
    assert str(workbook["引用"]["A1"].value).lstrip("=") == "'明细'!C4"
    assert workbook["引用"]["B1"].font.italic and workbook["引用"]["B1"].font.color.rgb == "FF0000FF"
    assert source.read_bytes() == before
