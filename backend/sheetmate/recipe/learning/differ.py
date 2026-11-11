"""输入 vs 输出的结构化 diff（移植自 SheetAgent 的 differ.py，并补充账单场景常见的模式）。

不是把两个 Excel 直接丢给 AI 凭感觉判断，而是程序先提取证据，再把证据摘要交给 LLM：

1. 工作表层：输出的每个 sheet 来自哪张输入表（按列重合度、取值重合度匹配）
2. 列层：共同列、新增列、删除列、疑似重命名、类型变化
3. 行层：行数变化、疑似过滤（分类值被排除 / 数值阈值）、排序、分组汇总
4. 值层：计算列（A±×÷B、多列求和、常数倍）、值变化样例
另外补充：长转宽（类别值变成列）、宽转长（列名变成值）、跨表匹配带出列、匹配不到时的填充值。

这些都是确定性的启发式规则，产出的是「候选解释 + 置信度」，最终步骤由 LLM 结合处理说明生成。
"""

from __future__ import annotations

import itertools
from difflib import SequenceMatcher
from typing import Any

import numpy as np
import pandas as pd

from sheetmate.engine.workbooks.profile import dtype_label
from sheetmate.engine.tables.sql import to_jsonable

_SAMPLE_ROWS = 200


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, str(a).lower().strip(), str(b).lower().strip()).ratio()


def _samples(series: pd.Series, n: int = 3) -> list[Any]:
    return [to_jsonable(v) for v in series.dropna().head(n).tolist()]


def _is_text(series: pd.Series) -> bool:
    return dtype_label(series) == "text"


def _is_number(series: pd.Series) -> bool:
    return dtype_label(series) in {"integer", "float"}


def _str_values(series: pd.Series, limit: int = 5000) -> set[str]:
    values = series.dropna()
    if len(values) > limit:
        values = values.head(limit)
    return {str(v).strip() for v in values.tolist() if str(v).strip()}


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _close(a: pd.Series, b: pd.Series) -> bool:
    try:
        return bool(np.allclose(a.fillna(0).to_numpy(float), b.fillna(0).to_numpy(float), rtol=1e-4, atol=1e-2))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# 列层
# ---------------------------------------------------------------------------


def detect_renames(input_cols: list[str], output_cols: list[str], threshold: float = 0.6) -> dict[str, str]:
    """模糊匹配疑似重命名，返回 {输出列: 输入列}。"""
    input_unmatched = [c for c in input_cols if c not in output_cols]
    output_unmatched = [c for c in output_cols if c not in input_cols]
    renames: dict[str, str] = {}
    used: set[str] = set()
    for out_col in output_unmatched:
        best, best_score = None, 0.0
        for in_col in input_unmatched:
            if in_col in used:
                continue
            score = _similarity(in_col, out_col)
            if score > best_score and score >= threshold:
                best, best_score = in_col, score
        if best:
            renames[out_col] = best
            used.add(best)
    return renames


# ---------------------------------------------------------------------------
# 值层：计算列
# ---------------------------------------------------------------------------


def _check_arithmetic(df: pd.DataFrame, target: str, candidates: list[str]) -> dict[str, Any] | None:
    """target 是否为其他数字列的简单算术组合：A+B、A-B、A*B、A/B、A*常数、多列求和。"""
    sample = df.head(_SAMPLE_ROWS)
    y = _numeric(sample[target])
    if y.notna().sum() < 2:
        return None
    nums = {c: _numeric(sample[c]) for c in candidates if c != target}

    for a, xa in nums.items():
        nz = xa[(xa != 0) & xa.notna() & y.notna()]
        if len(nz) >= 3:
            ratios = y[nz.index] / nz
            if ratios.std() < 1e-3 and abs(ratios.mean() - 1.0) > 1e-6:
                k = round(float(ratios.mean()), 4)
                return {"formula": f"{target} = {a} × {k}", "type": "scalar_multiply", "columns": [a], "constant": k}

    for a, b in itertools.permutations(nums, 2):
        xa, xb = nums[a], nums[b]
        ops = {"+": xa + xb, "-": xa - xb, "×": xa * xb}
        if (xb.fillna(0) != 0).all():
            ops["÷"] = xa / xb
        for sym, result in ops.items():
            if sym in {"+", "×"} and a > b:
                continue
            if _close(y, result):
                return {"formula": f"{target} = {a} {sym} {b}", "type": "arithmetic", "columns": [a, b]}

    names = list(nums)[:10]
    for size in (3, 4):
        for combo in itertools.combinations(names, size):
            total = sum(nums[c].fillna(0) for c in combo)
            if _close(y, total):
                return {"formula": f"{target} = {' + '.join(combo)}", "type": "sum", "columns": list(combo)}
    return None


