"""只读分析：查看表结构、列统计、值分布。不产生新表，供 Agent 理解数据。"""

from __future__ import annotations

from typing import Any

import pandas as pd

from sheetmate.engine.workbooks.profile import detect_keys, profile_columns
from sheetmate.engine.tables.sql import frame_rows, frame_to_text, quote_ident as q
from sheetmate.engine.tables.memory import cached_observation, cached_profile, save_observation, save_profile
from sheetmate.engine.tables.workspace import Workspace


def _require_complete_data(info: Any, columns: list[str] | None = None) -> None:
    quality = info.read_quality or (cached_profile(info) or {}).get("quality") or {}
    if quality.get("truncated"):
        raise ValueError(
            f"表「{info.name}」只载入 {quality.get('read_rows', '?')} 个物理行，"
            f"原表约 {quality.get('physical_rows', '?')} 行。不能将部分数据统计为全表结果；"
            "请先从原始文件分块读取完整数据后再统计。预览和结构检查仍可使用。"
        )
    targets = set(columns or info.column_names)
    for issue, present, label in (
        ("missing_formula_cache", quality.get("missing_formula_cache"), "公式缺少计算缓存"),
        ("formula_error", quality.get("formula_error_cells"), "公式计算结果包含 Excel 错误"),
        ("error", quality.get("error_cells"), "包含 Excel 错误单元格"),
    ):
        if not present:
            continue
        fields = quality.get(f"{issue}_fields")
        # Older metadata may only identify physical column positions. Recover
        # names when possible; otherwise avoid treating unknown errors as nulls.
        if fields is None and f"{issue}_columns" in quality:
            positions = (info.header or {}).get("col_index")
            if positions is not None:
                affected = set(quality[f"{issue}_columns"])
                fields = [name for name, position in zip(info.column_names, positions) if position in affected]
        affected_fields = targets if fields is None else targets.intersection(fields)
        if affected_fields:
            raise ValueError(
                f"表「{info.name}」的列「{'、'.join(sorted(affected_fields))}」{label}，"
                "不能将其当作普通空值或零参与统计。请先核对原始文件并重算公式或修复错误后再读取；"
                "预览和结构检查仍可使用。"
            )


def table_info(ws: Workspace, name: str, sample_rows: int = 5) -> dict[str, Any]:
    """表结构 + 每列画像 + 前几行样例（TabClaw 的 table_info，改为基于 DuckDB）。"""
    with ws.locked():
        info = ws.require(name)
        profile = cached_profile(info)
        if profile is None:
            df = ws.df(name)
            columns = profile_columns(df)
            profile = {"columns": columns, "keys": detect_keys(df, columns), "quality": dict(info.read_quality or {}),
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
            "read_quality": dict(info.read_quality or profile.get("quality") or {}),
            "full_data": not bool((info.read_quality or profile.get("quality") or {}).get("truncated")),
        }


def table_info_text(data: dict[str, Any]) -> str:
    lines = [f"表「{data['name']}」（{data['label']}）：{data['rows']} 行 × {data['cols']} 列"]
    quality = data.get("read_quality") or {}
    if quality.get("truncated"):
        lines.append("读取不完整：下面画像仅基于已载入部分，不能当作全表统计；请先从原件分块读取完整数据。")
    if quality.get("missing_formula_cache"):
        lines.append(f"读取风险：{quality['missing_formula_cache']} 个公式缺计算缓存，不能当作正常空白或零。")
    if quality.get("formula_error_cells") or quality.get("error_cells"):
        lines.append("读取风险：存在 Excel 错误单元格，请核对原始公式或错误值后计算。")
    if quality.get("footer_rows"):
        from sheetmate.engine.tables.memory import footer_notice

        lines.append("说明：" + footer_notice(quality["footer_rows"]))
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
    limit = int(limit)
    if not 1 <= limit <= 10_000:
        raise ValueError("分布 limit 必须在 1 到 10000 之间；请缩小范围或使用聚合查询。")
    with ws.locked():
        info = ws.require(name)
        if column not in info.column_names:
            raise ValueError(f"列「{column}」不在表「{name}」里。可用的列：{'、'.join(info.column_names)}")
        _require_complete_data(info, [column])
        params = {"column": column, "limit": limit}
        cached = cached_observation(info, "value_counts", params)
        if cached is not None and cached.attrs.get("distribution"):
            return cached
        category_alias = "__sheetmate_total_categories"
        row_alias = "__sheetmate_total_rows"
        while category_alias in info.column_names:
            category_alias += "_"
        while row_alias in info.column_names:
            row_alias += "_"
        count_alias = "出现次数" if column == "次数" else "次数"
        ratio_alias = "占比（%）" if column == "占比" else "占比"
        sql = (
            f"SELECT {q(column)} AS {q(column)}, count(*) AS {q(count_alias)}, "
            f"round(count(*) * 100.0 / sum(count(*)) OVER (), 2) AS {q(ratio_alias)}, "
            f"count(*) OVER () AS {q(category_alias)}, sum(count(*)) OVER () AS {q(row_alias)} "
            f"FROM {q(name)} GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {limit}"
        )
        result = ws.fetch_df(sql)
        total_categories = int(result[category_alias].iloc[0]) if len(result) else 0
        total_rows = int(result[row_alias].iloc[0]) if len(result) else 0
        result = result.drop(columns=[category_alias, row_alias])
        covered_rows = int(result.iloc[:, 1].sum())
        result.attrs["distribution"] = {
            "total_categories": total_categories, "total_rows": total_rows,
            "returned_categories": len(result), "omitted_categories": total_categories - len(result),
            "covered_rows": covered_rows, "coverage": covered_rows / total_rows if total_rows else 0.0,
        }
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
        _require_complete_data(info, cols)
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
