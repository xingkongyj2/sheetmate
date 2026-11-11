"""Recipe 层：执行、校验、比对、diff、输入匹配、从分析过程沉淀、修复。"""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from sheetmate.services.bootstrap.samples import demo_recipe_body
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workbooks.reader import read_workbook
from sheetmate.engine.formulas.runner import apply_formula
from sheetmate.engine.tables.workspace import Workspace
from sheetmate.recipe.definition.compiler import compile_from_workspace
from sheetmate.recipe.learning.differ import diff_tables, diff_to_text
from sheetmate.recipe.execution.runner import InputSource, RecipeRunner, RepairContext
from sheetmate.recipe.learning.matcher import Candidate, match_inputs
from sheetmate.recipe.definition.schema import Recipe, check_recipe, file_pattern_from_name
from sheetmate.recipe.validation.comparison import verify_outputs


def _run(runner: RecipeRunner, sources, out) -> list[dict]:
    async def go():
        return [e async for e in runner.run(sources, output_path=out)]

    return asyncio.run(go())


def _sources(samples, fee_key="fees_sep"):
    return {
        "运费明细": InputSource("运费明细", path=samples[fee_key], filename=samples[fee_key].name),
        "店铺归属": InputSource("店铺归属", path=samples["owners"], filename=samples["owners"].name),
    }


def test_demo_recipe_reproduces_expected(samples, tmp_path):
    recipe = Recipe(**demo_recipe_body())
    assert check_recipe(recipe, FormulaLibrary()) == []
    runner = RecipeRunner(recipe, FormulaLibrary(), Workspace(None))
    events = _run(runner, _sources(samples), tmp_path / "out.xlsx")
    assert events[-1]["type"] == "recipe_run_done"
    assert runner.report["status"] in {"success", "warning"}
    expected = {s.name: s.df for s in read_workbook(samples["expected_sep"])}
    report = verify_outputs(runner.collect_outputs(), expected)
    assert report["status"] == "pass"
    assert report["overall_match_rate"] == 1.0


def test_replay_next_month_with_field_mapping(samples, tmp_path):
    recipe = Recipe(**demo_recipe_body())
    runner = RecipeRunner(recipe, FormulaLibrary(), Workspace(None), field_mapping={"运单号": ["单号"], "店铺名称": ["店铺"]})
    _run(runner, _sources(samples, "fees_oct"), tmp_path / "oct.xlsx")
    assert runner.report["status"] in {"success", "warning"}
    assert runner.report["inputs"][0]["renames"] == {"单号": "运单号", "店铺": "店铺名称"}
    assert runner.report["inputs"][1]["renames"] == {}  # 对照表本来就叫「店铺」，不能被误改
    assert (tmp_path / "oct.xlsx").exists()


def test_missing_required_column_escalates_with_readable_report(samples, tmp_path):
    recipe = Recipe(**demo_recipe_body())
    runner = RecipeRunner(recipe, FormulaLibrary(), Workspace(None))
    _run(runner, _sources(samples, "fees_oct"), tmp_path / "x.xlsx")
    assert runner.report["status"] == "failed"
    assert "缺少必需的列" in runner.report["error"]
    assert "字段映射" in runner.report["escalation"]


class _FixColumnRepairer:
    """模拟修复器：把错误的列名改回正确的列名。"""

    def __init__(self):
        self.calls = 0

    async def repair(self, ctx: RepairContext):
        self.calls += 1
        params = dict(ctx.step.params)
        params["key"] = "店铺名称"
        return ctx.step.model_copy(update={"params": params, "explanation": "自动修复：列名应为店铺名称"})


def test_step_repair_loop(samples, tmp_path):
    body = demo_recipe_body()
    body["steps"][2]["params"]["key"] = "店铺"  # 故意写错列名
    recipe = Recipe(**body)
    repairer = _FixColumnRepairer()
    runner = RecipeRunner(recipe, FormulaLibrary(), Workspace(None), repairer=repairer)
    events = _run(runner, _sources(samples), tmp_path / "r.xlsx")
    assert repairer.calls == 1
    assert runner.report["repaired_steps"] == [3]
    assert runner.recipe.step(3).params["key"] == "店铺名称"
    assert runner.recipe_changed
    assert any(e["type"] == "recipe_repair" and e["status"] == "retry" for e in events)
    snapshot = next(event["recipe"] for event in events if event["type"] == "recipe_run_start")
    assert next(step for step in snapshot["steps"] if step["id"] == 3)["params"]["key"] == "店铺"
    repaired = next(event for event in events if event["type"] == "recipe_step_done" and event["step_id"] == 3)
    assert repaired["params"]["key"] == "店铺名称"
    assert snapshot["inputs"][0]["alias"] == "运费明细"


