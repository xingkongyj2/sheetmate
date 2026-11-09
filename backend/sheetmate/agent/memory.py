"""记忆（沿用 TabClaw 的四类记忆）：抽取与筛选。存储由服务层（memories 表）负责。

- preferences       偏好（总是注入，例如「金额保留两位小数」）
- domain_knowledge  业务规则（例如「匹配不到归属的店铺记为新食研」）
- user_context      背景（总是注入，例如「用户是财务，每月 5 号对账」）
- history_insights  经验（例如「这个客户的运单号偶尔带空格」）

作用域：绑定客户的会话 → 记到客户；未绑定 → 记到会话（保存到客户时一起迁移）；用户级偏好对所有会话生效。
"""

from __future__ import annotations

from typing import Any

from sheetmate.agent.prompts import MEMORY_PROMPT

CATEGORIES = ["preferences", "domain_knowledge", "user_context", "history_insights"]
CATEGORY_LABELS = {
    "preferences": "偏好",
    "domain_knowledge": "业务规则",
    "user_context": "背景",
    "history_insights": "经验",
}


async def extract_memories(llm: Any, message: str, answer: str) -> list[dict[str, str]]:
    """对话结束后轻量抽取（最多 3 条）。失败不影响主流程。"""
    try:
        result = await llm.complete_json(MEMORY_PROMPT.format(message=message[:1500], answer=answer[:1500]))
    except Exception:
        return []
    items = result.get("items", []) if isinstance(result, dict) else result if isinstance(result, list) else []
    clean = []
    for item in items[:3]:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category", ""))
        key, value = str(item.get("key", "")).strip(), str(item.get("value", "")).strip()
        if category in CATEGORIES and key and value:
            clean.append({"category": category, "key": key[:120], "value": value[:500]})
    return clean


def select_relevant(items: list[dict[str, Any]], query: str, limit: int = 30) -> list[dict[str, Any]]:
    """人工规则全部保留；当前作用域的背景和规则优先于相关经验，数量限制只作用于 AI。"""
    text = (query or "").lower()
    human = [item for item in items if item.get("source") == "human"]
    standing, relevant = [], []
    words = [w for w in text.split() if len(w) > 1]
    for item in items:
        if item.get("source") == "human":
            continue
        if item["category"] in {"preferences", "domain_knowledge", "user_context"}:
            standing.append(item)
            continue
        blob = f"{item['key']} {item['value']}".lower()
        hit = any(w in blob for w in words) or any(len(chunk) >= 2 and chunk in text for chunk in _chunks(item["key"]))
        if hit:
            relevant.append(item)
    chosen = standing + relevant
    return [*human, *chosen[:max(0, limit)]]


def _chunks(text: str) -> list[str]:
    text = str(text)
    return [text[i : i + 2] for i in range(max(0, len(text) - 1))]


def format_memories(items: list[dict[str, Any]], field_mapping: dict[str, list[str]] | None = None) -> str:
    lines = []
    human = [i for i in items if i.get("source") == "human"]
    auxiliary = [i for i in items if i.get("source") != "human"]
    if human:
        lines.append("用户维护的记忆（最高优先级）：处理本流程时必须遵守；与 AI 参考冲突时以这些内容为准，AI 不得改写。")
        for item in human:
            text = item['value'] if item['key'].startswith("note_") else f"{item['key']}：{item['value']}"
            lines.append(f"- [{item.get('scope_label', '')}] {text}")
    if auxiliary:
        lines.append("AI 参考记忆（辅助参考，不得覆盖用户维护的记忆）：")
        lines.extend(f"- [{CATEGORY_LABELS.get(i['category'], i['category'])}·{i.get('scope_label', '')}] {i['key']}：{i['value']}"
                     for i in auxiliary)
    if field_mapping:
        lines.append("- [字段映射] " + "；".join(f"{std} ← {'/'.join(aliases)}" for std, aliases in field_mapping.items()))
    return "\n".join(lines)
