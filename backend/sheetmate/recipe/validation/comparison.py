"""比对验证：用训练输入重跑 Recipe，把生成结果和人工结果逐项比较（SheetAgent 的「信任闸门」）。

Recipe 连学习用的数据都复现不了，就不能拿去处理下个月的数据。检查项：
1. sheet 是否齐全
2. 列是否齐全（缺列直接不通过）
3. 行数与主键集合完全一致，生成结果不能含重复主键
4. 数值只允许半分以内的绝对误差，不随金额大小放宽
5. 单元格匹配率、逐列匹配率及关键业务列分别检查

相比 SheetAgent 的改进：有共同主键（如运单号）时按主键对齐行，而不是只按行位置；
位置对齐匹配率低时再尝试忽略行顺序比较；输出不一致的单元格样例和每列匹配率，供 AI 精确修正。
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from typing import Any

import pandas as pd

from sheetmate.engine.tables.sql import to_jsonable

# 全表匹配率用于诊断；行数、主键和关键列仍有独立约束。
PASS_THRESHOLD = 0.98
WARN_THRESHOLD = 0.90


def values_equal(expected: Any, actual: Any, semantic: str | None = None) -> bool:
    """比较单元格。

    ``semantic`` 是可选的列语义，保持旧调用兼容。标识符必须精确相等，
    数值默认只容忍半分的绝对误差，不随金额放大；数字形态文本保持精确。
    """
    e_null, a_null = _is_null(expected), _is_null(actual)
    if e_null and a_null:
        return True
    if e_null or a_null:
        # 空字符串与缺失值可等价，数值 0 不能视为空。
        other = actual if e_null else expected
        return isinstance(other, str) and not other.strip()
    if semantic in {"identifier", "code", "key", "category"}:
        # 不能把带前导零的编号或相邻编号当成金额近似值。
        return str(expected).strip() == str(actual).strip()
    if (semantic is None and isinstance(expected, str) and isinstance(actual, str)
            and re.fullmatch(r"[+-]?\d+", expected.strip()) and re.fullmatch(r"[+-]?\d+", actual.strip())):
        return expected.strip() == actual.strip()
    e_num, a_num = _to_decimal(expected), _to_decimal(actual)
    if e_num is not None and a_num is not None:
        return abs(e_num - a_num) < Decimal("0.005")
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
    if len(exp) != len(gen):
        row_diff = abs(len(gen) - len(exp)) / max(len(exp), 1)
        sheet["messages"].append(f"行数：期望 {len(exp)}，实际 {len(gen)}（差 {row_diff:.1%}）")
        # 额外/缺失记录不能被整体匹配率掩盖。
        sheet["status"] = "fail"
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
            if len(exp) == 0 and len(gen) != 0:
                sheet["status"] = "fail"
                sheet["messages"].append("人工结果为空，但生成结果包含记录")
        return sheet

    key = _alignment_key(exp, gen, common)
    if key:
        expected_keys = _key_values(exp[key])
        generated_key_list = [str(v).strip() for v in gen[key].tolist() if not _is_null(v) and str(v).strip()]
        generated_keys = set(generated_key_list)
        duplicate_keys = sorted(k for k, count in Counter(generated_key_list).items() if count > 1)
        missing_keys = sorted(expected_keys - generated_keys)
        extra_keys = sorted(generated_keys - expected_keys)
        if duplicate_keys:
            sheet["status"] = "fail"
            sheet["messages"].append(f"生成结果主键重复：{duplicate_keys[:8]}")
        if missing_keys:
            sheet["status"] = "fail"
            sheet["messages"].append(f"生成结果缺少主键：{missing_keys[:8]}")
        if extra_keys:
            sheet["status"] = "fail"
            sheet["messages"].append(f"生成结果多出主键：{extra_keys[:8]}")
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
    for column in col_stats:
        semantic = _column_semantic(column["column"])
        if semantic in {"identifier", "category", "money"} and column["match_rate"] < 1:
            sheet["status"] = "fail"
            sheet["messages"].append(f"关键列「{column['column']}」存在不匹配值，匹配率 {column['match_rate']:.1%}")
        elif column["match_rate"] < WARN_THRESHOLD:
            sheet["status"] = "fail"
            sheet["messages"].append(f"列「{column['column']}」匹配率 {column['match_rate']:.1%}，不能被其他列平均掩盖")

    for col in common:
        if pd.api.types.is_numeric_dtype(exp[col]):
            e_sum = float(pd.to_numeric(exp[col], errors="coerce").sum())
            g_sum = float(pd.to_numeric(gen[col], errors="coerce").sum())
            if abs(g_sum - e_sum) >= 0.005:
                sheet["sum_diffs"][str(col)] = {"expected": round(e_sum, 4), "actual": round(g_sum, 4),
                                                "diff_pct": round(abs(g_sum - e_sum) / abs(e_sum), 6) if e_sum else None}
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
        if len(e_vals) != len(exp):
            continue
        g_vals = {str(v).strip() for v in g.dropna().tolist()}
        if len(e_vals & g_vals) >= 0.9 * len(e_vals):
            return col
    return None


def _key_values(series: pd.Series) -> set[str]:
    """键集合的保守规范化：保留前导零，空键不参与集合差异。"""
    return {str(v).strip() for v in series.tolist() if not _is_null(v) and str(v).strip()}


def _column_semantic(column: Any) -> str | None:
    text = str(column).strip().lower()
    if re.search(r"(?:^|[^a-z])(id|code|key)(?:$|[^a-z])", text) or any(
        token in text for token in ("编号", "编码", "单号", "手机号", "证件")
    ):
        return "identifier"
    if any(token in text for token in ("客户", "归属", "账户", "收款人", "customer", "owner", "account")):
        return "category"
    if any(token in text for token in ("金额", "合计", "收入", "成本", "费用", "单价", "price", "amount", "cost")):
        return "money"
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).strip().replace(",", ""))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def _align_by_key(exp: pd.DataFrame, gen: pd.DataFrame, key: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    e = exp.copy()
    e["__k"] = e[key].astype(str).str.strip()
    e["__n"] = e.groupby("__k", dropna=False).cumcount()
    g = gen.copy()
    g["__k"] = g[key].astype(str).str.strip()
    g["__n"] = g.groupby("__k", dropna=False).cumcount()
    g = g.set_index(["__k", "__n"]).reindex(pd.MultiIndex.from_frame(e[["__k", "__n"]])).reset_index(drop=True)
    return e.drop(columns=["__k", "__n"]).reset_index(drop=True), g.drop(columns=["__k", "__n"], errors="ignore")


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
            if values_equal(e, g, _column_semantic(col)):
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


def _to_date(value: Any) -> pd.Timestamp | None:
    """Preserve time, subsecond precision and timezone when comparing dates.

    A plain date is equivalent to a naive midnight only. Aware timestamps
    compare by instant; no timezone is invented for an unzoned value.
    """
    if isinstance(value, (pd.Timestamp, dt.datetime, dt.date)) or (isinstance(value, str) and len(value) >= 8):
        try:
            timestamp = pd.Timestamp(value.strip() if isinstance(value, str) else value)
            return None if pd.isna(timestamp) else timestamp
        except (ValueError, TypeError):
            return None
    return None