def test_repair_gives_up_after_max_attempts(samples, tmp_path):
    class Useless:
        async def repair(self, ctx):
            return ctx.step  # 原样返回，永远修不好

    body = demo_recipe_body()
    body["steps"][2]["params"]["key"] = "不存在的列"
    runner = RecipeRunner(Recipe(**body), FormulaLibrary(), Workspace(None), repairer=Useless(), max_repair=2)
    _run(runner, _sources(samples), tmp_path / "g.xlsx")
    assert runner.report["status"] == "escalated"
    assert "卡在第 3 步" in runner.report["escalation"]
    failed = runner.report["steps"][-1]
    assert failed["status"] == "failed" and len(failed["attempts"]) == 3


def test_differ_finds_pivot_sum_and_lookup_fill(samples):
    inputs = {"运费明细": read_workbook(samples["fees_sep"])[0].df, "店铺归属": read_workbook(samples["owners"])[0].df}
    expected = {s.name: s.df for s in read_workbook(samples["expected_sep"])}
    diff = diff_tables(inputs, expected)
    detail = next(s for s in diff["sheets"] if s["output_sheet"] == "账单明细")
    assert detail["pivot"]["pivot_column"] == "费用类型"
    assert detail["pivot"]["value_column"] == "金额"
    assert any("运费 + 保价费 + 包装费" in c["formula"] for c in detail["computed_columns"])
    lookup = detail["lookups"][0]
    assert lookup["lookup_table"] == "店铺归属" and lookup["possible_fill"]["values"] == ["新食研"]
    text = diff_to_text(diff)
    assert "长转宽" in text and "匹配不到时的填充值" in text


def test_verifier_flags_wrong_values():
    expected = {"s": pd.DataFrame({"id": ["a", "b"], "v": [1.0, 2.0]})}
    generated = {"s": pd.DataFrame({"id": ["b", "a"], "v": [2.0, 9.0]})}
    report = verify_outputs(generated, expected)
    sheet = report["sheets"][0]
    assert sheet["alignment"] == "key:id"
    assert sheet["match_rate"] == 0.75
    assert sheet["mismatches"][0] == {"column": "v", "expected": 1.0, "actual": 9.0, "key": "a"}


def test_matcher_assigns_files_to_inputs():
    recipe = Recipe(**demo_recipe_body())
    candidates = [
        Candidate("f1::归属", ["店铺", "归属", "负责人"], filename="店铺归属表.xlsx", sheet="归属"),
        Candidate("f2::明细", ["单号", "下单日期", "店铺", "费用类型", "金额", "重量(kg)"], filename="运费明细_2026-11.xlsx", sheet="明细"),
    ]
    hits = match_inputs(recipe, candidates, {"运单号": ["单号"], "店铺名称": ["店铺"]})
    assert hits["运费明细"]["key"] == "f2::明细"
    assert hits["店铺归属"]["key"] == "f1::归属"


def test_file_pattern():
    assert file_pattern_from_name("运费明细_2026-09.xlsx") == "运费明细*"
    assert file_pattern_from_name("10月账单.xlsx") == "*月账单*"


def test_compile_recipe_from_analysis(samples):
    ws = Workspace(None)
    lib = FormulaLibrary()
    ws.put_dataframe("运费明细", read_workbook(samples["fees_sep"])[0].df, source="uploaded", filename="运费明细_2026-09.xlsx")
    out = apply_formula(ws, lib, "filter_rows", {"table": "运费明细", "condition": "\"金额\" > 10"}, "大额")
    ws.update_meta("大额", role="derived", step={"formula": "filter_rows", "params": out.params})
    out2 = apply_formula(ws, lib, "group_aggregate", {"table": "大额", "group_by": ["店铺名称"],
                                                      "aggregations": [{"column": "金额", "func": "sum"}]}, "店铺汇总")
    ws.update_meta("店铺汇总", role="derived", step={"formula": "group_aggregate", "params": out2.params})
    recipe = compile_from_workspace(ws, ["店铺汇总"], lib, name="大额汇总")
    assert [s.formula for s in recipe.steps] == ["filter_rows", "group_aggregate"]
    assert recipe.inputs[0].alias == "运费明细" and recipe.inputs[0].file_pattern == "运费明细*"
    assert set(recipe.inputs[0].required_columns) == {"金额", "店铺名称"}
    assert check_recipe(recipe, lib) == []


