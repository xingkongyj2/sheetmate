"""Reproducible synthetic acceptance and context-size measurements. No API calls.

Run: backend/.venv/bin/python backend/evals/run_offline.py --output docs/offline-eval.json
Character measurements are not tokens, accuracy scores or billing estimates.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with TemporaryDirectory(prefix="sheetmate-eval-") as directory:
        os.environ["SHEETMATE_DATA_DIR"] = directory
        import pandas as pd
        from sheetmate.agent.memory import MemoryContextLimit, select_relevant
        from sheetmate.agent.tools import AgentContext, ToolRegistry, _tool_value_counts
        from sheetmate.engine.formulas import FormulaLibrary
        from sheetmate.engine.table_memory import format_table_memory
        from sheetmate.engine.workspace import Workspace
        from sheetmate.recipe.schema import Recipe, ValidationRule
        from sheetmate.recipe.validator import validate_run
        from sheetmate.recipe.verifier import values_equal, verify_outputs

        checks = {}
        for name, left, right in (("identifier", "10000001", "10000002"),
                                  ("leading_zero", "001234", "1234"),
                                  ("large_amount", 1000000, 1000900)):
            checks[name] = not values_equal(left, right)
        expected = pd.DataFrame({"单号": [f"X{i}" for i in range(100)], "金额": [0] * 100})
        checks["extra_duplicate"] = verify_outputs({"结果": pd.concat([expected, expected.iloc[[0]]])},
                                                    {"结果": expected})["status"] == "fail"
        wide = pd.DataFrame({"单号": [f"X{i}" for i in range(100)], "客户": ["A"] * 100,
                             **{f"字段{i}": [0] * 100 for i in range(98)}})
        wrong = wide.copy()
        wrong["客户"] = "B"
        checks["wrong_critical_column"] = verify_outputs({"结果": wrong}, {"结果": wide})["status"] == "fail"
        checks["empty_expected"] = verify_outputs({"结果": pd.DataFrame({"a": [1]})},
                                                   {"结果": pd.DataFrame({"a": []})})["status"] == "fail"
        for kind, params in (("no_nulls", {"columns": ["Missing"]}), ("unique_check", {"columns": ["Missing"]}),
                             ("type_check", {"columns": {"Missing": "numeric"}})):
            recipe = Recipe(name="评测", validation_rules=[ValidationRule(type=kind, severity="error", params=params)])
            checks[f"missing_{kind}"] = validate_run(recipe, {}, {"结果": expected})["status"] == "fail"
        recipe = Recipe(name="评测", validation_rules=[ValidationRule(type="historical_range", severity="error",
                                                                       params={"column": "金额"})])
        checks["zero_variance_outlier"] = validate_run(recipe, {}, {"结果": pd.DataFrame({"金额": [1000000]})},
                                                       [{"结果.金额.sum": 100}] * 3)["status"] == "fail"
        insight = dict(category="history_insights", key="运单号", value="核对空格", source="ai")
        standing = [dict(category="domain_knowledge", key=f"规则{i}", value="一般背景", source="ai") for i in range(30)]
        checks["relevant_memory"] = insight in select_relevant([*standing, insight], "检查运单号")
        try:
            select_relevant([dict(category="domain_knowledge", key=f"规则{i}", value="甲" * 500, source="human") for i in range(100)], "金额")
            checks["human_rule_overflow_explicit"] = False
        except MemoryContextLimit:
            checks["human_rule_overflow_explicit"] = True

        ws = Workspace()
        try:
            ws.put_dataframe("orders", pd.DataFrame({"订单号": [f"X{i:05}" for i in range(5000)]}))
            ctx = AgentContext("analyze", ws, FormulaLibrary(), SimpleNamespace(), None, "offline-eval")
            output = _tool_value_counts({"table": "orders", "column": "订单号", "limit": 5000}, ctx)
            info = ws.require("orders")
            memory = format_table_memory(info)
            registry = ToolRegistry()
            full = registry.for_context(ctx)
            core = registry.core_for_context(ctx)
            measurements = {
                "distribution_rows": 5000,
                "tool_result_chars": len(output.text), "next_table_context_chars": len(memory),
                "stored_distribution_rows": len(info.probe_memory["observations"][0]["result"]["rows"]),
                "full_tool_count": len(full), "core_tool_count": len(core),
                "full_tool_schema_chars": len(json.dumps([tool.definition() for tool in full], ensure_ascii=False)),
                "core_tool_schema_chars": len(json.dumps([tool.definition() for tool in core], ensure_ascii=False)),
            }
            checks["bounded_fact_projection"] = len(memory) <= 8000 and measurements["stored_distribution_rows"] == 5000
        finally:
            ws.close()
    result = {"kind": "synthetic_offline_acceptance", "api_calls": 0, "checks": checks,
              "passed": sum(checks.values()), "total": len(checks), "measurements": measurements,
              "limitations": "No live model quality, token cost or latency claims. Schema/text sizes are characters."}
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
