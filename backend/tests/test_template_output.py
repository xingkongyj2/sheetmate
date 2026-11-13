"""输出沿用原文件样式：复制原始 Excel，在里面修改 / 删除 / 新增。"""

from __future__ import annotations

import asyncio
import io
from copy import copy
from zipfile import ZipFile

import openpyxl
import pandas as pd
import pytest
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.comments import Comment
from openpyxl.formatting.rule import CellIsRule
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

from sheetmate.services.bootstrap.samples import demo_recipe_body
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workbooks.reader import read_workbook
from sheetmate.engine.tables.workspace import Workspace
from sheetmate.recipe.execution.runner import InputSource, RecipeRunner
from sheetmate.recipe.definition.schema import Recipe
from tests.conftest import read_sse

HEADER_FILL = "FF1F4E78"
ZEBRA_FILL = "FFEAF1FB"


def _styled_workbook(path):
    """一份有讲究的原始文件：合并标题行、深蓝表头、斑马纹、数字格式、列宽、冻结窗格、第二个 sheet。"""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "订单"
    ws.append(["华东区 9 月订单明细"])
    ws.merge_cells("A1:E1")
    ws["A1"].font = Font(bold=True, size=16, color="FF1F4E78")
    ws["A1"].alignment = Alignment(horizontal="center")
    ws.append(["订单号", "客户", "状态", "金额", "日期"])
    thin = Side(style="thin", color="FF999999")
    for cell in ws[2]:
        cell.font = Font(bold=True, color="FFFFFFFF")
        cell.fill = PatternFill("solid", fgColor=HEADER_FILL)
        cell.border = Border(bottom=thin)
    rows = [("A001", "星河", "已完成", 100.5, "2026-09-01"), ("A002", "蓝海", "已取消", 80, "2026-09-02"),
            ("A003", "星河", "已完成", 1200.25, "2026-09-03"), ("A004", "北辰", "已完成", 45, "2026-09-04"),
            ("A005", "蓝海", "已完成", 300, "2026-09-05"), ("A006", "北辰", "已取消", 66, "2026-09-06")]
    for i, (no, cust, status, amount, day) in enumerate(rows):
        ws.append([no, cust, status, amount, pd.Timestamp(day).to_pydatetime()])
        r = 3 + i
        ws.cell(row=r, column=4).number_format = "#,##0.00"
        ws.cell(row=r, column=5).number_format = "yyyy/mm/dd"
        if i % 2 == 1:
            for c in range(1, 6):
                ws.cell(row=r, column=c).fill = PatternFill("solid", fgColor=ZEBRA_FILL)
    for letter, width in zip("ABCDE", (12, 14, 10, 16, 14)):
        ws.column_dimensions[letter].width = width
    ws.freeze_panes = "A3"
    note = wb.create_sheet("说明")
    note["A1"] = "这个 sheet 不应该被动"
    wb.save(path)


def _run(recipe: Recipe, path, out):
    runner = RecipeRunner(recipe, FormulaLibrary(), Workspace(None))
    sources = {i.alias: InputSource(i.alias, path=path, filename=path.name) for i in recipe.inputs}

    async def go():
        return [e async for e in runner.run(sources, output_path=out)]

    asyncio.run(go())
    return runner


def _recipe(steps: list[dict], output: str, name: str, has_expected: bool = False) -> Recipe:
    return Recipe(
        name="订单整理",
        inputs=[{"alias": "订单", "sheet": "订单", "expected_columns": ["订单号", "客户", "状态", "金额", "日期"],
                 "required_columns": ["订单号", "状态", "金额"], "header": {"header_row": 2, "header_rows": 1}}],
        steps=steps,
        output_spec=[{"name": name, "source": output}],
        metadata={"has_expected": has_expected},
    )


