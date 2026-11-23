"""默认词项评分后端：中文 n-gram、业务同义词扩展、长度归一化、时效衰减与 Jaccard 多样性。"""
from __future__ import annotations

from datetime import datetime
from functools import lru_cache
import math
import re
from typing import Any

# 表格/财务/物流高频业务同义词组：跨表达时也能召回同一业务口径
_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("运费", "物流费", "配送费", "快递费", "运价", "资费", "首重", "续重"),
    ("金额", "总额", "合计", "总计", "费用", "价款", "小计", "应收", "应付"),
    ("单号", "运单号", "订单号", "快递单号", "物流单号", "流水号"),
    ("重量", "毛重", "净重", "计费重", "实重", "抛重", "体积重"),
    ("客户", "买家", "收货方", "商户", "商家", "甲方"),
    ("日期", "时间", "月份", "账期", "周期", "结算期"),
    ("口径", "规则", "标准", "算法", "公式", "逻辑", "计费"),
    ("模板", "表头", "格式", "样式", "排版", "总表"),
    ("税率", "含税", "未税", "不含税", "税额", "开票"),
    ("去重", "重复", "唯一", "剔除", "排除"),
    ("匹配", "关联", "对账", "核对", "核销", "勾稽", "映射"),
    ("发货地", "始发地", "起运地", "发件地", "寄件地", "仓"),
    ("收货地", "目的地", "到达地", "收件地", "省", "市"),
)


def _chunks(text: str) -> list[str]:
    text = str(text)
    return [text[i : i + 2] for i in range(max(0, len(text) - 1))]


@lru_cache(maxsize=2048)
def _query_terms(query: str) -> frozenset[str]:
    """Extract lightweight English words and Chinese bigrams/trigrams."""
    text = str(query or "").lower()
    terms = set(re.findall(r"[a-z0-9_]+", text))
    for run in re.findall(r"[\u3400-\u9fff]+", text):
        terms.update(_chunks(run))
        terms.update(run[i:i + 3] for i in range(max(0, len(run) - 2)))
    return frozenset(terms)


@lru_cache(maxsize=2048)
def _synonym_concepts(text: str) -> frozenset[int]:
    lowered = str(text or "").lower()
    if not lowered:
        return frozenset()
    return frozenset(
        index for index, group in enumerate(_SYNONYM_GROUPS)
        if any(word in lowered for word in group)
    )


def _recency_multiplier(item: dict[str, Any]) -> float:
    """Gentle decay (0.85~1.0) for non-human memories; human rules never decay."""
    if item.get("source") == "human":
        return 1.0
    raw = item.get("updated_at") or item.get("created_at")
    if not raw:
        return 1.0
    try:
        dt = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
        age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
        return 0.85 + 0.15 * math.exp(-age_days / 90.0)
    except (ValueError, TypeError):
        return 1.0


def _memory_relevance(item: dict[str, Any], terms: frozenset[str], query: str) -> float:
    if not terms:
        return 0.0
    key_text = str(item.get("key") or "")
    value_text = str(item.get("value") or "")
    key_terms = _query_terms(key_text)
    value_terms = _query_terms(value_text)
    key_hits = len(terms & key_terms)
    value_hits = len(terms & value_terms)
    # 词项得分 + 长度归一化（BM25 风格），防止超长冗余文本仅凭词数淹没精炼规则
    len_norm = 1.0 + 0.5 * (max(0, len(value_text) - 80) / 400.0)
    score = (4.0 * key_hits) + (value_hits / len_norm)
    # 业务同义词概念命中（即使字面不同也能稳定召回）
    query_concepts = _synonym_concepts(query)
    if query_concepts:
        concept_hits = len(query_concepts & _synonym_concepts(f"{key_text} {value_text}"))
        if concept_hits:
            score += 2.5 * concept_hits
    key_lower = key_text.lower()
    if len(key_lower) >= 2 and key_lower in str(query or "").lower():
        score += 20.0
    if score <= 0:
        return 0.0
    return round(score * _recency_multiplier(item), 4)


class LexicalMemoryScorer:
    """Pure default backend; no database, model client or service dependencies."""

    method = "lexical_mmr_recent"
    uses_embeddings = False

    def relevance(self, item: dict[str, Any], query: str) -> float:
        return _memory_relevance(item, _query_terms(query), query)

    def similarity(self, left: dict[str, Any], right: dict[str, Any]) -> float:
        first = _query_terms(str(left.get("key") or "") + " " + str(left.get("value") or ""))
        second = _query_terms(str(right.get("key") or "") + " " + str(right.get("value") or ""))
        combined = first | second
        return len(first & second) / len(combined) if combined else 0
