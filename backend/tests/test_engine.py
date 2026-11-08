"""引擎层：读取、画像、公式、DuckDB 执行与安全边界。"""

from __future__ import annotations

import pandas as pd
import pytest

from sheetmate.engine.formulas import FormulaError, FormulaLibrary
from sheetmate.engine.profile import profile_workbook
from sheetmate.engine.reader import read_workbook
from sheetmate.engine.runner import apply_formula
from sheetmate.engine.sqlutil import UnsafeSQLError, assert_safe_expression, assert_single_select
from sheetmate.engine.workspace import Workspace, WorkspaceError


@pytest.fixture
def ws():
    w = Workspace(None)
    yield w
    w.close()


def test_reader_detects_header_below_title(samples):
    sheet = read_workbook(samples["fees_sep"])[0]
    assert sheet.header.header_row == 2
    assert "运费明细" in sheet.header.title
    assert list(sheet.df.columns) == ["运单号", "下单日期", "店铺名称", "费用类型", "金额", "重量(kg)"]
    assert str(sheet.df["运单号"].iloc[0]).startswith("SF")  # 编号保持文本
    assert pd.api.types.is_datetime64_any_dtype(sheet.df["下单日期"])


def test_profile_flags_duplicate_keys(samples):
    profile = profile_workbook(read_workbook(samples["fees_sep"]))
    dup = profile["sheets"][0]["keys"]["duplicated"]
    assert any(d["column"] == "运单号" for d in dup)


def test_leading_zero_ids_stay_text(tmp_path, ws):
    path = tmp_path / "ids.csv"
    path.write_text("编号,金额\n00123,1.5\n00456,2\n", encoding="utf-8")
    df = read_workbook(path)[0].df
    assert df["编号"].tolist() == ["00123", "00456"]
    assert df["金额"].sum() == pytest.approx(3.5)


def test_pivot_wide_with_fixed_values(samples, ws):
    ws.put_dataframe("费用", read_workbook(samples["fees_sep"])[0].df, source="uploaded")
    out = apply_formula(ws, FormulaLibrary(), "pivot_wide", {
        "table": "费用", "index": ["运单号"], "pivot_column": "费用类型", "value_column": "金额",
        "fill_value": 0, "values": ["运费", "保价费", "包装费"], "carry_columns": ["店铺名称"],
    }, "宽表")
    df = ws.df("宽表")
    assert out.rows_out == df["运单号"].nunique() == len(df)
    assert list(df.columns) == ["运单号", "店铺名称", "运费", "保价费", "包装费"]
    assert df[["运费", "保价费", "包装费"]].sum().sum() == pytest.approx(ws.df("费用")["金额"].sum())


def test_lookup_reports_match_rate_and_fills(samples, ws):
    ws.put_dataframe("订单", pd.DataFrame({"店铺": ["A", "B", "C", " a "], "金额": [1, 2, 3, 4]}), source="uploaded")
    ws.put_dataframe("归属", pd.DataFrame({"店铺": ["A", "B"], "归属": ["甲", "乙"]}), source="uploaded")
    out = apply_formula(ws, FormulaLibrary(), "lookup", {
        "table": "订单", "lookup_table": "归属", "key": "店铺", "columns": ["归属"], "missing_fill": {"归属": "新食研"},
    }, "结果")
    assert ws.df("结果")["归属"].tolist() == ["甲", "乙", "新食研", "甲"]  # " a " 标准化后匹配 A
    assert out.metrics["match_rate"] == 0.75


def test_lookup_blocks_duplicate_keys_when_asked(ws):
    ws.put_dataframe("订单", pd.DataFrame({"k": ["x"]}), source="uploaded")
    ws.put_dataframe("对照", pd.DataFrame({"k": ["x", "x"], "v": [1, 2]}), source="uploaded")
    with pytest.raises(FormulaError, match="重复值"):
        apply_formula(ws, FormulaLibrary(), "lookup", {"table": "订单", "lookup_table": "对照", "key": "k", "columns": ["v"],
                                                       "duplicates": "error"}, "r")


