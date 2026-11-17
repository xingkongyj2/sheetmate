"""按列识别联系方式和地址：列名 + 内容两路证据，依据写成大白话，直接展示给用户看。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from sheetmate.privacy.address import looks_like_address, split_address
from sheetmate.privacy.rules import (
    EMAIL_RE, KIND_LABELS, LANDLINE_RE, MOBILE_RE, cell_text, header_kind, is_email, is_id_like_header, is_landline,
    is_mobile,
)

ACTIONS = {"phone": "tokenize", "landline": "tokenize", "email": "tokenize", "im": "tokenize",
           "address": "address_detail", "mixed": "cell_scan", "custom": "tokenize"}

# 整列处理的内容门槛：多数单元格符合格式，才不看列名也整列处理
WHOLE_COLUMN_RATE = 0.5
# 列名已经命中时，内容只要有一点符合就算两路证据都有
SUPPORT_RATE = 0.2
MAX_VALUES = 200
# 列名明说是「地址」「门牌」的：一定是详细地址列。「发货地」「收件地」这类可能只写省市，要内容或脱敏模型一起确认
_STRONG_ADDRESS_WORDS = frozenset({"地址", "详细地", "门牌", "address", "addr", "street"})


@dataclass(frozen=True)
class Finding:
    kind: str          # phone / landline / email / im / address / mixed
    source: str        # rules / model / both
    reason: str        # 给用户看的依据
    hit_rate: float = 0.0
    firm: bool = True  # False：只有一个含糊的列名、内容没有佐证。脱敏模型看过样例并判为「不是」时，这个结论不再采用

    @property
    def action(self) -> str:
        return ACTIONS[self.kind]


def _rates(texts: list[str]) -> tuple[dict[str, float], int]:
    counts = {"phone": 0, "landline": 0, "email": 0, "address": 0}
    embedded = 0
    for text in texts:
        if is_mobile(text):
            counts["phone"] += 1
        elif is_landline(text):
            counts["landline"] += 1
        elif is_email(text):
            counts["email"] += 1
        elif looks_like_address(text):
            counts["address"] += 1
        elif MOBILE_RE.search(text) or EMAIL_RE.search(text) or LANDLINE_RE.search(text):
            embedded += 1
    total = len(texts) or 1
    return {kind: count / total for kind, count in counts.items()}, embedded


def region_only_column(name: object, values: Iterable[object]) -> bool:
    """列名只是「发货地」「收货地」这类含糊的说法（不是「地址」「门牌」），内容又都只有省、市：没有要遮的详细地址。"""
    header = header_kind(name)
    if header is None or header[0] != "address" or header[1] in _STRONG_ADDRESS_WORDS:
        return False
    texts = [text for text in (cell_text(value) for value in list(values)[:MAX_VALUES]) if text]
    return bool(texts) and all(not split_address(text)[1].strip() for text in texts)


def classify_column(name: object, values: Iterable[object]) -> Finding | None:
    """只用内置规则判断一列；没有发现联系方式或地址时返回 None。"""
    texts = [text for text in (cell_text(value) for value in list(values)[:MAX_VALUES]) if text]
    rates, embedded = _rates(texts)
    best_kind, best_rate = max(rates.items(), key=lambda item: item[1])
    header = header_kind(name)

    if header is not None:
        kind, word = header
        # 列名只是「发货地」「收货地」这类，内容又只有省、市：没有要遮的详细地址，不用识别、不用标记，直接放过。
        # 列名明说是「地址」的仍然保护：这一列后面的行里可能有详细地址。
        if region_only_column(name, texts):
            return None
        # 列名说是电话、内容多半是座机，就按座机处理（两者令牌一样，只影响显示）
        if kind == "phone" and rates["landline"] > rates["phone"] and rates["landline"] >= SUPPORT_RATE:
            kind = "landline"
        support = rates.get(kind, 0.0)
        reason = f"列名含「{word}」"
        if support >= SUPPORT_RATE:
            reason += f"；内容 {support:.0%} 为{KIND_LABELS[kind]}格式"
        firm = support >= SUPPORT_RATE or not (kind == "address" and word not in _STRONG_ADDRESS_WORDS)
        return Finding(kind, "rules", reason, support, firm)

    # 没有列名线索：内容多数符合才整列处理。订单号、运单号这类长数字列例外，避免误伤。
    if best_rate >= WHOLE_COLUMN_RATE and texts and not (best_kind in {"phone", "landline"} and is_id_like_header(name)):
        return Finding(best_kind, "rules", f"内容 {best_rate:.0%} 为{KIND_LABELS[best_kind]}格式", best_rate)
    hits = embedded + sum(1 for kind in ("phone", "landline", "email", "address") if rates[kind] > 0)
    if hits and not is_id_like_header(name):
        parts = [KIND_LABELS[kind] for kind in ("phone", "landline", "email", "address") if rates[kind] > 0]
        return Finding("mixed", "rules", "部分单元格里含有" + "、".join(parts or ["手机号或邮箱"]), 0.0)
    return None


def plausible(kind: str, texts: list[str]) -> bool:
    """脱敏模型单独判出来的列，样例里要真有这一类东西的样子，防止模型把「姓名」「备注」误判成电话或地址。"""
    if kind in {"phone", "landline"}:
        return any(sum(ch.isdigit() for ch in text) >= 7 for text in texts)
    if kind == "email":
        return any("@" in text or "＠" in text or "at" in text.lower() or "艾特" in text for text in texts)
    if kind == "im":
        return any(len(text) >= 5 and any(ch.isascii() and ch.isalnum() for ch in text) for text in texts)
    if kind == "address":
        return any(len(text) >= 5 and split_address(text)[1].strip() for text in texts)
    return any(len(text) >= 6 for text in texts)


def merge(rules: Finding | None, model: Finding | None, *, reviewed: bool = False, texts: list[str] | None = None) -> Finding | None:
    """合并规则和脱敏模型对一列的结论。

    - 规则有内容佐证（手机号、邮箱、详细地址的格式）：一定处理，模型不能推翻。
    - 规则只靠一个含糊的列名（「发货地」）：模型看过样例说不是，就放过；
    - 规则什么都没发现、模型说是：样例里要真有那一类内容才采用。
    reviewed 表示脱敏模型确实看过这一列（没有配置脱敏模型时不能拿「没结论」当作「不是」）。"""
    if rules is None:
        if model is not None and texts is not None and not plausible(model.kind, texts):
            return None
        return model
    if model is None:
        return None if reviewed and not rules.firm else rules
    kind = rules.kind if rules.kind != "mixed" else model.kind
    return Finding(kind, "both", f"{rules.reason}；脱敏模型也判为{KIND_LABELS[model.kind]}（{model.reason}）", rules.hit_rate)
