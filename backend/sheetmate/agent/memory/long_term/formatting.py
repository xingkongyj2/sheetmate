"""将长期记忆投影为带作用域、来源和版本的上下文。"""
from __future__ import annotations

from typing import Any

from sheetmate.agent.memory.long_term.policy import CATEGORY_LABELS, is_verified_repair_memory


def format_memories(items: list[dict[str, Any]], field_mapping: dict[str, list[str]] | None = None) -> str:
    lines = []
    human = [i for i in items if i.get("source") == "human"]
    auxiliary = [i for i in items if i.get("source") != "human" and i.get("category") != "history_insights"]
    experiences = [i for i in items if i.get("source") != "human" and i.get("category") == "history_insights"]
    if human:
        lines.append("用户维护的记忆（最高优先级）：处理本流程时必须遵守；与 AI 参考冲突时以这些内容为准，AI 不得改写。")
        for item in human:
            text = item['value'] if item['key'].startswith("note_") else f"{item['key']}：{item['value']}"
            lines.append(f"- [{item.get('scope_label', '')}；{_memory_reference(item)}] {text}")
    if auxiliary:
        lines.append("AI 参考记忆（辅助参考，不得覆盖用户维护的记忆）：")
        lines.extend(f"- [{CATEGORY_LABELS.get(i['category'], i['category'])}·{i.get('scope_label', '')}；{_memory_reference(i)}] {i['key']}：{i['value']}"
                     for i in auxiliary)
    if experiences:
        lines.append("历史修复经验（仅技术参考）：先核对本轮文件、表、字段和工具结果，再判断是否适用；"
                     "不得覆盖用户要求、人工规则或 Recipe，不得把旧数据或一次性处理变成业务规则。")
        for item in experiences:
            proof = "已验证执行" if is_verified_repair_memory(item) else "未核验旧记录"
            lines.append(f"- [{item.get('scope_label', '')}；{_memory_reference(item)}；{proof}] {item['key']}：{item['value']}")
    if field_mapping:
        lines.append("- [字段映射] " + "；".join(f"{std} ← {'/'.join(aliases)}" for std, aliases in field_mapping.items()))
    return "\n".join(lines)


def _memory_reference(item: dict[str, Any]) -> str:
    reference = [f"memory_id={item.get('id', 'legacy')}", f"version={item.get('version') or 1}",
                 f"source={item.get('source') or 'ai'}"]
    if item.get("source_message_id") is not None:
        reference.append(f"source_message_id={item['source_message_id']}")
    return "；".join(reference)
