"""单步执行：把一个公式（带参数）在工作区里跑一次，结果存成输出表。

这是引擎闭环的「执行」环节，Agent 工具调用和 Recipe 每一步都走这里：
校验参数 → 编译成 SQL → DuckDB 执行（CREATE TABLE AS SELECT）→ 统计行列变化 / 匹配率 / 警告 → 预览。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

from sheetmate.engine.analysis import _require_complete_data
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.sqlutil import frame_rows
from sheetmate.engine.workspace import TableInfo, Workspace

PREVIEW_ROWS = 20


@dataclass
class StepOutcome:
    """一步执行的结果（会出现在右侧「执行明细」和 Recipe 运行报告里）。"""

    formula: str
    formula_kind: str  # builtin / customer
    output: str
    params: dict[str, Any]
    sql: str
    inputs: list[str]
    rows_in: dict[str, int]
    rows_out: int
    columns_out: list[str]
    description: str
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    duration_ms: int = 0
    preview: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        """一句话结果（回传给 LLM 的 tool_result 里用）。"""
        rows_in = "、".join(f"{t} {n} 行" for t, n in self.rows_in.items())
        text = f"已生成表「{self.output}」：{self.rows_out} 行 × {len(self.columns_out)} 列（输入：{rows_in}）。列：{self.columns_out}"
        if self.metrics.get("match_rate") is not None:
            m = self.metrics
            text += f"。匹配 {m['matched_rows']}/{m['total_rows']} 行，匹配率 {m['match_rate']:.1%}"
        if self.warnings:
            text += "。注意：" + "；".join(self.warnings)
        return text


def apply_formula(
    ws: Workspace,
    library: FormulaLibrary,
    formula_id: str,
    params: dict[str, Any],
    output: str,
    *,
    label: str | None = None,
    created_by: str = "",
    description: str = "",
) -> StepOutcome:
    """执行一个公式，结果写入 output 表（同名覆盖）。参数或 SQL 有问题时抛 ValueError，信息可直接给 AI 修复。"""
    started = time.perf_counter()
    resolved = library.resolve(formula_id)
    with ws.locked():
        normalized, plan = resolved.compile(params, ws)
        # Every formula entry point must preserve the same completeness guard.
        # In particular, a rename must not turn partial/error data into a new
        # apparently clean table that can later be aggregated without warning.
        for source in dict.fromkeys(plan.inputs):
            _require_complete_data(ws.require(source))
        rows_in = {t: ws.row_count(t) for t in dict.fromkeys(plan.inputs)}
        info: TableInfo = ws.create_table_as(
            output,
            plan.sql,
            label=label or output,
            source="computed",
            created_by=created_by or formula_id,
            description=description or resolved.describe(normalized),
        )
    warnings = list(plan.warnings)
    metrics = dict(plan.notes.get("metrics", {}))
    if plan.post is not None:
        extra = plan.post(ws, output) or {}
        warnings.extend(extra.get("warnings", []))
        metrics.update(extra.get("metrics", {}))
    notes = {k: v for k, v in plan.notes.items() if k != "metrics"}
    preview_df = ws.df(output, limit=PREVIEW_ROWS)
    return StepOutcome(
        formula=resolved.spec.id,
        formula_kind=resolved.kind,
        output=output,
        params=normalized,
        sql=plan.sql,
        inputs=list(dict.fromkeys(plan.inputs)),
        rows_in=rows_in,
        rows_out=info.rows,
        columns_out=info.column_names,
        description=description or resolved.describe(normalized),
        metrics=metrics,
        notes=notes,
        warnings=warnings,
        duration_ms=int((time.perf_counter() - started) * 1000),
        preview={"columns": info.column_names, "rows": frame_rows(preview_df), "total": info.rows},
    )