def test_in_place_edit_keeps_original_look(tmp_path):
    src = tmp_path / "订单_2026-09.xlsx"
    _styled_workbook(src)
    recipe = _recipe([
        {"id": 1, "name": "去掉取消订单", "formula": "filter_rows", "params": {"table": "订单", "condition": "\"状态\" <> '已取消'"}, "output": "有效订单"},
        {"id": 2, "name": "加含税金额", "formula": "compute_column",
         "params": {"table": "有效订单", "column": "含税金额", "expression": "round(\"金额\" * 1.06, 2)"}, "output": "结果"},
    ], output="结果", name="整理结果")
    out = tmp_path / "out.xlsx"
    runner = _run(recipe, src, out)
    assert runner.report["status"] in {"success", "warning"} and runner.report["output_style"] == "original"

    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["订单", "说明"]  # 没有样例：保持原 sheet 名；没动过的 sheet 原样保留
    assert wb["说明"]["A1"].value == "这个 sheet 不应该被动"
    ws = wb["订单"]
    # 标题行和合并单元格还在
    assert ws["A1"].value == "华东区 9 月订单明细" and "A1:E1" in {str(r) for r in ws.merged_cells.ranges}
    assert ws["A1"].font.size == 16
    # 表头原样：文字、深蓝底、白色粗体
    assert [c.value for c in ws[2]][:5] == ["订单号", "客户", "状态", "金额", "日期"]
    assert ws["A2"].fill.fgColor.rgb == HEADER_FILL and ws["A2"].font.bold
    # 数据：已取消的行被删掉了（4 行），沿用原数据行样式
    assert ws.max_row == 2 + 4 and ws["A3"].value == "A001" and ws["A4"].value == "A003"
    assert ws["D3"].number_format == "#,##0.00" and ws["E3"].number_format == "yyyy/mm/dd"
    assert ws["D3"].value == 100.5 and ws["D4"].value == 1200.25
    # 静态底色跟随原始记录，不能为了交替色覆盖一条记录原本的格式。
    original_book = openpyxl.load_workbook(src)
    original = original_book["订单"]
    assert [copy(ws.cell(row, 1).fill) for row in range(3, 7)] == [copy(original.cell(row, 1).fill) for row in (3, 5, 6, 7)]
    original_book.close()
    # 新增列：表头样式照左边的列，数据照同类型（数字）的列
    assert ws["F2"].value == "含税金额" and ws["F2"].fill.fgColor.rgb == HEADER_FILL and ws["F2"].font.bold
    assert ws["F3"].value == 106.53 and ws["F3"].number_format == "#,##0.00"
    # 列宽、冻结窗格
    assert ws.column_dimensions["A"].width == 12 and ws.column_dimensions["D"].width == 16
    assert ws.freeze_panes == "A3"


def test_removed_and_reordered_columns_follow_their_styles(tmp_path):
    src = tmp_path / "订单.xlsx"
    _styled_workbook(src)
    recipe = _recipe([
        {"id": 1, "name": "调整列", "formula": "select_columns", "params": {"table": "订单", "columns": ["金额", "订单号", "客户"]}, "output": "结果"},
    ], output="结果", name="订单")
    out = tmp_path / "out.xlsx"
    _run(recipe, src, out)
    ws = openpyxl.load_workbook(out)["订单"]
    assert [c.value for c in ws[2]][:3] == ["金额", "订单号", "客户"] and ws["D2"].value is None  # 状态、日期被删掉
    assert ws["A3"].number_format == "#,##0.00" and ws["A3"].value == 100.5  # 金额的格式跟着金额走
    assert ws.column_dimensions["A"].width == 16 and ws.column_dimensions["B"].width == 12  # 列宽跟着列走
    assert ws["A2"].fill.fgColor.rgb == HEADER_FILL


def test_footer_whose_label_column_was_dropped_still_passes_readback(tmp_path):
    """合计行的标签在被去掉的列里：保留下来的页脚不能被回读核对当成多出来的一行数据。"""
    src = tmp_path / "账单.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "账单"
    ws.append(["序号", "订单号", "发货地", "金额"])
    for i in range(1, 6):
        ws.append([i, f"DD{i:03d}", "杭州" if i % 2 else "上海", 10.0 * i])
    ws.append(["合计", "共 5 单", None, 150.0])
    wb.save(src)
    recipe = Recipe(
        name="账单",
        inputs=[{"alias": "账单", "sheet": "账单", "expected_columns": ["序号", "订单号", "发货地", "金额"],
                 "required_columns": ["订单号", "金额"], "header": {"header_row": 1, "header_rows": 1}}],
        steps=[{"id": 1, "name": "去掉序号", "formula": "select_columns",
                "params": {"table": "账单", "columns": ["订单号", "发货地", "金额"]}, "output": "结果"}],
        output_spec=[{"name": "账单", "source": "结果"}],
    )
    runner = _run(recipe, src, tmp_path / "out.xlsx")
    failed = [c for c in runner.report.get("output_verification", []) if c["status"] == "fail"]
    assert not failed, failed
    assert runner.report["status"] in {"success", "warning"}


