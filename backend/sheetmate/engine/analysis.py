"""只读分析：查看表结构、列统计、值分布。不产生新表，供 Agent 理解数据。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from sheetmate.engine.profile import detect_keys, profile_columns
from sheetmate.engine.sqlutil import frame_rows, frame_to_text, quote_ident as q
from sheetmate.engine.table_memory import cached_observation, cached_profile, save_observation, save_profile
from sheetmate.engine.workspace import Workspace


def table_info(ws: Workspace, name: str, sample_rows: int = 5) -> dict[str, Any]:
    """表结构 + 每列画像 + 前几行样例（TabClaw 的 table_info，改为基于 DuckDB）。"""
    with ws.locked():
        info = ws.require(name)
        profile = cached_profile(info)
        if profile is None:
            df = ws.df(name)
            columns = profile_columns(df)
            profile = {"columns": columns, "keys": detect_keys(df, columns),
                       "sample": {"columns": info.column_names, "rows": frame_rows(df, max(20, sample_rows))}}
            save_profile(ws, name, profile, expected_revision=info.revision)
        sample = profile.get("sample", {"columns": info.column_names, "rows": []})
        if len(sample["rows"]) < min(max(0, sample_rows), info.rows):
            # 更多预览只读取所需的前几行，不重新扫描各列生成画像。
            sample = {"columns": info.column_names, "rows": frame_rows(ws.df(name, limit=sample_rows))}
            profile["sample"] = sample
            save_profile(ws, name, profile, expected_revision=info.revision)
        return {
            "name": info.name,
            "label": info.label,
            "source": info.source,
            "rows": info.rows,
            "cols": len(info.columns),
            "columns": profile["columns"],
            "keys": profile["keys"],
            "sample": {"columns": sample["columns"], "rows": sample["rows"][:max(0, sample_rows)]},
        }


def table_info_text(data: dict[str, Any]) -> str:
    lines = [f"表「{data['name']}」（{data['label']}）：{data['rows']} 行 × {data['cols']} 列"]
    for col in data["columns"]:
        extra = ""
        if "sum" in col:
            extra = f"，范围 {col.get('min')}~{col.get('max')}，合计 {col.get('sum')}"
        lines.append(
            f"- {col['name']}：{col['dtype']}，空值 {col['null_rate']:.0%}，{col['unique']} 个不同值{extra}，样本 {col['samples'][:5]}"
        )
    keys = data.get("keys", {})
    if keys.get("candidates"):
        lines.append(f"候选主键：{keys['candidates']}")
    for dup in keys.get("duplicated", []):
        lines.append(f"「{dup['column']}」有 {dup['duplicate_values']} 个值重复（最多 {dup['max_repeat']} 次），可能是一单多行")
    sample = pd.DataFrame(data["sample"]["rows"], columns=data["sample"]["columns"])
    lines.append("前几行：\n" + frame_to_text(sample, limit=len(sample)))
    return "\n".join(lines)


def value_counts(ws: Workspace, name: str, column: str, limit: int = 20) -> pd.DataFrame:
    """某列的取值分布（按出现次数从多到少）。"""
    with ws.locked():
        info = ws.require(name)
        if column not in info.column_names:
            raise ValueError(f"列「{column}」不在表「{name}」里。可用的列：{'、'.join(info.column_names)}")
        params = {"column": column, "limit": int(limit)}
        cached = cached_observation(info, "value_counts", params)
        if cached is not None:
            return cached
        sql = (
            f"SELECT {q(column)} AS {q(column)}, count(*) AS 次数, "
            f"round(count(*) * 100.0 / sum(count(*)) OVER (), 2) AS 占比 "
            f"FROM {q(name)} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {int(limit)}"
        )
        result = ws.fetch_df(sql)
        save_observation(ws, name, "value_counts", params, result, expected_revision=info.revision)
        return result


def column_stats(ws: Workspace, name: str, columns: list[str] | None = None) -> pd.DataFrame:
    """描述统计（DuckDB SUMMARIZE）：最小、最大、均值、分位数、空值比例等。"""
    with ws.locked():
        info = ws.require(name)
        cols = columns or info.column_names
        missing = [c for c in cols if c not in info.column_names]
        if missing:
            raise ValueError(f"列 {missing} 不在表「{name}」里")
        params = {"columns": cols}
        cached = cached_observation(info, "column_stats", params)
        if cached is not None:
            return cached
        select = ", ".join(q(c) for c in cols)
        df = ws.fetch_df(f"SUMMARIZE SELECT {select} FROM {q(name)}")
        result = df.rename(columns=_STATS_NAMES)
        save_observation(ws, name, "column_stats", params, result, expected_revision=info.revision)
        return result


_STATS_NAMES = {
    "column_name": "列",
    "column_type": "类型",
    "min": "最小",
    "max": "最大",
    "approx_unique": "约不同值",
    "avg": "均值",
    "std": "标准差",
    "q25": "25分位",
    "q50": "中位数",
    "q75": "75分位",
    "count": "行数",
    "null_percentage": "空值比例",
}