def _detect_computed(input_df: pd.DataFrame, output_df: pd.DataFrame) -> list[dict[str, Any]]:
    computed = []
    out_numeric = [c for c in output_df.columns if _is_number(output_df[c])]
    for col in output_df.columns:
        if col in input_df.columns or col not in out_numeric:
            continue
        found = _check_arithmetic(output_df, col, out_numeric)
        if found:
            computed.append(found)
    if len(input_df) == len(output_df):
        for col in out_numeric:
            if col in input_df.columns and _is_number(input_df[col]):
                if not _close(_numeric(input_df[col]).head(100).reset_index(drop=True), _numeric(output_df[col]).head(100).reset_index(drop=True)):
                    found = _check_arithmetic(output_df, col, [c for c in out_numeric if c != col])
                    if found:
                        found["note"] = "输入里已有这一列，但值被重新计算了"
                        computed.append(found)
    return computed


# ---------------------------------------------------------------------------
# 行层：过滤、排序、汇总
# ---------------------------------------------------------------------------


def _detect_filters(input_df: pd.DataFrame, output_df: pd.DataFrame, common: list[str]) -> list[dict[str, Any]]:
    removed = len(input_df) - len(output_df)
    if removed <= 0 or not common:
        return []
    filters: list[dict[str, Any]] = []
    for col in common:
        s_in = input_df[col]
        if not _is_text(s_in) or s_in.nunique() >= 20:
            continue
        in_values, out_values = _str_values(s_in), _str_values(output_df[col])
        gone = in_values - out_values
        if gone and len(gone) < len(in_values):
            gone_rows = int(s_in.astype(str).str.strip().isin(gone).sum())
            confidence = 0.95 if gone_rows == removed else 0.6
            filters.append(
                {
                    "column": col,
                    "type": "exclude_values",
                    "values": sorted(gone),
                    "confidence": confidence,
                    "explanation": f"「{col}」为 {sorted(gone)} 的 {gone_rows} 行在输出中消失（共少了 {removed} 行）",
                }
            )
    for col in common:
        if not (_is_number(input_df[col]) and _is_number(output_df[col])):
            continue
        x_in, x_out = _numeric(input_df[col]), _numeric(output_df[col])
        if x_out.min() > x_in.min():
            kept = int((x_in >= x_out.min()).sum())
            if abs(kept - len(output_df)) <= 1:
                filters.append({"column": col, "type": "min_threshold", "threshold": to_jsonable(x_out.min()), "confidence": 0.85,
                                "explanation": f"保留「{col}」≥ {to_jsonable(x_out.min())} 的行"})
        if x_out.max() < x_in.max():
            kept = int((x_in <= x_out.max()).sum())
            if abs(kept - len(output_df)) <= 1:
                filters.append({"column": col, "type": "max_threshold", "threshold": to_jsonable(x_out.max()), "confidence": 0.85,
                                "explanation": f"保留「{col}」≤ {to_jsonable(x_out.max())} 的行"})
    return filters


def _detect_sorting(input_df: pd.DataFrame, output_df: pd.DataFrame, common: list[str]) -> dict[str, Any] | None:
    if len(input_df) != len(output_df) or len(output_df) < 3:
        return None
    for col in common:
        s_out, s_in = output_df[col], input_df[col]
        if _is_number(s_out) or pd.api.types.is_datetime64_any_dtype(s_out):
            if s_out.is_monotonic_increasing and not s_in.is_monotonic_increasing:
                return {"column": col, "order": "asc", "confidence": 0.9}
            if s_out.is_monotonic_decreasing and not s_in.is_monotonic_decreasing:
                return {"column": col, "order": "desc", "confidence": 0.9}
        elif _is_text(s_out):
            values = s_out.dropna().astype(str).tolist()
            if values == sorted(values) and s_in.dropna().astype(str).tolist() != sorted(s_in.dropna().astype(str).tolist()):
                return {"column": col, "order": "asc", "confidence": 0.8}
    return None