def test_summary_sheet_is_appended_with_original_header_style(tmp_path):
    src = tmp_path / "订单.xlsx"
    _styled_workbook(src)
    recipe = Recipe(
        name="订单汇总",
        inputs=[{"alias": "订单", "sheet": "订单", "expected_columns": ["订单号", "客户", "状态", "金额", "日期"],
                 "required_columns": ["客户", "金额"], "header": {"header_row": 2, "header_rows": 1}}],
        steps=[
            {"id": 1, "name": "筛选", "formula": "filter_rows", "params": {"table": "订单", "condition": "\"状态\" = '已完成'"}, "output": "已完成订单"},
            {"id": 2, "name": "按客户汇总", "formula": "group_aggregate",
             "params": {"table": "已完成订单", "group_by": ["客户"], "aggregations": [{"column": "金额", "func": "sum", "as": "金额合计"}]}, "output": "客户汇总"},
        ],
        output_spec=[{"name": "订单明细", "source": "已完成订单"}, {"name": "客户汇总", "source": "客户汇总"}],
    )
    out = tmp_path / "out.xlsx"
    _run(recipe, src, out)
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["订单", "说明", "客户汇总"]  # 明细改写原 sheet，汇总追加成新 sheet
    assert wb["订单"]["A1"].value == "华东区 9 月订单明细"
    summary = wb["客户汇总"]
    assert [c.value for c in summary[1]] == ["客户", "金额合计"]
    assert summary["A1"].fill.fgColor.rgb == HEADER_FILL and summary["A1"].font.bold  # 表头样式照原表
    assert summary["B2"].number_format == "#,##0.00"  # 数字列照原表的金额列
    assert {r[0].value: r[1].value for r in summary.iter_rows(min_row=2)} == {"星河": 1300.75, "北辰": 45.0, "蓝海": 300.0}


def test_sheet_named_after_expected_result_when_learned_from_sample(tmp_path):
    src = tmp_path / "订单.xlsx"
    _styled_workbook(src)
    recipe = _recipe([{"id": 1, "name": "筛选", "formula": "filter_rows", "params": {"table": "订单", "condition": "\"金额\" > 100"}, "output": "结果"}],
                     output="结果", name="大额订单", has_expected=True)
    out = tmp_path / "out.xlsx"
    _run(recipe, src, out)
    assert openpyxl.load_workbook(out).sheetnames == ["大额订单", "说明"]  # 有人工样例：按样例的 sheet 名


def test_output_reads_back_to_the_same_data(tmp_path):
    """写出来的文件再读回来，数据和结果一致（标题行不会干扰表头识别）。"""
    src = tmp_path / "订单.xlsx"
    _styled_workbook(src)
    recipe = _recipe([
        {"id": 1, "name": "去掉取消订单", "formula": "filter_rows", "params": {"table": "订单", "condition": "\"状态\" <> '已取消'"}, "output": "结果"},
    ], output="结果", name="订单")
    out = tmp_path / "out.xlsx"
    runner = _run(recipe, src, out)
    expected = runner.collect_outputs()["订单"]
    back = next(s for s in read_workbook(out) if s.name == "订单")
    assert back.header.header_row == 2
    assert back.df["订单号"].tolist() == expected["订单号"].tolist()
    assert back.df["金额"].tolist() == expected["金额"].tolist()


def test_csv_input_falls_back_to_default_style(tmp_path):
    src = tmp_path / "订单.csv"
    src.write_text("订单号,客户,状态,金额,日期\nA001,星河,已完成,100.5,2026-09-01\nA002,蓝海,已取消,80,2026-09-02\n", encoding="utf-8")
    recipe = Recipe(
        name="x", inputs=[{"alias": "订单", "expected_columns": ["订单号", "客户", "状态", "金额", "日期"], "required_columns": ["订单号"]}],
        steps=[{"id": 1, "name": "筛选", "formula": "filter_rows", "params": {"table": "订单", "condition": "\"状态\" = '已完成'"}, "output": "结果"}],
        output_spec=[{"name": "结果", "source": "结果"}],
    )
    out = tmp_path / "out.xlsx"
    runner = _run(recipe, src, out)
    assert runner.report["output_style"] == "default"
    assert openpyxl.load_workbook(out).sheetnames == ["结果"]


