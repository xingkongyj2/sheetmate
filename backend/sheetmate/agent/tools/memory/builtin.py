"""memory / builtin tool capabilities."""

from __future__ import annotations

from typing import Any
from sheetmate.agent.tools.contracts import AgentContext, ToolResult


def _tool_remember(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    category = str(args.get("category") or "domain_knowledge")
    key, value = str(args.get("key") or "").strip(), str(args.get("value") or "").strip()
    if not key or not value:
        raise ValueError("key 和 value 都不能为空")
    receipt = ctx.host.remember(category, key, value, evidence_span=str(args.get("evidence_span") or ""))
    if not isinstance(receipt, dict):
        return ToolResult("记忆保存未返回可验证的写入结果，本轮不能确认已保存。", ok=False,
                          summary="记忆写入结果未确认")
    status = receipt.get("status")
    scope = str(receipt.get("scope_label") or "记忆")
    actual_key, actual_value = str(receipt.get("key") or key), str(receipt.get("value") or "")
    if status == "needs_evidence":
        return ToolResult("记忆未保存：" + str(receipt.get("reason") or "需要可核实的用户原话证据。"),
                          ok=False, summary=f"记忆证据不足：{actual_key}")
    if status == "blocked":
        return ToolResult(f"未更新（{scope}）：人工规则受到保护。当前保存的是 {actual_key} = {actual_value}。",
                          summary=f"未覆盖人工记忆：{actual_key}")
    if status == "unchanged":
        return ToolResult(f"记忆未变更（{scope}）：{actual_key} = {actual_value}。", summary=f"记忆未变更：{actual_key}")
    if status not in {"created", "updated"}:
        return ToolResult("记忆保存未返回可验证的写入状态，本轮不能确认已保存。", ok=False,
                          summary="记忆写入结果未确认")
    label = "已记住" if status == "created" else "已更新记忆"
    return ToolResult(f"{label}（{scope}）：{actual_key} = {actual_value}", summary=f"{label}：{actual_key}",
                      events=[{"type": "memory_saved", "items": [receipt], "scope": scope, "status": status}])


def _tool_save_field_mapping(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    standard = str(args.get("standard") or "").strip()
    aliases = [str(a).strip() for a in (args.get("aliases") or []) if str(a).strip()]
    if not standard or not aliases:
        raise ValueError("standard 和 aliases 都不能为空")
    msg = ctx.host.save_field_mapping(standard, aliases)
    return ToolResult(msg, summary=f"字段映射：{standard} ← {'、'.join(aliases)}",
                      events=[{"type": "field_mapping_saved", "standard": standard, "aliases": aliases}])