def _detect_group_keys(input_df: pd.DataFrame, output_df: pd.DataFrame, renames: dict[str, str]) -> list[dict[str, Any]]:
    """输出行数变少时，找「输出里唯一、且在输入里分组数≈输出行数」的列，作为分组键候选。"""
    if len(output_df) >= len(input_df) or len(output_df) == 0:
        return []
    keys = []
    for out_col in output_df.columns:
        in_col = out_col if out_col in input_df.columns else renames.get(out_col)
        if not in_col or in_col not in input_df.columns:
            continue
        s_out = output_df[out_col]
        if s_out.isna().any() or s_out.nunique() != len(output_df):
            continue
        groups = input_df[in_col].nunique()
        if abs(groups - len(output_df)) <= 2 and _str_values(s_out) <= _str_values(input_df[in_col]) | {""}:
            keys.append({"input_col": in_col, "output_col": out_col, "groups": int(groups), "confidence": 0.85})
    return keys


def _detect_sum_preserved(input_df: pd.DataFrame, output_df: pd.DataFrame) -> list[dict[str, Any]]:
    """数字列总和是否被保留（输入列合计 ≈ 输出列合计 → 可能是 sum 汇总）。"""
    found = []
    for out_col in output_df.columns:
        if not _is_number(output_df[out_col]):
            continue
        out_sum = float(_numeric(output_df[out_col]).sum())
        for in_col in input_df.columns:
            if not _is_number(input_df[in_col]):
                continue
            in_sum = float(_numeric(input_df[in_col]).sum())
            if in_sum and abs(out_sum - in_sum) / abs(in_sum) < 0.001:
                found.append({"input_col": in_col, "output_col": out_col, "func": "sum", "total": round(in_sum, 4)})
    return found


def _detect_pivot(input_df: pd.DataFrame, output_df: pd.DataFrame, added: list[str]) -> dict[str, Any] | None:
    """长转宽：输出新增的列名，正好是输入某个分类列的取值。"""
    if len(added) < 2:
        return None
    best = None
    for col in input_df.columns:
        if not _is_text(input_df[col]) or input_df[col].nunique() > 200:
            continue
        values = _str_values(input_df[col])
        hit = [c for c in added if str(c) in values]
        if len(hit) >= 2 and (best is None or len(hit) > len(best["new_columns"])):
            best = {"pivot_column": col, "new_columns": hit, "all_values": sorted(values)}
    if best is None:
        return None
    out_total = float(sum(_numeric(output_df[c]).fillna(0).sum() for c in best["new_columns"] if _is_number(output_df[c])))
    for col in input_df.columns:
        if _is_number(input_df[col]):
            mask = input_df[best["pivot_column"]].astype(str).isin(best["new_columns"])
            in_total = float(_numeric(input_df.loc[mask, col]).sum())
            if in_total and abs(out_total - in_total) / abs(in_total) < 0.001:
                best["value_column"] = col
                best["aggfunc"] = "sum"
                break
    best["confidence"] = 0.9 if "value_column" in best else 0.7
    return best


def _detect_unpivot(input_df: pd.DataFrame, output_df: pd.DataFrame) -> dict[str, Any] | None:
    """宽转长：输出某个文本列的取值，正好是输入的若干列名。"""
    in_cols = {str(c) for c in input_df.columns}
    for col in output_df.columns:
        if col in input_df.columns or not _is_text(output_df[col]):
            continue
        values = _str_values(output_df[col])
        hit = sorted(values & in_cols)
        if len(hit) >= 2 and len(hit) >= 0.6 * len(values):
            return {"name_column": col, "from_columns": hit, "confidence": 0.85, "rows": f"{len(input_df)} → {len(output_df)}"}
    return None


def _value_changes(input_df: pd.DataFrame, output_df: pd.DataFrame, common: list[str], key: str | None) -> list[dict[str, Any]]:
    """值变化样例：有共同主键时按主键对齐，否则按行位置对齐（SheetAgent 的做法）。"""
    changes: list[dict[str, Any]] = []
    cols = [c for c in common if c != key][:6]
    if key:
        left = input_df.drop_duplicates(subset=[key]).set_index(key)
        right = output_df.drop_duplicates(subset=[key]).set_index(key)
        keys = [k for k in right.index[:50] if k in left.index]
        for k in keys:
            for col in cols:
                a, b = left.at[k, col], right.at[k, col]
                if str(to_jsonable(a)) != str(to_jsonable(b)):
                    changes.append({"key": to_jsonable(k), "column": col, "input_value": to_jsonable(a), "output_value": to_jsonable(b)})
                    if len(changes) >= 20:
                        return changes
        return changes
    if len(input_df) != len(output_df):
        return changes
    for col in cols[:5]:
        for i in range(min(10, len(input_df))):
            a, b = input_df[col].iloc[i], output_df[col].iloc[i]
            if str(to_jsonable(a)) != str(to_jsonable(b)):
                changes.append({"row": i + 1, "column": col, "input_value": to_jsonable(a), "output_value": to_jsonable(b)})
                if len(changes) >= 20:
                    return changes
    return changes


