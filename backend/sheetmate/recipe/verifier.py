"""比对验证：用训练输入重跑 Recipe，把生成结果和人工结果逐项比较（SheetAgent 的「信任闸门」）。

Recipe 连学习用的数据都复现不了，就不能拿去处理下个月的数据。检查项：
1. sheet 是否齐全
2. 列是否齐全（缺列直接不通过）
3. 行数（默认允许 5% 差异）
4. 数字列合计（默认允许 0.1% 差异）
5. 单元格匹配率（≥98% 通过，90%~98% 警告；SheetAgent 原为 90% / 75%）

相比 SheetAgent 的改进：有共同主键（如运单号）时按主键对齐行，而不是只按行位置；
位置对齐匹配率低时再尝试忽略行顺序比较；输出不一致的单元格样例和每列匹配率，供 AI 精确修正。
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd

from sheetmate.engine.sqlutil import to_jsonable

ROW_TOLERANCE = 0.05
SUM_TOLERANCE = 0.001
# SheetAgent 用 90% 算通过；账单场景错一个归属都是错，这里收紧到 98%（比较本身已经容忍格式、大小写、0.1% 的数值误差）
PASS_THRESHOLD = 0.98
WARN_THRESHOLD = 0.90


def values_equal(expected: Any, actual: Any) -> bool:
    """单元格比较：空对空相等；数字允许 0.1% 误差；日期按天；文本去空格忽略大小写。"""
    e_null, a_null = _is_null(expected), _is_null(actual)
    if e_null and a_null:
        return True
    if e_null or a_null:
        # 空字符串和 0 视为「空」的常见写法差异，不算错
        other = actual if e_null else expected
        return isinstance(other, str) and not other.strip()
    e_num, a_num = _to_number(expected), _to_number(actual)
    if e_num is not None and a_num is not None:
        if e_num == 0:
            return abs(a_num) < 0.005
        return abs(e_num - a_num) / abs(e_num) < 0.001 or abs(e_num - a_num) < 0.005
    e_date, a_date = _to_date(expected), _to_date(actual)
    if e_date is not None and a_date is not None:
        return e_date == a_date
    return str(expected).strip().lower() == str(actual).strip().lower()


def verify_outputs(generated: dict[str, pd.DataFrame], expected: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """比较生成结果与人工结果，返回报告（status: pass / warn / fail）。"""
    report: dict[str, Any] = {"status": "pass", "sheets": [], "overall_match_rate": 0.0, "details": [], "summary": ""}
    pairs = _pair_sheets(generated, expected, report)
    rates: list[float] = []
    for exp_name, gen_name in pairs:
        if gen_name is None:
            report["sheets"].append({"sheet": exp_name, "status": "fail", "match_rate": 0.0,
                                     "messages": [f"生成结果里缺少 sheet「{exp_name}」"]})
            rates.append(0.0)
            continue
        sheet = _verify_sheet(exp_name, expected[exp_name], generated[gen_name])
        if gen_name != exp_name:
            sheet["messages"].insert(0, f"生成的 sheet 名是「{gen_name}」，人工结果是「{exp_name}」")
        report["sheets"].append(sheet)
        rates.append(sheet["match_rate"])

    report["overall_match_rate"] = round(sum(rates) / len(rates), 4) if rates else 0.0
    statuses = {s["status"] for s in report["sheets"]}
    rate = report["overall_match_rate"]
    if "fail" in statuses or not report["sheets"]:
        report["status"] = "fail"
        report["summary"] = f"验证未通过：Recipe 还不能复现人工结果（匹配率 {rate:.1%}）"
    elif "warn" in statuses:
        report["status"] = "warn"
        report["summary"] = f"基本复现人工结果，有需要留意的差异（匹配率 {rate:.1%}）"
    else:
        report["summary"] = f"验证通过：Recipe 复现了人工结果（匹配率 {rate:.1%}）"
    return report


def report_to_text(report: dict[str, Any]) -> str:
    """把验证报告压缩成给 LLM 的文本（修正 Recipe 时用）。"""
    lines = [report["summary"]]
    lines.extend(report.get("details", []))
    for sheet in report["sheets"]:
        lines.append(f"- sheet「{sheet['sheet']}」：{sheet['status']}，匹配率 {sheet.get('match_rate', 0):.1%}")
        for msg in sheet.get("messages", []):
            lines.append(f"    · {msg}")
        bad_cols = [c for c in sheet.get("column_match", []) if c["match_rate"] < 1]
        for col in bad_cols[:8]:
            lines.append(f"    · 列「{col['column']}」匹配率 {col['match_rate']:.1%}")
        for mm in sheet.get("mismatches", [])[:10]:
            where = f"主键 {mm['key']}" if mm.get("key") is not None else f"第 {mm['row']} 行"
            lines.append(f"    · {where}「{mm['column']}」：期望 {mm['expected']!r}，实际 {mm['actual']!r}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _pair_sheets(generated: dict[str, pd.DataFrame], expected: dict[str, pd.DataFrame], report: dict[str, Any]) -> list[tuple[str, str | None]]:
    pairs: list[tuple[str, str | None]] = []
    unused = [g for g in generated if g not in expected]
    for exp_name in expected:
        if exp_name in generated:
            pairs.append((exp_name, exp_name))
        elif len(expected) == 1 and len(generated) == 1:
            pairs.append((exp_name, next(iter(generated))))
            unused = []
        else:
            pairs.append((exp_name, None))
    missing = [e for e, g in pairs if g is None]
    if missing:
        report["details"].append(f"缺少 sheet：{missing}")
    if unused:
        report["details"].append(f"多出的 sheet：{unused}")
    return pairs


def _verify_sheet(name: str, exp: pd.DataFrame, gen: pd.DataFrame) -> dict[str, Any]:
    sheet: dict[str, Any] = {
        "sheet": name,
        "status": "pass",
        "expected_rows": int(len(exp)),
        "actual_rows": int(len(gen)),
        "missing_columns": [str(c) for c in exp.columns if c not in gen.columns],
        "extra_columns": [str(c) for c in gen.columns if c not in exp.columns],
        "messages": [],
        "sum_diffs": {},
        "column_match": [],
        "mismatches": [],
        "alignment": "position",
        "match_rate": 0.0,
    }
    if len(exp):
        row_diff = abs(len(gen) - len(exp)) / len(exp)
        if row_diff > ROW_TOLERANCE:
            sheet["messages"].append(f"行数：期望 {len(exp)}，实际 {len(gen)}（差 {row_diff:.1%}）")
            sheet["status"] = "warn" if row_diff < 0.2 else "fail"
    if sheet["missing_columns"]:
        sheet["messages"].append(f"缺少列：{sheet['missing_columns']}")
        sheet["status"] = "fail"
    if sheet["extra_columns"]:
        sheet["messages"].append(f"多出的列：{sheet['extra_columns']}")
    common = [c for c in exp.columns if c in gen.columns]
    if not common or len(exp) == 0:
        if not common:
            sheet["status"] = "fail"
            sheet["messages"].append("没有可以比较的共同列")
        else:
            sheet["match_rate"] = 1.0 if len(gen) == 0 else 0.0
        return sheet

    key = _alignment_key(exp, gen, common)
    if key:
        e_al, g_al = _align_by_key(exp, gen, key)
        sheet["alignment"] = f"key:{key}"
    else:
        e_al, g_al = exp.reset_index(drop=True), gen.reset_index(drop=True)
    rate, col_stats, mismatches = _cell_match(e_al, g_al, common, key, len(exp))
    if not key and rate < PASS_THRESHOLD:
        e_sorted, g_sorted = _sort_all(exp, common), _sort_all(gen, common)
        rate2, col_stats2, mismatches2 = _cell_match(e_sorted, g_sorted, common, None, len(exp))
        if rate2 > rate:
            rate, col_stats, mismatches = rate2, col_stats2, mismatches2
            sheet["alignment"] = "sorted"
            sheet["messages"].append("行顺序与人工结果不同（忽略顺序后比较）")
    sheet["match_rate"] = round(rate, 4)
    sheet["column_match"] = col_stats
    sheet["mismatches"] = mismatches

    for col in common:
        if pd.api.types.is_numeric_dtype(exp[col]):
            e_sum = float(pd.to_numeric(exp[col], errors="coerce").sum())
            g_sum = float(pd.to_numeric(gen[col], errors="coerce").sum())
            if e_sum and abs(g_sum - e_sum) / abs(e_sum) > SUM_TOLERANCE:
                sheet["sum_diffs"][str(col)] = {"expected": round(e_sum, 4), "actual": round(g_sum, 4),
                                                "diff_pct": round(abs(g_sum - e_sum) / abs(e_sum), 6)}
    if sheet["sum_diffs"]:
        sheet["messages"].append(
            "合计不一致：" + "，".join(f"{c} 期望 {d['expected']} 实际 {d['actual']}" for c, d in sheet["sum_diffs"].items())
        )
        if sheet["status"] == "pass":
            sheet["status"] = "warn"

    if sheet["status"] != "fail":
        if rate >= PASS_THRESHOLD:
            sheet["status"] = sheet["status"] if sheet["status"] == "warn" else "pass"
        elif rate >= WARN_THRESHOLD:
            sheet["status"] = "warn"
        else:
            sheet["status"] = "fail"
    sheet["messages"].insert(0, f"单元格匹配率 {rate:.1%}（{'按主键「' + key + '」对齐' if key else '按行位置对齐'}）")
    return sheet


def _alignment_key(exp: pd.DataFrame, gen: pd.DataFrame, common: list[str]) -> str | None:
    for col in common:
        e, g = exp[col], gen[col]
        if e.isna().any() or e.nunique() != len(exp) or len(exp) < 2:
            continue
        if pd.api.types.is_float_dtype(e):
            continue
        e_vals = {str(v).strip() for v in e.tolist()}
        g_vals = {str(v).strip() for v in g.dropna().tolist()}
        if len(e_vals & g_vals) >= 0.9 * len(e_vals):
            return col
    return None


def _align_by_key(exp: pd.DataFrame, gen: pd.DataFrame, key: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    e = exp.copy()
    g = gen.drop_duplicates(subset=[key]).copy()
    e["__k"] = e[key].astype(str).str.strip()
    g["__k"] = g[key].astype(str).str.strip()
    g = g.set_index("__k").reindex(e["__k"]).reset_index(drop=True)
    return e.drop(columns="__k").reset_index(drop=True), g


def _sort_all(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    try:
        return df.sort_values(by=cols, key=lambda s: s.astype(str)).reset_index(drop=True)
    except (TypeError, ValueError):
        return df.reset_index(drop=True)


def _cell_match(
    exp: pd.DataFrame, gen: pd.DataFrame, common: list[str], key: str | None, expected_rows: int
) -> tuple[float, list[dict[str, Any]], list[dict[str, Any]]]:
    rows = min(len(exp), len(gen)) if not key else len(exp)
    total = max(expected_rows, 1) * len(common)
    matched = 0
    col_stats: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    for col in common:
        col_ok = 0
        e_vals, g_vals = exp[col].tolist(), gen[col].tolist()
        for i in range(rows):
            e, g = e_vals[i], g_vals[i] if i < len(g_vals) else None
            if values_equal(e, g):
                col_ok += 1
            elif len(mismatches) < 30:
                item = {"column": str(col), "expected": to_jsonable(e), "actual": to_jsonable(g)}
                if key:
                    item["key"] = to_jsonable(exp[key].iloc[i])
                else:
                    item["row"] = i + 1
                mismatches.append(item)
        matched += col_ok
        col_stats.append({"column": str(col), "match_rate": round(col_ok / max(expected_rows, 1), 4)})
    return matched / total, col_stats, mismatches


def _is_null(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _to_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        import numpy as np

        if isinstance(value, (np.integer, np.floating)):
            return float(value)
    except ImportError:  # pragma: no cover
        pass
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _to_date(value: Any) -> dt.date | None:
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.date()
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str) and len(value) >= 8:
        try:
            return pd.Timestamp(value.strip()).date()
        except (ValueError, TypeError):
            return None
    return None
