"""按列识别联系方式和地址：列名 + 内容两路证据，依据写成大白话，直接展示给用户看。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sheetmate.privacy.address import looks_like_address
from sheetmate.privacy.rules import (
    EMAIL_RE, KIND_LABELS, LANDLINE_RE, MOBILE_RE, cell_text, header_kind, is_email, is_id_like_header, is_landline,
    is_landline_number, is_mobile,
)

ACTIONS = {"phone": "tokenize", "landline": "tokenize", "email": "tokenize", "im": "tokenize",
           "address": "address_detail", "mixed": "cell_scan"}

# 整列处理的内容门槛：多数单元格符合格式，才不看列名也整列处理
WHOLE_COLUMN_RATE = 0.5
# 列名已经命中时，内容只要有一点符合就算两路证据都有
SUPPORT_RATE = 0.2
MAX_VALUES = 200


@dataclass(frozen=True)
class Finding:
    kind: str          # phone / landline / email / im / address / mixed
    source: str        # rules / model / both
    reason: str        # 给用户看的依据
    hit_rate: float = 0.0

    @property
    def action(self) -> str:
        return ACTIONS[self.kind]


def _rates(items: list[tuple[str, object]]) -> tuple[dict[str, float], int]:
    counts = {"phone": 0, "landline": 0, "email": 0, "address": 0}
    embedded = 0
    for text, value in items:
        if is_mobile(text):
            counts["phone"] += 1
        elif is_landline(text) or is_landline_number(value):
            counts["landline"] += 1
        elif is_email(text):
            counts["email"] += 1
        elif looks_like_address(text):
            counts["address"] += 1
        elif MOBILE_RE.search(text) or EMAIL_RE.search(text) or LANDLINE_RE.search(text):
            embedded += 1
    total = len(items) or 1
    return {kind: count / total for kind, count in counts.items()}, embedded


def classify_column(name: object, values: Iterable[object]) -> Finding | None:
    """只用内置规则判断一列；没有发现联系方式或地址时返回 None。"""
    items = [(text, value) for value in list(values)[:MAX_VALUES] if (text := cell_text(value))]
    texts = [text for text, _ in items]
    rates, embedded = _rates(items)
    best_kind, best_rate = max(rates.items(), key=lambda item: item[1])
    header = header_kind(name)

    if header is not None:
        kind, word = header
        # 列名说是电话、内容多半是座机，就按座机处理（两者令牌一样，只影响显示）
        if kind == "phone" and rates["landline"] > rates["phone"] and rates["landline"] >= SUPPORT_RATE:
            kind = "landline"
        support = rates.get(kind, 0.0)
        reason = f"列名含「{word}」"
        if support >= SUPPORT_RATE:
            reason += f"；内容 {support:.0%} 为{KIND_LABELS[kind]}格式"
        return Finding(kind, "rules", reason, support)

    # 没有列名线索：内容多数符合才整列处理。订单号、运单号这类长数字列例外，避免误伤。
    if best_rate >= WHOLE_COLUMN_RATE and texts and not (best_kind in {"phone", "landline"} and is_id_like_header(name)):
        return Finding(best_kind, "rules", f"内容 {best_rate:.0%} 为{KIND_LABELS[best_kind]}格式", best_rate)
    hits = embedded + sum(1 for kind in ("phone", "landline", "email", "address") if rates[kind] > 0)
    if hits and not is_id_like_header(name):
        parts = [KIND_LABELS[kind] for kind in ("phone", "landline", "email", "address") if rates[kind] > 0]
        return Finding("mixed", "rules", "部分单元格里含有" + "、".join(parts or ["手机号或邮箱"]), 0.0)
    return None


def merge(rules: Finding | None, model: Finding | None) -> Finding | None:
    """规则和脱敏模型的结论取并集：任何一方认为敏感就处理。"""
    if rules is None:
        return model
    if model is None:
        return rules
    kind = rules.kind if rules.kind != "mixed" else model.kind
    return Finding(kind, "both", f"{rules.reason}；脱敏模型也判为{KIND_LABELS[model.kind]}（{model.reason}）", rules.hit_rate)