def test_demo_bill_keeps_title_row_and_header_style(samples, tmp_path):
    """示例账单：长转宽 + 匹配 + 汇总之后，输出仍然是原来运费明细那张表的样子（标题行、表头加粗、冻结…）。"""
    recipe = Recipe(**demo_recipe_body())
    runner = RecipeRunner(recipe, FormulaLibrary(), Workspace(None))
    out = tmp_path / "bill.xlsx"

    async def go():
        sources = {"运费明细": InputSource("运费明细", path=samples["fees_sep"], filename="运费明细.xlsx"),
                   "店铺归属": InputSource("店铺归属", path=samples["owners"], filename="店铺归属表.xlsx")}
        return [e async for e in runner.run(sources, output_path=out)]

    asyncio.run(go())
    assert runner.report["output_style"] == "original"
    wb = openpyxl.load_workbook(out)
    assert wb.sheetnames == ["明细", "归属汇总"]
    ws = wb["明细"]
    assert ws["A1"].value == "顺达物流 · 2026 年 9 月运费明细"  # 原文件的标题行
    assert [c.value for c in ws[2]][:9] == ["运单号", "下单日期", "店铺名称", "重量(kg)", "运费", "保价费", "包装费", "合计", "归属"]
    assert ws["A2"].font.bold and ws["I2"].font.bold  # 新增的列表头也是加粗的
    assert ws.max_row == 2 + 48
    assert wb["归属汇总"]["A1"].font.bold


def test_rerun_from_step_still_writes_in_original_style(client):
    """断点重跑没有重新读输入文件：靠运行记录里记下的位置恢复，输出依然是原样式。"""
    customers = client.get("/api/customers").json()["customers"]
    demo = next(c for c in customers if c["name"].startswith("顺达物流"))
    detail = client.get(f"/api/customers/{demo['id']}").json()["customer"]
    run_id = [r["run_id"] for r in detail["runs"] if r["kind"] == "replay"][-1]  # 最早的一条是种子数据里的示例运行；其他测试可能在同一个客户下追加过运行
    run = client.get(f"/api/runs/{run_id}").json()["run"]
    events = read_sse(client.post(f"/api/runs/{run_id}/rerun/3", json={"params": run["steps"][2]["params"]}))
    file_event = next(e for e in events if e["type"] == "file")
    wb = openpyxl.load_workbook(io.BytesIO(client.get(file_event["download_url"]).content))
    assert wb["明细"]["A1"].value == "顺达物流 · 2026 年 9 月运费明细"


def _amount_recipe():
    return _recipe([{"id": 1, "name": "修改金额", "formula": "compute_column",
                     "params": {"table": "订单", "column": "金额", "expression": '"金额" + 10'}, "output": "结果"}],
                   output="结果", name="订单")


def test_value_edits_keep_cell_details_formulas_and_other_sheets(tmp_path):
    source = tmp_path / "原表.xlsx"
    _styled_workbook(source)
    workbook = openpyxl.load_workbook(source)
    sheet = workbook["订单"]
    sheet.merge_cells("B3:B4")
    sheet["D5"].fill = PatternFill("solid", fgColor="FFFF0000")
    sheet["D5"].comment = Comment("保留说明", "财务")
    sheet["A5"].hyperlink = "https://example.com/order/A003"
    sheet.row_dimensions[5].height = 37
    sheet.row_dimensions[5].hidden = True
    sheet.column_dimensions["C"].hidden = True
    sheet["F2"] = "公式"
    sheet["F3"] = "=D3*2"
    workbook["说明"]["B1"] = "='订单'!D3"
    validation = DataValidation(type="decimal", operator="greaterThan", formula1=0)
    sheet.add_data_validation(validation)
    validation.add(sheet["D5"])
    sheet.conditional_formatting.add("D3:D8", CellIsRule(operator="greaterThan", formula=[100],
                                                      fill=PatternFill("solid", fgColor="FFFFFF00")))
    workbook.save(source)
    original_bytes = source.read_bytes()
    output = tmp_path / "修改.xlsx"
    runner = _run(_amount_recipe(), source, output)
    assert runner.report["status"] in {"success", "warning"}, runner.report
    result = openpyxl.load_workbook(output)
    edited = result["订单"]
    assert edited["D5"].value == 1210.25
    assert edited["D5"]._style == sheet["D5"]._style
    assert edited["D5"].comment.text == "保留说明"
    assert edited["A5"].hyperlink.target == sheet["A5"].hyperlink.target
    assert edited.row_dimensions[5].height == 37 and edited.row_dimensions[5].hidden
    assert edited.column_dimensions["C"].hidden
    assert {str(merged) for merged in edited.merged_cells.ranges} == {"A1:E1", "B3:B4"}
    assert edited["F3"].value == "=D3*2"
    assert result["说明"]["B1"].value == "='订单'!D3"
    assert edited.data_validations.count == 1
    assert len(edited.conditional_formatting) == 1
    assert source.read_bytes() == original_bytes


