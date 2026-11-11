"""字段标准化（别名映射）。

不同表里同一个意思的字段名经常不一样（订单号 / 单号 / 运单号），这是 Excel 自动化的核心难点。
做法：客户维护「标准字段名 → 别名列表」，读入时把别名统一改成标准名，后续公式全部用标准名。

注意：语义相近但不同的字段（例如 ERP单号 和 订单号）不能配成别名，必须由人确认映射。
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import pandas as pd

_NOISE = re.compile(r"[\s_\-()（）\[\]【】:：/\\.]+")


def normalize_name(name: str) -> str:
    """比较列名用：去空白和常见符号、转小写。"""
    return _NOISE.sub("", str(name)).lower()


def apply_field_mapping(df: pd.DataFrame, mapping: dict[str, list[str]]) -> tuple[pd.DataFrame, dict[str, str]]:
    """按映射把别名列改成标准名。已有标准名的列不动。返回 (新表, {原列名: 标准名})。"""
    if not mapping:
        return df, {}
    columns = [str(c) for c in df.columns]
    normalized = {normalize_name(c): c for c in columns}
    renames: dict[str, str] = {}
    taken = set(columns)
    for standard, aliases in mapping.items():
        if standard in taken:
            continue
        for alias in aliases or []:
            actual = alias if alias in columns else normalized.get(normalize_name(alias))
            if actual and actual not in renames and actual != standard:
                renames[actual] = standard
                taken.add(standard)
                break
    if not renames:
        return df, {}
    return df.rename(columns=renames), renames


def match_columns(
    expected: list[str],
    actual: list[str],
    aliases: dict[str, list[str]] | None = None,
    threshold: float = 0.8,
) -> tuple[dict[str, str], list[str]]:
    """把 Recipe 期望的列对应到新文件的实际列（沿用 SheetAgent 的模糊匹配，并先查客户别名）。

    返回 ({期望列: 实际列}，缺失的期望列)。匹配顺序：完全相同 → 客户别名 → 去符号后相同 → 相似度 ≥ threshold。
    """
    aliases = aliases or {}
    mapping: dict[str, str] = {}
    used: set[str] = set()
    actual_norm = {normalize_name(a): a for a in actual}

    def take(exp: str, act: str) -> None:
        mapping[exp] = act
        used.add(act)

    for exp in expected:
        if exp in actual:
            take(exp, exp)
    for exp in expected:
        if exp in mapping:
            continue
        for alias in aliases.get(exp, []):
            cand = alias if alias in actual else actual_norm.get(normalize_name(alias))
            if cand and cand not in used:
                take(exp, cand)
                break
    for exp in expected:
        if exp in mapping:
            continue
        cand = actual_norm.get(normalize_name(exp))
        if cand and cand not in used:
            take(exp, cand)
    for exp in expected:
        if exp in mapping:
            continue
        best, best_score = None, 0.0
        for act in actual:
            if act in used:
                continue
            score = SequenceMatcher(None, normalize_name(exp), normalize_name(act)).ratio()
            if score > best_score:
                best, best_score = act, score
        if best is not None and best_score >= threshold:
            take(exp, best)
    missing = [e for e in expected if e not in mapping]
    return mapping, missing
