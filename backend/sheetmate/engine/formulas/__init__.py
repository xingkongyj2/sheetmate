"""公式库：内置公式（全局只读）+ 客户专属公式（scope=customer，同 id 覆盖内置）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sheetmate.engine.formulas.base import (
    BuiltinFormula,
    Formula,
    FormulaError,
    FormulaPlan,
    ParamSpec,
    normalize_params,
    render_template,
    template_inputs,
)
from sheetmate.engine.formulas.builtin import BUILTIN_FORMULAS, referenced_tables
from sheetmate.engine.workspace import Workspace

__all__ = [
    "BUILTIN_FORMULAS",
    "Formula",
    "FormulaError",
    "FormulaLibrary",
    "FormulaPlan",
    "ParamSpec",
    "ResolvedFormula",
    "referenced_tables",
]


@dataclass
class ResolvedFormula:
    """解析后的公式：知道它是内置的还是客户独有的，并能编译成 SQL。"""

    spec: Formula
    builtin: BuiltinFormula | None = None

    @property
    def kind(self) -> str:
        """builtin = 内置公式；customer = 客户独有公式。"""
        return "builtin" if self.builtin is not None else "customer"

    def compile(self, params: dict[str, Any], ws: Workspace) -> tuple[dict[str, Any], FormulaPlan]:
        """校验参数并生成 SQL。返回 (规整后的参数, 编译结果)。"""
        normalized = normalize_params(self.spec, params, ws)
        if self.builtin is not None:
            return normalized, self.builtin.build(normalized, ws)
        sql = render_template(self.spec, normalized)
        return normalized, FormulaPlan(sql=sql, inputs=template_inputs(self.spec, normalized))

    def describe(self, params: dict[str, Any]) -> str:
        """人话描述（展示用，不要求参数完整）。"""
        try:
            if self.builtin is not None:
                return self.builtin.describe(params or {})
        except Exception:
            pass
        return self.spec.intent or self.spec.name


class FormulaLibrary:
    """某个上下文（某客户 / 无客户）下可用的全部公式。查找优先级：客户专属 > 内置。"""

    def __init__(self, customer_formulas: list[Formula] | None = None):
        self._customer: dict[str, Formula] = {}
        for f in customer_formulas or []:
            if f.status == "deprecated":
                continue
            current = self._customer.get(f.id)
            if current is None or f.version > current.version:
                self._customer[f.id] = f

    def resolve(self, formula_id: str) -> ResolvedFormula:
        fid = str(formula_id or "").strip()
        if fid in self._customer:
            return ResolvedFormula(self._customer[fid])
        if fid in BUILTIN_FORMULAS:
            item = BUILTIN_FORMULAS[fid]
            return ResolvedFormula(item.spec, item)
        available = "、".join(self.ids())
        raise FormulaError(f"没有公式「{fid}」。可用公式：{available}")

    def has(self, formula_id: str) -> bool:
        return formula_id in self._customer or formula_id in BUILTIN_FORMULAS

    def ids(self) -> list[str]:
        return list(self._customer) + [i for i in BUILTIN_FORMULAS if i not in self._customer]

    def builtin_specs(self) -> list[Formula]:
        return [item.spec for fid, item in BUILTIN_FORMULAS.items() if fid not in self._customer]

    def customer_specs(self) -> list[Formula]:
        return list(self._customer.values())

    def catalog_text(self) -> str:
        """给 LLM 的公式目录：每个公式一段（id、用途、参数声明）。"""
        lines: list[str] = []
        for spec in self.customer_specs():
            lines.append(_spec_text(spec, "客户独有公式"))
        for spec in self.builtin_specs():
            lines.append(_spec_text(spec, "内置公式"))
        return "\n".join(lines)


def _spec_text(spec: Formula, label: str) -> str:
    params = []
    for name, p in spec.params.items():
        flag = "必填" if p.required else "可选"
        extra = f"，可选值 {p.values}" if p.values else ""
        default = f"，默认 {p.default!r}" if p.default is not None else ""
        params.append(f"    - {name}（{p.type}，{flag}{default}{extra}）：{p.desc}")
    head = f"- `{spec.id}` {spec.name}［{label}·{spec.category}］：{spec.intent}"
    return "\n".join([head, *params])
