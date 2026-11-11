"""文件画像（Profile 引擎）。

画像 = 给 AI 看的「表格说明书」：每个 sheet 的表头位置、列名、类型、行数、空值率、样本值、
候选主键与重复键。AI 根据画像理解数据，不需要把整张表发过去。
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from sheetmate.engine.workbooks.reader import SheetData
from sheetmate.engine.tables.sql import frame_rows, to_jsonable

_SAMPLE_SIZE = 5
_KEY_HINTS = ("号", "编码", "编号", "id", "ID", "代码", "单号", "code", "no")


def dtype_label(series: pd.Series) -> str:
    """粗粒度类型：integer / float / datetime / boolean / text（沿用 SheetAgent 的分类）。"""
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "datetime"
    if pd.api.types.is_integer_dtype(series):
        return "integer"
    if pd.api.types.is_float_dtype(series):
        non_null = series.dropna()
        if len(non_null) and (non_null == non_null.round()).all() and (non_null.abs() < 2**53).all():
            return "integer"
        return "float"
    return "text"


def profile_columns(df: pd.DataFrame) -> list[dict[str, Any]]:
    """逐列画像。"""
    total = len(df)
    columns: list[dict[str, Any]] = []
    for col in df.columns:
        series = df[col]
        non_null = series.dropna()
        if series.dtype == object or pd.api.types.is_string_dtype(series):
            non_null = non_null[non_null.astype(str).str.strip() != ""]
        info: dict[str, Any] = {
            "name": str(col),
            "dtype": dtype_label(series),
            "null_rate": round(1 - len(non_null) / total, 4) if total else 0.0,
            "unique": int(non_null.nunique()) if len(non_null) else 0,
            "samples": [to_jsonable(v) for v in non_null.drop_duplicates().head(_SAMPLE_SIZE).tolist()],
        }
        if info["dtype"] in {"integer", "float"} and len(non_null):
            numeric = pd.to_numeric(non_null, errors="coerce")
            info.update(
                {
                    "min": to_jsonable(numeric.min()),
                    "max": to_jsonable(numeric.max()),
                    "sum": to_jsonable(round(float(numeric.sum()), 4)),
                }
            )
        columns.append(info)
    return columns


def detect_keys(df: pd.DataFrame, columns: list[dict[str, Any]]) -> dict[str, Any]:
    """找候选主键（非空且唯一的列），并报告像编号的列里有没有重复（多行合并、一对多的信号）。"""
    total = len(df)
    candidates: list[str] = []
    duplicated: list[dict[str, Any]] = []
    for info in columns:
        name = info["name"]
        if info["null_rate"] > 0 or total == 0:
            continue
        if info["unique"] == total and info["dtype"] in {"text", "integer"}:
            candidates.append(name)
        elif any(h in name for h in _KEY_HINTS) and info["dtype"] in {"text", "integer"}:
            counts = df[name].value_counts()
            dup = counts[counts > 1]
            if len(dup):
                duplicated.append(
                    {
                        "column": name,
                        "duplicate_values": int(len(dup)),
                        "max_repeat": int(dup.max()),
                        "examples": [to_jsonable(v) for v in dup.index[:3].tolist()],
                    }
                )
    candidates.sort(key=lambda c: (not any(h in c for h in _KEY_HINTS), list(df.columns).index(c)))
    return {"candidates": candidates[:5], "duplicated": duplicated[:5]}


def profile_sheet(sheet: SheetData, preview_rows: int = 20) -> dict[str, Any]:
    """一个 sheet 的完整画像（含前 N 行预览）。"""
    df = sheet.df
    columns = profile_columns(df)
    return {
        "sheet": sheet.name,
        "rows": int(len(df)),
        "cols": int(len(df.columns)),
        "header": sheet.header.to_dict(),
        "quality": dict(sheet.quality or {}),
        "columns": columns,
        "keys": detect_keys(df, columns),
        "preview": {"columns": [str(c) for c in df.columns], "rows": frame_rows(df, preview_rows)},
    }


def profile_workbook(sheets: list[SheetData], preview_rows: int = 20) -> dict[str, Any]:
    """整个文件的画像。"""
    return {"sheets": [profile_sheet(s, preview_rows) for s in sheets]}


def profile_to_text(profile: dict[str, Any], table_names: dict[str, str] | None = None) -> str:
    """把画像压缩成给 LLM 的文本：每列一行（类型、空值率、样本）。"""
    lines: list[str] = []
    for sheet in profile.get("sheets", []):
        label = (table_names or {}).get(sheet["sheet"], sheet["sheet"])
        header = sheet.get("header", {})
        lines.append(
            f"- 表 {label}（sheet「{sheet['sheet']}」）：{sheet['rows']} 行 × {sheet['cols']} 列，"
            f"表头在第 {header.get('header_row', 1)} 行"
            + (f"，共 {header.get('header_rows')} 行表头" if header.get("header_rows", 1) > 1 else "")
        )
        for col in sheet["columns"]:
            extra = ""
            if col["dtype"] in {"integer", "float"} and "sum" in col:
                extra = f"，范围 {col.get('min')}~{col.get('max')}，合计 {col.get('sum')}"
            lines.append(
                f"    · {col['name']}（{col['dtype']}，空值 {col['null_rate']:.0%}，{col['unique']} 个不同值{extra}）"
                f" 样本：{col['samples'][:4]}"
            )
        keys = sheet.get("keys", {})
        if keys.get("candidates"):
            lines.append(f"    候选主键：{keys['candidates']}")
        for dup in keys.get("duplicated", []):
            lines.append(
                f"    注意：「{dup['column']}」有 {dup['duplicate_values']} 个值重复出现（最多 {dup['max_repeat']} 次），"
                "可能是一单多行"
            )
    return "\n".join(lines)