def test_validator_flags_numbers_stored_as_text():
    from sheetmate.recipe.validation.rules import validate_run

    ws = Workspace(None)
    ws.put_dataframe("t", pd.DataFrame({"金额": [str(i * 1.5) for i in range(20)]}), source="uploaded")
    outputs = {"结果": ws.df("t")}  # 经过 DuckDB 读回（pandas 3 下是 str 类型）
    report = validate_run(Recipe(name="x"), {}, outputs)
    assert any(c["rule_type"] == "type_check" and c["status"] == "warn" for c in report["checks"])


def test_validation_rule_param_aliases_and_bad_rules():
    """模型写规则时参数名不统一：output_column / input_table 也要认；列名写错时报「规则写错了」，不能误报合计对不上。"""
    from sheetmate.recipe.definition.schema import ValidationRule
    from sheetmate.recipe.validation.rules import validate_run

    inputs = {"明细": pd.DataFrame({"金额": [0.1, 0.2, 1.0]})}
    outputs = {"汇总": pd.DataFrame({"合计": [0.30000000000000004, 1.0]})}
    rules = [
        ValidationRule(type="sum_preservation", severity="error",
                       params={"output_column": "合计", "input_table": "明细", "input_column": "金额", "tolerance": 0.0}),
        ValidationRule(type="sum_preservation", severity="error", params={"column": "不存在的列", "input_column": "金额"}),
        ValidationRule(type="no_nulls", severity="error", params={"column": "合计"}),
        ValidationRule(type="unique_check", severity="error", params={"columns": ["合计"], "sheet": "没有这个sheet"}),
    ]
    report = validate_run(Recipe(name="x", validation_rules=rules), inputs, outputs)
    checks = {c["rule_id"]: c for c in report["checks"]}
    first, wrong_col, nulls, wrong_sheet = (checks[r.id] for r in rules)
    assert first["status"] == "pass"  # 别名生效，且浮点误差不算差异
    assert wrong_col["status"] == "warn" and "规则写错了" in wrong_col["message"]
    assert nulls["status"] == "pass"  # column 自动转成 columns
    assert wrong_sheet["status"] == "warn" and "没有 sheet" in wrong_sheet["message"]
    assert report["status"] == "warn"  # 写错的规则只警告，不让整次运行失败


def test_sum_preservation_with_input_filter():
    """流程先筛选再汇总时，合计校验要按同样的条件算输入合计。"""
    from sheetmate.recipe.definition.schema import ValidationRule
    from sheetmate.recipe.validation.rules import validate_run

    inputs = {"明细": pd.DataFrame({"状态": ["已完成", "已取消", "已完成"], "金额": [10.0, 99.0, 5.5]})}
    outputs = {"汇总": pd.DataFrame({"金额合计": [15.5]})}
    good = ValidationRule(type="sum_preservation", severity="error",
                          params={"column": "金额合计", "input": "明细", "input_column": "金额", "input_filter": "\"状态\" = '已完成'"})
    naive = ValidationRule(type="sum_preservation", severity="error", params={"column": "金额合计", "input_column": "金额"})
    bad = ValidationRule(type="sum_preservation", severity="error",
                         params={"column": "金额合计", "input_column": "金额", "input_filter": "\"不存在\" = 1"})
    report = validate_run(Recipe(name="x", validation_rules=[good, naive, bad]), inputs, outputs)
    checks = {c["rule_id"]: c for c in report["checks"]}
    assert checks[good.id]["status"] == "pass"
    assert checks[naive.id]["status"] == "fail"  # 不带条件：114.5 vs 15.5，确实对不上
    assert checks[bad.id]["status"] == "warn" and "规则写错了" in checks[bad.id]["message"]


def test_sum_preservation_between_output_sheets():
    from sheetmate.recipe.definition.schema import ValidationRule
    from sheetmate.recipe.validation.rules import validate_run

    outputs = {"订单汇总": pd.DataFrame({"金额合计": [10.0, 5.5]}), "等级汇总": pd.DataFrame({"金额合计": [15.5]})}
    rule = ValidationRule(type="sum_preservation", severity="error",
                          params={"column": "金额合计", "input": "订单汇总", "input_column": "金额合计", "sheet": "等级汇总"})
    report = validate_run(Recipe(name="x", validation_rules=[rule]), {"明细": pd.DataFrame({"金额": [1.0]})}, outputs)
    check = next(c for c in report["checks"] if c["rule_id"] == rule.id)
    assert check["status"] == "pass", check