def _common_key(input_df: pd.DataFrame, output_df: pd.DataFrame, common: list[str]) -> str | None:
    """找一个在输入和输出里都非空、在输出里唯一、并且大部分取值能对上的列作为对齐主键。"""
    for col in common:
        s_out = output_df[col]
        if s_out.isna().any() or s_out.nunique() != len(output_df) or len(output_df) < 2:
            continue
        if not (_is_text(s_out) or dtype_label(s_out) == "integer"):
            continue
        overlap = len(_str_values(s_out) & _str_values(input_df[col])) / max(1, len(_str_values(s_out)))
        if overlap >= 0.9:
            return col
    return None


# ---------------------------------------------------------------------------
# 跨表：匹配带出列、填充值
# ---------------------------------------------------------------------------


def _detect_lookups(
    output_df: pd.DataFrame,
    added: list[str],
    main_input: str,
    inputs: dict[str, pd.DataFrame],
) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for out_col in added:
        out_values = _str_values(output_df[out_col])
        if len(out_values) < 1 or len(out_values) > 5000:
            continue
        for name, df in inputs.items():
            if name == main_input:
                continue
            for in_col in df.columns:
                in_values = _str_values(df[in_col])
                if len(in_values) < 1:
                    continue
                overlap = out_values & in_values
                if len(overlap) >= max(1, 0.5 * len(out_values)):
                    fill = sorted(out_values - in_values)
                    item = {
                        "output_column": out_col,
                        "lookup_table": name,
                        "lookup_column": in_col,
                        "overlap_ratio": round(len(overlap) / len(out_values), 3),
                        "relationship": "direct" if out_col == in_col else "lookup",
                    }
                    if fill and len(fill) <= 3:
                        rows = int(output_df[out_col].astype(str).isin(fill).sum())
                        item["possible_fill"] = {"values": fill, "rows": rows}
                    item["possible_keys"] = _possible_join_keys(output_df, df)
                    found.append(item)
                    break
    return found


def _possible_join_keys(output_df: pd.DataFrame, lookup_df: pd.DataFrame) -> list[dict[str, Any]]:
    keys = []
    for out_col in output_df.columns:
        if not _is_text(output_df[out_col]):
            continue
        out_values = _str_values(output_df[out_col])
        for in_col in lookup_df.columns:
            if not _is_text(lookup_df[in_col]) or lookup_df[in_col].nunique() < max(1, 0.8 * len(lookup_df)):
                continue
            in_values = _str_values(lookup_df[in_col])
            overlap = out_values & in_values
            if overlap and len(overlap) >= 0.5 * len(in_values):
                keys.append({"output_column": out_col, "lookup_column": in_col, "overlap": len(overlap)})
    return keys[:3]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def _match_score(input_df: pd.DataFrame, output_df: pd.DataFrame) -> float:
    common = len(set(map(str, input_df.columns)) & set(map(str, output_df.columns)))
    value_cols = 0
    for col in input_df.columns:
        if _is_text(input_df[col]) and input_df[col].nunique() <= 200:
            values = _str_values(input_df[col])
            value_cols = max(value_cols, sum(1 for c in output_df.columns if str(c) in values))
    return common + 0.5 * value_cols


