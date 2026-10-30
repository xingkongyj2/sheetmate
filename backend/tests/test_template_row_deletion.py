from __future__ import annotations

from copy import copy

import openpyxl
import pandas as pd
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import PatternFill

from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workspace import Workspace
from sheetmate.recipe.executor import InputSource, RecipeRunner
from sheetmate.recipe.schema import Recipe


def _source(path):
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "排班"
    sheet.append(["标题"])
    sheet.append(["员工", "班次", "工时"])
    rows = [("甲", "早", 8), ("乙", "晚", 7), ("丙", "早", 8), ("丁", "休", 0)]
    for row in rows:
        sheet.append(row)
    sheet["A1"] = "9 月排班"
    sheet.merge_cells("A1:C1")
    sheet.freeze_panes = "A3"
    sheet.auto_filter.ref = "A2:C6"
    sheet["A3"].fill = PatternFill("solid", fgColor="FFFFCC00")
    sheet["A5"].comment = Comment("保留备注", "测试")
    sheet["A5"].hyperlink = "https://example.com/employee"
    sheet.row_dimensions[5].height = 31
    sheet.row_dimensions[5].hidden = True
    sheet.conditional_formatting.add(
        "C3:C6",
        CellIsRule(operator="greaterThan", formula=[7], fill=PatternFill("solid", fgColor="FF00FF00")),
    )
    note = workbook.create_sheet("引用")
    note["A1"] = "='排班'!C6"
    workbook.save(path)


def _run(source, output):
    recipe = Recipe(
        name="排班筛选",
        inputs=[{
            "alias": "排班",
            "sheet": "排班",
            "expected_columns": ["员工", "班次", "工时"],
            "required_columns": ["员工", "工时"],
            "header": {"header_row": 2, "header_rows": 1},
        }],
        steps=[{
            "id": 1,
            "name": "保留有工时人员",
            "formula": "filter_rows",
            "params": {"table": "排班", "condition": '"工时" <> 7'},
            "output": "结果",
        }],
        output_spec=[{"name": "排班", "source": "结果"}],
    )
    runner = RecipeRunner(recipe, FormulaLibrary(), Workspace(None))

    async def go():
        source_info = InputSource("排班", path=source, filename=source.name)
        return [event async for event in runner.run({"排班": source_info}, output_path=output)]

    import asyncio

    asyncio.run(go())
    return runner


def test_filtered_rows_keep_original_row_objects_and_ranges(tmp_path):
    source = tmp_path / "排班.xlsx"
    output = tmp_path / "结果.xlsx"
    _source(source)
    original_bytes = source.read_bytes()
    runner = _run(source, output)

    assert runner.report["status"] in {"success", "warning"}, runner.report
    assert source.read_bytes() == original_bytes
    workbook = openpyxl.load_workbook(output)
    sheet = workbook["排班"]
    assert [sheet.cell(row, 1).value for row in range(3, 6)] == ["甲", "丙", "丁"]
    assert sheet["A3"].fill.fgColor.rgb == "FFFFCC00"
    assert sheet["A4"].comment.text == "保留备注"
    assert sheet["A4"].hyperlink.target == "https://example.com/employee"
    assert sheet.row_dimensions[4].height == 31 and sheet.row_dimensions[4].hidden
    assert sheet.auto_filter.ref == "A2:C5"
    assert str(next(iter(sheet.conditional_formatting))) == "C3:C5"
    assert workbook["引用"]["A1"].value == "='排班'!C5"
    assert "A1:C1" in {str(rng) for rng in sheet.merged_cells.ranges}
