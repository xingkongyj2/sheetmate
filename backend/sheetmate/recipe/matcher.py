"""重复执行前的输入匹配：新上传的文件 / 工作区里的表，分别对应 Recipe 的哪个输入。

打分 = 期望列的匹配比例（含客户字段映射和模糊匹配）+ 文件名符合规则 + sheet 名相同 + 表名相同。
每个输入取分数最高、且没被别的输入占用的候选。
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any

from sheetmate.engine.field_mapping import match_columns
from sheetmate.recipe.schema import Recipe, RecipeInput


@dataclass
class Candidate:
    key: str  # 表名，或「file_id::sheet」
    columns: list[str]
    filename: str = ""
    sheet: str | None = None
    rows: int = 0
    kind: str = "table"


def score_candidate(inp: RecipeInput, cand: Candidate, mapping: dict[str, list[str]] | None = None) -> float:
    if inp.kind != cand.kind:
        return 0.0
    score = 0.0
    if inp.expected_columns:
        scoped = {k: v for k, v in (mapping or {}).items() if k in inp.expected_columns}
        colmap, _ = match_columns(inp.expected_columns, cand.columns, scoped)
        required = inp.required_columns or inp.expected_columns
        req_hit = sum(1 for c in required if c in colmap) / len(required)
        score += 0.6 * req_hit + 0.4 * (len(colmap) / len(inp.expected_columns))
    if inp.file_pattern and cand.filename and fnmatch.fnmatch(cand.filename, inp.file_pattern + ("" if inp.file_pattern.endswith("*") else "*")):
        score += 0.8 if inp.kind == "file" else 0.3
    if inp.kind == "file" and inp.source_file and cand.filename == inp.source_file:
        score = max(score, 0.9)
    if inp.sheet and cand.sheet == inp.sheet:
        score += 0.1
    if cand.key == inp.alias:
        score += 0.5
    return round(score, 4)


def match_inputs(recipe: Recipe, candidates: list[Candidate], mapping: dict[str, list[str]] | None = None,
                 min_score: float = 0.45) -> dict[str, dict[str, Any]]:
    """返回 {alias: {"key", "score", "filename", "sheet"}}；匹配不到的 alias 不在结果里。"""
    pairs = []
    for inp in recipe.inputs:
        file_pairs = []
        for cand in candidates:
            score = score_candidate(inp, cand, mapping)
            # 没有列可匹配的原文件输入，只有唯一文件时才能安全地自动绑定。
            if (inp.kind == "file" and cand.kind == "file" and not inp.file_pattern
                    and sum(c.kind == "file" for c in candidates) == 1):
                score = max(score, 0.5)
            file_pairs.append((score, inp.alias, cand))
        if inp.kind == "file":
            best = max((score for score, _, _ in file_pairs), default=0)
            if sum(score == best for score, _, _ in file_pairs) > 1:
                # 同类文件有多个时交给用户明确选择，避免根据上传顺序随意绑定。
                continue
        pairs.extend(file_pairs)
    pairs.sort(key=lambda p: p[0], reverse=True)
    result: dict[str, dict[str, Any]] = {}
    used: set[str] = set()
    for score, alias, cand in pairs:
        if alias in result or cand.key in used or score < min_score:
            continue
        result[alias] = {"key": cand.key, "score": score, "filename": cand.filename, "sheet": cand.sheet, "kind": cand.kind}
        used.add(cand.key)
    return result