def test_value_edits_keep_blank_rows_and_leading_columns(tmp_path):
    source = tmp_path / "空行.xlsx"
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "订单"
    sheet.append([None, "订单号", "金额"])
    sheet.append([None, "A001", 10])
    sheet.append([None, None, None])
    sheet.append([None, "A002", 20])
    sheet.row_dimensions[3].height = 40
    workbook.save(source)
    recipe = Recipe(name="修改", inputs=[{"alias": "订单", "sheet": "订单"}],
                    steps=_amount_recipe().steps, output_spec=[{"name": "订单", "source": "结果"}])
    output = tmp_path / "修改.xlsx"
    runner = _run(recipe, source, output)
    assert runner.report["status"] in {"success", "warning"}, runner.report
    result = openpyxl.load_workbook(output)["订单"]
    assert result["C2"].value == 20 and result["C4"].value == 30
    assert result["A2"].value is None and result["B3"].value is None
    assert result.row_dimensions[3].height == 40


def test_summary_only_does_not_replace_original_sheet(tmp_path):
    source = tmp_path / "原表.xlsx"
    _styled_workbook(source)
    recipe = _recipe([{"id": 1, "name": "汇总", "formula": "group_aggregate",
                       "params": {"table": "订单", "group_by": ["客户"],
                                  "aggregations": [{"column": "金额", "func": "sum", "as": "合计"}]},
                       "output": "汇总"}], output="汇总", name="客户汇总")
    output = tmp_path / "汇总.xlsx"
    runner = _run(recipe, source, output)
    assert runner.report["status"] in {"success", "warning"}, runner.report
    workbook = openpyxl.load_workbook(output)
    assert workbook.sheetnames == ["订单", "说明", "客户汇总"]
    assert list(workbook["订单"].values) == list(openpyxl.load_workbook(source)["订单"].values)
    assert workbook["订单"]["A2"].fill.fgColor.rgb == HEADER_FILL


def test_edits_two_original_sheets_without_duplicating_them(tmp_path):
    source = tmp_path / "原表.xlsx"
    _styled_workbook(source)
    workbook = openpyxl.load_workbook(source)
    workbook.copy_worksheet(workbook["订单"]).title = "订单二"
    workbook.save(source)
    recipe = _amount_recipe()
    recipe.inputs.append(recipe.inputs[0].model_copy(update={"alias": "订单二", "sheet": "订单二"}))
    recipe.steps.append(recipe.steps[0].model_copy(update={"id": 2, "output": "结果二",
                         "params": {"table": "订单二", "column": "金额", "expression": '"金额" + 20'}}))
    recipe.output_spec.append(recipe.output_spec[0].model_copy(update={"name": "订单二", "source": "结果二"}))
    output = tmp_path / "修改.xlsx"
    runner = _run(recipe, source, output)
    assert runner.report["status"] in {"success", "warning"}, runner.report
    result = openpyxl.load_workbook(output)
    assert result.sheetnames == ["订单", "说明", "订单二"]
    assert result["订单"]["D3"].value == 110.5 and result["订单二"]["D3"].value == 120.5
    assert result["订单二"]["A2"].fill.fgColor.rgb == HEADER_FILL