def test_group_aggregate_keeps_first_seen_order(ws):
    ws.put_dataframe("t", pd.DataFrame({"客户": ["乙", "甲", "乙"], "金额": [1.0, 2.0, 3.0]}), source="uploaded")
    apply_formula(ws, FormulaLibrary(), "group_aggregate", {
        "table": "t", "group_by": ["客户"], "aggregations": [{"column": "金额", "func": "sum"}, {"column": "*", "func": "count", "as": "笔数"}],
    }, "汇总")
    df = ws.df("汇总")
    assert df["客户"].tolist() == ["乙", "甲"]
    assert df["金额"].tolist() == [4.0, 2.0]
    assert df["笔数"].tolist() == [2, 1]


def test_unknown_column_error_suggests_alternative(ws):
    ws.put_dataframe("t", pd.DataFrame({"店铺名称": ["a"]}), source="uploaded")
    with pytest.raises(FormulaError, match="店铺名称"):
        apply_formula(ws, FormulaLibrary(), "select_columns", {"table": "t", "columns": ["店铺"]}, "o")


def test_sql_safety():
    with pytest.raises(UnsafeSQLError):
        assert_safe_expression("1=1; DROP TABLE t")
    with pytest.raises(UnsafeSQLError):
        assert_single_select("DELETE FROM t")
    with pytest.raises(UnsafeSQLError):
        assert_single_select("SELECT 1; SELECT 2")
    assert assert_safe_expression("\"备注\" <> 'DROP 不算关键字'")


def test_workspace_cannot_read_local_files(ws):
    with pytest.raises((WorkspaceError, UnsafeSQLError)):
        ws.query("SELECT * FROM read_csv('/etc/hosts')")


def test_workspace_recovers_after_internal_error(tmp_path):
    w = Workspace(tmp_path / "w.duckdb")
    w.put_dataframe("t", pd.DataFrame({"k": ["a", "a", "b"], "v": [1.5, 2.5, 3.5]}), source="uploaded")
    try:
        w.create_table_as("o", 'SELECT * FROM "t" QUALIFY ROW_NUMBER() OVER (PARTITION BY k ORDER BY rowid) = 1 ORDER BY rowid')
    except WorkspaceError:
        pass
    assert w.row_count("t") == 3  # 工作区仍然可用
    w.close()


def test_all_builtin_formulas_have_valid_specs():
    lib = FormulaLibrary()
    assert len(lib.ids()) >= 20
    text = lib.catalog_text()
    for fid in ("pivot_wide", "lookup", "group_aggregate", "custom_sql"):
        assert f"`{fid}`" in text


def test_db_migration_adds_missing_columns(tmp_path):
    """旧版本的数据库缺少新列时，启动会自动补上，旧数据保留并得到默认值。"""
    import sqlite3

    from sqlalchemy import create_engine

    from sheetmate.db.database import _add_missing_columns

    path = tmp_path / "old.db"
    with sqlite3.connect(path) as raw:
        raw.execute("CREATE TABLE customers (id VARCHAR(32) PRIMARY KEY, name VARCHAR(128))")
        raw.execute("INSERT INTO customers VALUES ('c_1', '老客户')")
        raw.execute("CREATE TABLE recipe_profiles (recipe_id VARCHAR(32) PRIMARY KEY, customer_id VARCHAR(32), summary TEXT)")
        raw.execute("INSERT INTO recipe_profiles VALUES ('r_1', 'c_1', '旧流程说明')")
    eng = create_engine(f"sqlite:///{path}")
    with eng.begin() as conn:
        added = _add_missing_columns(conn)
    assert "customers.summary_by" in added and "customers.mascot" in added
    assert "customers.summary_policy_version" in added
    assert "recipe_profiles.summary_policy_version" in added
    with sqlite3.connect(path) as raw:
        row = raw.execute("SELECT name, summary_by, summary, summary_policy_version FROM customers").fetchone()
        profile = raw.execute("SELECT recipe_id, summary, summary_by, summary_policy_version FROM recipe_profiles").fetchone()
    assert row == ("老客户", "ai", "", 0)
    assert profile == ("r_1", "旧流程说明", "ai", 0)
