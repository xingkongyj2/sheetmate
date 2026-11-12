"""Regenerate the synthetic BIFF fixtures with optional xlwt 1.3.0.

xlwt is used only for fixture authoring; it is not a runtime or test dependency.
Run this script from any directory after installing xlwt into a temporary environment.
These files contain synthetic data only.
"""

from datetime import datetime
from pathlib import Path

import xlwt


ROOT = Path(__file__).parent


def values(filename, *, dates_1904=False):
    book = xlwt.Workbook()
    book.dates_1904 = dates_1904
    sheet = book.add_sheet("Data")
    sheet.write(0, 0, "kind")
    sheet.write(0, 1, "value")
    rows = [
        ("literal_formula", "=1+1", None),
        ("literal_error", "#N/A", None),
        ("number", 123.5, None),
        ("boolean", True, None),
        ("true_error", None, None),
        ("date", datetime(2026, 11, 12, 9, 30), "yyyy-mm-dd hh:mm"),
        ("time", 0.5, "hh:mm"),
        ("duration", 2.5, "[h]:mm"),
        ("richtext", None, None),
        ("blank", None, "0.00"),
    ]
    for row, (kind, value, number_format) in enumerate(rows, 1):
        sheet.write(row, 0, kind)
        if kind == "true_error":
            sheet.row(row).set_cell_error(1, 0x07)
        elif kind == "richtext":
            red = xlwt.Font()
            red.name, red.bold, red.colour_index = "Arial", True, 2
            sheet.write_rich_text(row, 1, ["plain 😀 ", ("red bold", red)])
        else:
            style = xlwt.easyxf(num_format_str=number_format) if number_format else xlwt.Style.default_style
            sheet.write(row, 1, value, style)
    book.save(str(ROOT / filename))


def sparse():
    book = xlwt.Workbook()
    sheet = book.add_sheet("Sparse")
    sheet.write(0, 0, "start")
    sheet.write(1, 1, None, xlwt.easyxf("pattern: pattern solid, fore_colour yellow;"))
    sheet.write(1000, 255, "end")
    book.save(str(ROOT / "conversion_sparse.xls"))


def layout():
    book = xlwt.Workbook()
    sheet = book.add_sheet("Data")
    for column, heading in enumerate(["group", "name", "amount"]):
        sheet.write(0, column, heading, xlwt.easyxf("font: bold on;"))
    sheet.write_merge(1, 2, 0, 0, "组一", xlwt.easyxf("borders: left thin, right thin, top thin, bottom thin;"))
    sheet.write(1, 1, "Alice")
    sheet.write(2, 1, "Bob")
    sheet.write(1, 2, 10)
    sheet.write(2, 2, 20)
    sheet.row(0).height, sheet.row(0).height_mismatch = 600, True
    sheet.col(0).width = 20 * 256
    sheet.col(1).width = 18 * 256
    sheet.col(3).hidden = True
    sheet.row(4).hidden = True
    sheet.panes_frozen = True
    sheet.horz_split_pos, sheet.vert_split_pos = 1, 1
    sheet.row_default_height = 360
    sheet.row_default_height_mismatch = True
    visible = book.add_sheet("Visible")
    visible.write(0, 0, "name")
    visible.write(1, 0, "visible")
    hidden = book.add_sheet("Hidden")
    hidden.write(0, 0, "name")
    hidden.write(1, 0, "hidden")
    hidden.visibility = 1
    book.save(str(ROOT / "conversion_layout.xls"))


def blank():
    book = xlwt.Workbook()
    book.add_sheet("Blank").write(0, 0, None, xlwt.easyxf("pattern: pattern solid, fore_colour yellow;"))
    book.save(str(ROOT / "conversion_blank.xls"))


def control_text():
    book = xlwt.Workbook()
    book.add_sheet("Data").write(0, 0, "text\x0bcontrol")
    book.save(str(ROOT / "conversion_control.xls"))


if __name__ == "__main__":
    values("conversion_values.xls")
    values("conversion_1904.xls", dates_1904=True)
    sparse()
    layout()
    blank()
    control_text()