@pytest.mark.parametrize("feature", ["formula", "comment", "unique_style", "hidden_row"])
def test_filtered_rows_preserve_original_details(tmp_path, feature):
    source = tmp_path / "原表.xlsx"
    _styled_workbook(source)
    workbook = openpyxl.load_workbook(source)
    sheet = workbook["订单"]
    if feature == "formula":
        workbook["说明"]["B1"] = "='订单'!D3"
    elif feature == "comment":
        sheet["D5"].comment = Comment("不能丢", "财务")
    elif feature == "unique_style":
        sheet["D5"].fill = PatternFill("solid", fgColor="FFFF0000")
    else:
        sheet.row_dimensions[5].hidden = True
    workbook.save(source)
    original_bytes = source.read_bytes()
    recipe = _recipe([{"id": 1, "name": "筛选", "formula": "filter_rows",
                       "params": {"table": "订单", "condition": '"金额" > 100'}, "output": "结果"}],
                     output="结果", name="订单")
    output = tmp_path / "修改.xlsx"
    runner = _run(recipe, source, output)
    assert runner.report["status"] in {"success", "warning"}, runner.report
    result = openpyxl.load_workbook(output)
    edited = result["订单"]
    assert [edited.cell(row, 1).value for row in range(3, 6)] == ["A001", "A003", "A005"]
    for result_row, source_row in zip(range(3, 6), (3, 5, 7)):
        assert [cell._style for cell in edited[result_row]] == [cell._style for cell in sheet[source_row]]
    if feature == "formula":
        assert result["说明"]["B1"].value == "='订单'!D3"
    elif feature == "comment":
        assert edited["D4"].comment.text == "不能丢"
    elif feature == "unique_style":
        assert edited["D4"].fill.fgColor.rgb == "FFFF0000"
    else:
        assert edited.row_dimensions[4].hidden
    assert source.read_bytes() == original_bytes


def test_existing_excel_table_keeps_metadata_when_column_is_added(tmp_path):
    source = tmp_path / "原表.xlsx"
    _styled_workbook(source)
    workbook = openpyxl.load_workbook(source)
    table = Table(displayName="Orders", ref="A2:E8")
    table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium9", showRowStripes=True)
    workbook["订单"].add_table(table)
    workbook.save(source)
    recipe = _recipe([{"id": 1, "name": "添加金额", "formula": "compute_column",
                       "params": {"table": "订单", "column": "含税金额", "expression": '"金额" * 1.06'},
                       "output": "结果"}], output="结果", name="订单")
    output = tmp_path / "修改.xlsx"
    runner = _run(recipe, source, output)
    assert runner.report["status"] in {"success", "warning"}, runner.report
    result = openpyxl.load_workbook(output)["订单"].tables["Orders"]
    assert result.ref == "A2:F8" and result.autoFilter.ref == "A2:F8"
    assert result.tableStyleInfo.name == "TableStyleMedium9"
    assert [column.name for column in result.tableColumns] == ["订单号", "客户", "状态", "金额", "日期", "含税金额"]


def test_macro_workbook_keeps_vba_and_xlsm_extension(tmp_path):
    original = tmp_path / "原表.xlsx"
    _styled_workbook(original)
    source = tmp_path / "原表.xlsm"
    macro = b"test VBA project payload"
    with ZipFile(original) as archive, ZipFile(source, "w") as target:
        for name in archive.namelist():
            data = archive.read(name)
            if name == "[Content_Types].xml":
                data = data.replace(b"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
                                    b"application/vnd.ms-excel.sheet.macroEnabled.main+xml")
                data = data.replace(b"</Types>", b'<Override PartName="/xl/vbaProject.bin" ContentType="application/vnd.ms-office.vbaProject"/></Types>')
            elif name == "xl/_rels/workbook.xml.rels":
                data = data.replace(b"</Relationships>", b'<Relationship Id="rIdVBA" Type="http://schemas.microsoft.com/office/2006/relationships/vbaProject" Target="vbaProject.bin"/></Relationships>')
            target.writestr(name, data)
        target.writestr("xl/vbaProject.bin", macro)
    original_bytes = source.read_bytes()
    requested = tmp_path / "修改.xlsx"
    runner = _run(_amount_recipe(), source, requested)
    assert runner.report["status"] in {"success", "warning"}, runner.report
    assert runner.output_path == requested.with_suffix(".xlsm") and not requested.exists()
    with ZipFile(runner.output_path) as result:
        assert result.read("xl/vbaProject.bin") == macro
    assert source.read_bytes() == original_bytes