def diff_sheet(input_name: str, input_df: pd.DataFrame, output_name: str, output_df: pd.DataFrame,
               inputs: dict[str, pd.DataFrame] | None = None) -> dict[str, Any]:
    """比较一个输入表和一个输出 sheet。"""
    in_cols, out_cols = [str(c) for c in input_df.columns], [str(c) for c in output_df.columns]
    common = [c for c in in_cols if c in out_cols]
    added = [c for c in out_cols if c not in in_cols]
    removed = [c for c in in_cols if c not in out_cols]
    renames = detect_renames(in_cols, out_cols)

    columns: list[dict[str, Any]] = []
    for col in out_cols:
        if col in common:
            old, new = dtype_label(input_df[col]), dtype_label(output_df[col])
            columns.append({"name": col, "status": "unchanged" if old == new else "type_changed",
                            "old_dtype": old, "new_dtype": new, "samples": _samples(output_df[col])})
        elif col in renames:
            columns.append({"name": col, "status": "renamed", "old_name": renames[col],
                            "old_dtype": dtype_label(input_df[renames[col]]), "new_dtype": dtype_label(output_df[col]),
                            "samples": _samples(output_df[col])})
        else:
            columns.append({"name": col, "status": "added", "new_dtype": dtype_label(output_df[col]), "samples": _samples(output_df[col])})
    for col in removed:
        if col not in renames.values():
            columns.append({"name": col, "status": "removed", "old_dtype": dtype_label(input_df[col])})

    key = _common_key(input_df, output_df, common)
    pivot = _detect_pivot(input_df, output_df, added)
    result: dict[str, Any] = {
        "output_sheet": output_name,
        "input_table": input_name,
        "input_rows": int(len(input_df)),
        "output_rows": int(len(output_df)),
        "input_cols": len(in_cols),
        "output_cols": len(out_cols),
        "columns": columns,
        "added_columns": added,
        "removed_columns": [c for c in removed if c not in renames.values()],
        "renamed_columns": renames,
        "common_columns": common,
        "align_key": key,
        "filters": _detect_filters(input_df, output_df, common),
        "sorting": _detect_sorting(input_df, output_df, common),
        "group_keys": _detect_group_keys(input_df, output_df, renames),
        "sum_preserved": _detect_sum_preserved(input_df, output_df) if len(output_df) < len(input_df) else [],
        "pivot": pivot,
        "unpivot": _detect_unpivot(input_df, output_df),
        "computed_columns": _detect_computed(input_df, output_df),
        "value_changes": _value_changes(input_df, output_df, common, key),
        "lookups": _detect_lookups(output_df, [c for c in added if not (pivot and c in pivot["new_columns"])], input_name, inputs or {}),
    }
    changed = added or removed or renames or result["value_changes"] or len(input_df) != len(output_df)
    result["status"] = "modified" if changed else "unchanged"
    return result


def diff_tables(inputs: dict[str, pd.DataFrame], outputs: dict[str, pd.DataFrame]) -> dict[str, Any]:
    """多输入表 vs 多输出 sheet。每个输出 sheet 自动找最像的输入表来比较。"""
    sheets = []
    for out_name, out_df in outputs.items():
        if not inputs:
            break
        best = max(inputs, key=lambda name: _match_score(inputs[name], out_df))
        sheets.append(diff_sheet(best, inputs[best], out_name, out_df, inputs))
    return {
        "inputs": {name: {"rows": int(len(df)), "cols": int(len(df.columns))} for name, df in inputs.items()},
        "outputs": {name: {"rows": int(len(df)), "cols": int(len(df.columns))} for name, df in outputs.items()},
        "sheets": sheets,
    }


