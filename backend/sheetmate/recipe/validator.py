"""运行校验（移植自 SheetAgent validator.py）：每次执行 Recipe 都会产出一份校验报告。

1. 自动检查（每次都跑）：输出不为空、该有的 sheet 都有、没有整列为空、数字没有被存成文本
2. Recipe 自带规则：row_count_ratio / sum_preservation / no_nulls / type_check / unique_check /
   column_exists / historical_range
不通过的规则：severity=error 记为 fail，其余记为 warn。
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from sheetmate.recipe.schema import Recipe, ValidationRule


def validate_run(
    recipe: Recipe,
    inputs: dict[str, pd.DataFrame],
    outputs: dict[str, pd.DataFrame],
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    checks = _auto_checks(recipe, outputs)
    for rule in recipe.validation_rules:
        if rule.requires_history and not history:
            checks.append(_check(rule, "info", message="需要历史运行数据，暂时跳过"))
            continue
        try:
            checks.append(_run_rule(rule, inputs, outputs, history or []))
        except Exception as exc:  # 规则本身写错了，不应该让整次运行失败
            checks.append(_check(rule, "warn", message=f"校验规则执行出错：{exc}"))
    passed = sum(1 for c in checks if c["status"] == "pass")
    warnings = sum(1 for c in checks if c["status"] == "warn")
    failures = sum(1 for c in checks if c["status"] == "fail")
    if failures:
        status, summary = "fail", f"{failures} 项校验未通过，{warnings} 项警告"
    elif warnings:
        status, summary = "warn", f"校验通过，有 {warnings} 项警告"
    else:
        status, summary = "pass", f"全部 {passed} 项校验通过"
    return {"status": status, "summary": summary, "passed": passed, "warnings": warnings, "failures": failures, "checks": checks}


def output_metrics(outputs: dict[str, pd.DataFrame]) -> dict[str, float]:
    """记录本次输出的关键指标（行数、数字列合计），供以后的 historical_range 规则比较。"""
    metrics: dict[str, float] = {}
    for sheet, df in outputs.items():
        metrics[f"{sheet}.__rows"] = float(len(df))
        for col in df.columns:
            if pd.api.types.is_numeric_dtype(df[col]):
                metrics[f"{sheet}.{col}.sum"] = float(pd.to_numeric(df[col], errors="coerce").sum())
    return metrics


# ---------------------------------------------------------------------------
# 自动检查
# ---------------------------------------------------------------------------


def _auto_checks(recipe: Recipe, outputs: dict[str, pd.DataFrame]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    total_rows = sum(len(df) for df in outputs.values())
    checks.append(
        {
            "rule_id": "auto_not_empty", "rule_type": "not_empty", "description": "输出不能为空",
            "status": "pass" if total_rows else "fail", "severity": "error",
            "expected": "至少 1 行", "actual": f"{len(outputs)} 个 sheet 共 {total_rows} 行",
            "message": "" if total_rows else "输出是空的！",
        }
    )
    expected = [o.name for o in recipe.output_spec if not o.split_by]
    if expected:
        missing = [n for n in expected if n not in outputs]
        checks.append(
            {
                "rule_id": "auto_sheets_exist", "rule_type": "sheets_exist", "description": "该有的 sheet 都要有",
                "status": "fail" if missing else "pass", "severity": "error",
                "expected": expected, "actual": list(outputs), "message": f"缺少 sheet：{missing}" if missing else "",
            }
        )
    for sheet, df in outputs.items():
        empty_cols = [str(c) for c in df.columns if len(df) and df[c].isna().all()]
        if empty_cols:
            checks.append(
                {
                    "rule_id": f"auto_null_cols_{sheet}", "rule_type": "null_columns",
                    "description": f"「{sheet}」不应有整列为空", "status": "warn", "severity": "warning",
                    "actual": empty_cols, "message": f"整列为空：{empty_cols}",
                }
            )
        for col in df.columns:
            # pandas 3 里 DuckDB 读回的文本列是 str 类型，pandas 2 里是 object，两种都要检查
            if not (df[col].dtype == object or pd.api.types.is_string_dtype(df[col])):
                continue
            values = df[col].dropna()
            if len(values) <= 10:
                continue
            as_num = pd.to_numeric(values.astype(str).str.replace(",", ""), errors="coerce")
            ratio = as_num.notna().mean()
            looks_like_id = values.astype(str).str.match(r"^0\d+").any() or values.astype(str).str.len().max() >= 12
            if ratio > 0.9 and not looks_like_id:
                checks.append(
                    {
                        "rule_id": f"auto_dtype_{sheet}_{col}", "rule_type": "type_check",
                        "description": f"「{sheet}.{col}」看起来是数字却存成了文本", "status": "warn", "severity": "warning",
                        "message": f"{int(as_num.notna().sum())}/{len(values)} 个值是数字形式的文本",
                    }
                )
    return checks


# ---------------------------------------------------------------------------
# Recipe 规则
# ---------------------------------------------------------------------------


def _check(rule: ValidationRule, status: str, expected: Any = None, actual: Any = None, message: str = "") -> dict[str, Any]:
    if status == "fail" and rule.severity != "error":
        status = "warn"
    return {
        "rule_id": rule.id, "rule_type": rule.type, "description": rule.description or rule.type,
        "status": status, "severity": rule.severity, "expected": expected, "actual": actual, "message": message,
    }


# 规则参数的常见别名（模型写规则时参数名并不统一，例如 output_column / input_table），统一成标准名再执行
_PARAM_ALIASES = {
    "column": ("column", "output_column", "col", "field", "target_column", "column_name"),
    "input_column": ("input_column", "source_column", "from_column"),
    "input": ("input", "input_table", "source_table", "vs", "table"),
    "sheet": ("sheet", "output_sheet", "sheet_name"),
    "min_ratio": ("min_ratio", "min", "lower"),
    "max_ratio": ("max_ratio", "max", "upper"),
    "tolerance": ("tolerance", "tol", "max_diff"),
}


def normalize_rule_params(rule_type: str, params: dict[str, Any]) -> dict[str, Any]:
    """把规则参数的各种写法统一成标准名（标准名优先；已经写对的不会被覆盖）。"""
    result = dict(params or {})
    for standard, aliases in _PARAM_ALIASES.items():
        if standard in result:
            continue
        for alias in aliases:
            if alias in result and alias != standard:
                result[standard] = result[alias]
                break
    columns = result.get("columns")
    if isinstance(columns, str):
        result["columns"] = [c.strip() for c in columns.replace("，", ",").split(",") if c.strip()]
    if rule_type in {"no_nulls", "unique_check", "column_exists"} and not result.get("columns") and result.get("column"):
        result["columns"] = [result["column"]]
    return result


def _target_sheets(rule: ValidationRule, outputs: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    sheet = rule.params.get("sheet")
    if sheet:
        return {sheet: outputs[sheet]} if sheet in outputs else {}
    return outputs


def _run_rule(rule: ValidationRule, inputs: dict[str, pd.DataFrame], outputs: dict[str, pd.DataFrame], history: list[dict[str, Any]]) -> dict[str, Any]:
    rule = rule.model_copy(update={"params": normalize_rule_params(rule.type, rule.params)})
    p = rule.params
    if p.get("sheet") and p["sheet"] not in outputs:
        return _check(rule, "warn", message=f"规则写错了：输出里没有 sheet「{p['sheet']}」（现有 {list(outputs)}），这条规则没有执行")
    sheets = _target_sheets(rule, outputs)
    if rule.type == "row_count_ratio":
        vs = p.get("vs") or p.get("input")
        in_rows = len(inputs[vs]) if vs in inputs else sum(len(df) for df in inputs.values())
        out_rows = sum(len(df) for df in sheets.values())
        ratio = out_rows / in_rows if in_rows else 0.0
        lo, hi = float(p.get("min_ratio", 0.3)), float(p.get("max_ratio", 1.5))
        ok = lo <= ratio <= hi
        return _check(rule, "pass" if ok else "fail", f"{lo:.2f} ~ {hi:.2f}", f"{ratio:.2f}（{out_rows}/{in_rows} 行）",
                      "" if ok else f"输出/输入行数比 {ratio:.2f} 超出范围")
    if rule.type == "sum_preservation":
        col = str(p.get("column") or "")
        in_col = str(p.get("input_column") or col)
        tolerance = float(p.get("tolerance", 0.01))
        if p.get("input") in outputs and p.get("input") not in inputs:
            # 输出之间对账（例如 汇总表的合计 = 明细表的合计）：对比的「输入」是另一个输出 sheet
            in_frames = [outputs[p["input"]]]
        else:
            in_frames = [inputs[p["input"]]] if p.get("input") in inputs else list(inputs.values())
        out_frames = [df for df in sheets.values() if col in df.columns]
        in_hits = [df for df in in_frames if in_col in df.columns]
        # 列名对不上时不能把合计当成 0 去比，那会误报「合计对不上」：明确告诉是规则本身写错了
        if not col or not out_frames:
            return _check(rule, "warn", message=f"规则写错了：输出里找不到列「{col or '（没写）'}」，这条规则没有执行")
        if not in_hits:
            return _check(rule, "warn", message=f"规则写错了：输入里找不到列「{in_col}」，这条规则没有执行")
        condition = str(p.get("input_filter") or p.get("filter") or "").strip()
        if condition:
            try:
                in_sum = _filtered_sum(in_hits, in_col, condition)
            except Exception as exc:  # 条件写错了
                return _check(rule, "warn", message=f"规则写错了：input_filter「{condition}」无法执行（{exc}），这条规则没有执行")
        else:
            in_sum = sum(float(pd.to_numeric(df[in_col], errors="coerce").sum()) for df in in_hits)
        out_sum = sum(float(pd.to_numeric(df[col], errors="coerce").sum()) for df in out_frames)
        if abs(out_sum - in_sum) < 0.005:  # 金额到分一致即可（浮点累加误差不算差异）
            diff = 0.0
        else:
            diff = abs(out_sum - in_sum) / abs(in_sum) if in_sum else float("inf")
        ok = diff <= tolerance
        return _check(rule, "pass" if ok else "fail", f"合计误差 ≤ {tolerance:.2%}",
                      f"输入「{in_col}」合计 {in_sum:,.2f}，输出「{col}」合计 {out_sum:,.2f}，误差 {diff:.4%}",
                      "" if ok else f"合计对不上：输入 {in_sum:,.2f}，输出 {out_sum:,.2f}")
    if rule.type in {"no_nulls", "unique_check", "column_exists"} and not p.get("columns"):
        return _check(rule, "warn", message="规则写错了：没有写要检查哪些列（columns），这条规则没有执行")
    if rule.type == "no_nulls":
        cols = p.get("columns", [])
        found = [f"{s}.{c}: {int(df[c].isna().sum())} 个空值" for s, df in sheets.items() for c in cols if c in df.columns and df[c].isna().any()]
        return _check(rule, "fail" if found else "pass", f"{cols} 不能有空值", "；".join(found) or "无空值", "；".join(found))
    if rule.type == "unique_check":
        cols = p.get("columns", [])
        found = []
        for s, df in sheets.items():
            present = [c for c in cols if c in df.columns]
            if present:
                dups = int(df.duplicated(subset=present, keep=False).sum())
                if dups:
                    found.append(f"{s}: {dups} 行在 {present} 上重复")
        return _check(rule, "fail" if found else "pass", f"{cols} 唯一", "；".join(found) or "唯一", "；".join(found))
    if rule.type == "column_exists":
        cols = p.get("columns", [])
        missing = [f"{s}.{c}" for s, df in sheets.items() for c in cols if c not in df.columns]
        if not sheets:
            missing = [f"(sheet {p.get('sheet')}).{c}" for c in cols]
        return _check(rule, "fail" if missing else "pass", f"包含列 {cols}", "全部存在" if not missing else f"缺少 {missing}",
                      f"缺少列：{missing}" if missing else "")
    if rule.type == "type_check":
        wanted = p.get("columns", {})
        bad = []
        for s, df in sheets.items():
            for col, want in wanted.items():
                if col not in df.columns:
                    continue
                actual = "numeric" if pd.api.types.is_numeric_dtype(df[col]) else "datetime" if pd.api.types.is_datetime64_any_dtype(df[col]) else "text"
                if actual != want:
                    bad.append(f"{s}.{col}: 期望 {want}，实际 {actual}")
        return _check(rule, "fail" if bad else "pass", str(wanted), "；".join(bad) or "类型正确", "；".join(bad))
    if rule.type == "historical_range":
        return _historical(rule, sheets, history)
    return _check(rule, "info", message=f"未知的校验类型：{rule.type}")


def _filtered_sum(frames: list[pd.DataFrame], column: str, condition: str) -> float:
    """在临时 DuckDB 里按条件求输入列合计（条件写法和 filter_rows 一样：列名用双引号，文本用单引号）。"""
    import duckdb

    from sheetmate.engine.sqlutil import assert_safe_expression, quote_ident

    expr = assert_safe_expression(condition)
    con = duckdb.connect(":memory:")
    try:
        con.execute("SET enable_external_access = false").fetchall()
        total = 0.0
        for i, df in enumerate(frames):
            name = f"__in{i}"
            con.register(name, df)
            value = con.execute(
                f"SELECT sum(TRY_CAST({quote_ident(column)} AS DOUBLE)) FROM {name} WHERE ({expr})"
            ).fetchall()[0][0]
            total += float(value or 0)
        return total
    finally:
        con.close()


def _historical(rule: ValidationRule, sheets: dict[str, pd.DataFrame], history: list[dict[str, Any]]) -> dict[str, Any]:
    p = rule.params
    col = p.get("column", "")
    std_devs = float(p.get("std_devs", 2))
    if len(history) < 3:
        return _check(rule, "info", message="至少需要 3 次历史运行")
    current = sum(float(pd.to_numeric(df[col], errors="coerce").sum()) for df in sheets.values() if col in df.columns)
    key_suffix = f".{col}.sum"
    values = [v for h in history for k, v in h.items() if k.endswith(key_suffix)]
    if len(values) < 3:
        return _check(rule, "info", message="历史数据里没有这一列")
    mean, std = float(np.mean(values)), float(np.std(values))
    if std == 0:
        return _check(rule, "pass", actual=current, message="历史数据没有波动")
    z = abs(current - mean) / std
    ok = z <= std_devs
    return _check(rule, "pass" if ok else "fail", f"历史均值 {mean:,.2f} ± {std_devs} 倍标准差", f"本次 {current:,.2f}",
                  "" if ok else f"本次合计偏离历史均值 {z:.1f} 个标准差")