def test_unsupported_content_is_preserved_byte_for_byte_when_only_values_change(tmp_path, monkeypatch):
    monkeypatch.setattr("sheetmate.engine.workbooks.template_writer._ooxml_mode", lambda: "auto")
    source = tmp_path / "原表.xlsx"
    _styled_workbook(source)
    with ZipFile(source, "a") as archive:
        archive.writestr("xl/ctrlProps/ctrlProp1.xml", "<control/>")
    original_bytes = source.read_bytes()
    output = tmp_path / "修改.xlsx"
    runner = _run(_amount_recipe(), source, output)
    # 只改单元格值：直接补丁 XML，控件部件原样保留，不再需要"可能变化"的提示。
    assert runner.report["status"] == "success" and not runner.report["output_warnings"], runner.report
    with ZipFile(output) as result:
        assert result.read("xl/ctrlProps/ctrlProp1.xml") == b"<control/>"
    assert openpyxl.load_workbook(output)["订单"]["D3"].value == 110.5
    assert source.read_bytes() == original_bytes


def _filter_recipe(with_summary: bool) -> Recipe:
    steps = [{"id": 1, "name": "筛选", "formula": "filter_rows",
              "params": {"table": "订单", "condition": "\"状态\" = '已完成'"}, "output": "已完成订单"}]
    outputs = [{"name": "订单", "source": "已完成订单"}]
    if with_summary:
        steps.append({"id": 2, "name": "按客户汇总", "formula": "group_aggregate",
                      "params": {"table": "已完成订单", "group_by": ["客户"],
                                 "aggregations": [{"column": "金额", "func": "sum", "as": "金额合计"}]}, "output": "客户汇总"})
        outputs.append({"name": "客户汇总", "source": "客户汇总"})
    return Recipe(
        name="订单整理",
        inputs=[{"alias": "订单", "sheet": "订单", "expected_columns": ["订单号", "客户", "状态", "金额", "日期"],
                 "required_columns": ["客户", "状态", "金额"], "header": {"header_row": 2, "header_rows": 1}}],
        steps=steps, output_spec=outputs)


def test_filtering_rows_keeps_unsupported_parts_through_ooxml_editing(tmp_path, monkeypatch):
    monkeypatch.setattr("sheetmate.engine.workbooks.template_writer._ooxml_mode", lambda: "auto")
    source = tmp_path / "原表.xlsx"
    _styled_workbook(source)
    with ZipFile(source, "a") as archive:
        archive.writestr("xl/ctrlProps/ctrlProp1.xml", "<control/>")
    original_bytes = source.read_bytes()
    output = tmp_path / "修改.xlsx"
    runner = _run(_filter_recipe(with_summary=False), source, output)
    assert runner.report["status"] == "success" and not runner.report["output_warnings"], runner.report
    with ZipFile(output) as result:
        assert result.read("xl/ctrlProps/ctrlProp1.xml") == b"<control/>"  # 删行也不丢控件
    sheet = openpyxl.load_workbook(output)["订单"]
    statuses = [row[2] for row in sheet.iter_rows(min_row=3, values_only=True) if row[0]]
    assert statuses and set(statuses) == {"已完成"}
    assert source.read_bytes() == original_bytes


def test_unsupported_workbook_content_reports_warning_when_a_summary_sheet_is_appended(tmp_path, monkeypatch):
    monkeypatch.setattr("sheetmate.engine.workbooks.template_writer._ooxml_mode", lambda: "auto")
    source = tmp_path / "原表.xlsx"
    _styled_workbook(source)
    with ZipFile(source, "a") as archive:
        archive.writestr("xl/ctrlProps/ctrlProp1.xml", "<control/>")
    original_bytes = source.read_bytes()
    output = tmp_path / "修改.xlsx"
    runner = _run(_filter_recipe(with_summary=True), source, output)
    # 追加新工作表不在 OOXML 编辑的范围内：退回常规流程并如实提示这些对象可能变化。
    assert runner.report["status"] == "warning" and runner.report["output_warnings"]
    assert "客户汇总" in openpyxl.load_workbook(output).sheetnames and source.read_bytes() == original_bytes