def diff_to_text(diff: dict[str, Any]) -> str:
    """把 diff 压缩成给 LLM 的证据文本。"""
    lines = ["=" * 50, "输入 vs 输出 差异分析（程序检测，供推断处理步骤）", "=" * 50]
    lines.append("输入表：" + "、".join(f"{n}（{v['rows']} 行 × {v['cols']} 列）" for n, v in diff["inputs"].items()))
    lines.append("输出 sheet：" + "、".join(f"{n}（{v['rows']} 行 × {v['cols']} 列）" for n, v in diff["outputs"].items()))
    for sd in diff["sheets"]:
        lines.append(f"\n▶ 输出「{sd['output_sheet']}」最像来自输入表「{sd['input_table']}」")
        lines.append(f"  行数：{sd['input_rows']} → {sd['output_rows']}（{sd['output_rows'] - sd['input_rows']:+d}）；列数：{sd['input_cols']} → {sd['output_cols']}")
        if sd["added_columns"]:
            lines.append(f"  新增列：{sd['added_columns']}")
        if sd["removed_columns"]:
            lines.append(f"  删除列：{sd['removed_columns']}")
        for new, old in sd["renamed_columns"].items():
            lines.append(f"  疑似重命名：「{old}」→「{new}」")
        for col in sd["columns"]:
            if col["status"] == "type_changed":
                lines.append(f"  类型变化：「{col['name']}」{col['old_dtype']} → {col['new_dtype']}")
            elif col["status"] == "added" and col.get("samples"):
                lines.append(f"  新列「{col['name']}」（{col['new_dtype']}）样本：{col['samples']}")
        if sd["pivot"]:
            pv = sd["pivot"]
            lines.append(
                f"  长转宽：输入「{pv['pivot_column']}」的取值 {pv['new_columns']} 变成了列"
                + (f"，值来自「{pv['value_column']}」（{pv['aggfunc']}，总额一致）" if pv.get("value_column") else "")
                + f"；该列全部取值：{pv['all_values']}"
            )
        if sd["unpivot"]:
            up = sd["unpivot"]
            lines.append(f"  宽转长：输入的列 {up['from_columns']} 变成了「{up['name_column']}」列的取值（{up['rows']} 行）")
        for gk in sd["group_keys"]:
            lines.append(f"  可能按「{gk['input_col']}」分组（{gk['groups']} 组 ≈ 输出 {sd['output_rows']} 行）")
        for sp in sd["sum_preserved"][:6]:
            lines.append(f"  合计一致：sum(输入.{sp['input_col']}) = sum(输出.{sp['output_col']}) = {sp['total']}")
        for flt in sd["filters"]:
            lines.append(f"  可能过滤：{flt['explanation']}（置信度 {flt['confidence']}）")
        if sd["sorting"]:
            lines.append(f"  可能排序：按「{sd['sorting']['column']}」{'升序' if sd['sorting']['order'] == 'asc' else '降序'}")
        for cc in sd["computed_columns"]:
            lines.append(f"  计算列：{cc['formula']}" + (f"（{cc['note']}）" if cc.get("note") else ""))
        for lk in sd["lookups"]:
            text = f"  跨表匹配：输出「{lk['output_column']}」的值来自表「{lk['lookup_table']}」的「{lk['lookup_column']}」（重合 {lk['overlap_ratio']:.0%}）"
            if lk.get("possible_keys"):
                text += "，可能的匹配键：" + "、".join(f"输出.{k['output_column']} ↔ {lk['lookup_table']}.{k['lookup_column']}" for k in lk["possible_keys"])
            if lk.get("possible_fill"):
                text += f"；有 {lk['possible_fill']['rows']} 行的值 {lk['possible_fill']['values']} 不在来源表里，可能是匹配不到时的填充值"
            lines.append(text)
        if sd["value_changes"]:
            lines.append(f"  值变化样例（{'按主键 ' + sd['align_key'] + ' 对齐' if sd['align_key'] else '按行位置'}）：")
            for vc in sd["value_changes"][:6]:
                where = f"主键 {vc['key']}" if "key" in vc else f"第 {vc['row']} 行"
                lines.append(f"    {where}「{vc['column']}」：{vc['input_value']} → {vc['output_value']}")
    return "\n".join(lines)


def diff_view(diff: dict[str, Any]) -> dict[str, Any]:
    """前端「处理前 / 处理后」对比卡片需要的精简数据。"""
    cards = []
    for sd in diff["sheets"]:
        highlights: list[str] = []
        if sd["pivot"]:
            highlights.append(f"「{sd['pivot']['pivot_column']}」拆成了 {len(sd['pivot']['new_columns'])} 列")
        if sd["unpivot"]:
            highlights.append(f"{len(sd['unpivot']['from_columns'])} 列转成了行")
        for gk in sd["group_keys"][:1]:
            highlights.append(f"按「{gk['input_col']}」汇总")
        for flt in sd["filters"][:2]:
            highlights.append(flt["explanation"])
        for cc in sd["computed_columns"][:3]:
            highlights.append(cc["formula"])
        for lk in sd["lookups"][:2]:
            highlights.append(f"从「{lk['lookup_table']}」带出「{lk['output_column']}」")
        cards.append(
            {
                "output_sheet": sd["output_sheet"],
                "input_table": sd["input_table"],
                "rows": [sd["input_rows"], sd["output_rows"]],
                "cols": [sd["input_cols"], sd["output_cols"]],
                "added": sd["added_columns"],
                "removed": sd["removed_columns"],
                "renamed": sd["renamed_columns"],
                "highlights": highlights,
            }
        )
    return {"cards": cards}
