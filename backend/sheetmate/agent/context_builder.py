"""Soft, recoverable projections for long-running model requests.

Completed old tool exchanges can be replaced as a whole with evidence-backed
progress records. Original instructions, history and execution evidence are
never changed. A request that cannot safely shrink is still allowed through.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from sheetmate.agent.llm_budget import request_size
from sheetmate.agent.tool_results import result_reference

_EVIDENCE = re.compile(r"^\[evidence_id=(ev_[a-zA-Z0-9_]+)\]\n")
_RECEIPT = re.compile(
    r"^\[execution_receipt status=(success|failed) executed=(true|false) reused=(true|false)\]\n"
)
_RECENT_TOOL_RESULTS = 4

# This is a soft projection target, independent of hard request admission.
DEFAULT_PROJECTION_CHARS = 80_000


def _over_target(kwargs: dict[str, Any], max_chars: int, max_tokens: int) -> bool:
    chars, tokens = request_size(kwargs)
    return bool((max_chars > 0 and chars > max_chars * 0.8)
                or (max_tokens > 0 and tokens > max_tokens * 0.8))


def _callable_tool(kwargs: dict[str, Any], name: str) -> dict[str, Any] | None:
    choice = kwargs.get("tool_choice")
    if choice == "none":
        return None
    if isinstance(choice, dict) and (choice.get("function") or {}).get("name") != name:
        return None
    for tool in kwargs.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        function = tool.get("function") or {}
        if function.get("name") == name:
            return function
    return None


def _args_retrievable(kwargs: dict[str, Any]) -> bool:
    tool = _callable_tool(kwargs, "inspect_execution_evidence")
    if tool is None:
        return False
    sections = ((tool.get("parameters") or {}).get("properties") or {}).get("section") or {}
    return "args" in sections.get("enum", [])


@dataclass(frozen=True)
class _Exchange:
    start: int
    end: int
    calls: tuple[dict[str, Any], ...]
    replies: tuple[dict[str, Any], ...]


def _recent_tool_indexes(messages: list[dict[str, Any]]) -> set[int]:
    """Keep recent parallel result batches intact, even when they exceed four."""
    tool_indexes = [index for index, item in enumerate(messages) if item.get("role") == "tool"]
    recent = set(tool_indexes[-_RECENT_TOOL_RESULTS:])
    protected: set[int] = set()
    start = 0
    while start < len(messages):
        if messages[start].get("role") != "tool":
            start += 1
            continue
        end = start + 1
        while end < len(messages) and messages[end].get("role") == "tool":
            end += 1
        if any(index in recent for index in range(start, end)):
            protected.update(range(start, end))
        start = end
    return protected


def _old_exchanges(messages: list[dict[str, Any]]) -> list[_Exchange]:
    """Find complete contiguous exchanges, retaining recent parallel groups."""
    protected = _recent_tool_indexes(messages)
    exchanges = []
    for start, message in enumerate(messages):
        calls = message.get("tool_calls")
        if message.get("role") != "assistant" or not isinstance(calls, list) or not calls:
            continue
        if any(not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]
               or call.get("type") != "function" or not isinstance(call.get("function"), dict)
               or not isinstance(call["function"].get("name"), str) for call in calls):
            continue
        call_ids = {call["id"] for call in calls}
        if len(call_ids) != len(calls):
            continue
        end = start + 1
        while end < len(messages) and messages[end].get("role") == "tool":
            end += 1
        if end == start + 1 or any(index in protected for index in range(start + 1, end)):
            continue
        replies = messages[start + 1:end]
        reply_ids = [item.get("tool_call_id") for item in replies]
        if (len(reply_ids) != len(calls) or any(not isinstance(value, str) for value in reply_ids)
                or set(reply_ids) != call_ids):
            continue
        references = [_EVIDENCE.match(item.get("content", ""))
                      if isinstance(item.get("content"), str) else None for item in replies]
        if any(reference is None for reference in references):
            continue
        if len({reference.group(1) for reference in references}) != len(references):
            continue
        if message.get("content") is not None and not isinstance(message.get("content"), str):
            continue
        exchanges.append(_Exchange(start, end, tuple(calls), tuple(replies)))
    return exchanges


def _execution_status(content: str) -> str:
    reference = _EVIDENCE.match(content)
    receipt = _RECEIPT.match(content[reference.end():]) if reference else None
    if receipt is None:
        return "已有返回；执行状态须回查"
    status, executed, reused = receipt.groups()
    if reused == "true":
        if executed != "false":
            return "回执状态需回查"
        return "复用已有失败结果；本次未重新执行" if status == "failed" else "复用已有结果；本次未重新执行"
    if executed == "false":
        return "本次未执行"
    return "执行成功；任务验收另行核对" if status == "success" else "执行失败"


def _progress_message(assistant: dict[str, Any], exchange: _Exchange) -> dict[str, Any]:
    replies = {item["tool_call_id"]: item for item in exchange.replies}
    lines = ["【旧工具交互进度：完整参数及结果已留存】"]
    if assistant.get("content"):
        lines.extend(["此前助手说明（不作为完成依据）：", assistant["content"]])
    for call in exchange.calls:
        content = replies[call["id"]]["content"]
        reference = _EVIDENCE.match(content)
        record = {"tool": call["function"]["name"], "call_id": call["id"],
                  "evidence_id": reference.group(1), "state": _execution_status(content)}
        output_id = result_reference(content)
        if output_id:
            record["tool_result_id"] = output_id
        lines.append(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    lines.append(
        "需要旧 SQL、脚本参数或精确结果时，按 evidence_id 调用 inspect_execution_evidence："
        "section='args' 回查参数，section='text' 回查结果，section='facts' 回查状态与源表版本；"
        "按 offset/limit 分页。参数若以 script_id 引用代码，先从结果取得 run_id，"
        "再调用 inspect_python_script(run_id) 查看原代码。"
        "不能从省略内容推断数值、业务规则或任务已完成。"
    )
    message = {"role": "assistant", "content": "\n".join(lines)}
    if "reasoning_content" in assistant:
        message["reasoning_content"] = ""
    return message


def _smaller(before: dict[str, Any], after: dict[str, Any]) -> bool:
    old_chars, old_tokens = request_size(before)
    chars, tokens = request_size(after)
    return chars < old_chars and tokens <= old_tokens


def prepare_context(kwargs: dict[str, Any], max_chars: int, *, max_tokens: int = 0) -> dict[str, Any]:
    """Project recoverable old work; both targets are soft input budgets.

    ``max_tokens`` has already had output headroom reserved by the caller.
    It is a provider-neutral estimate, not a claim of exact tokenization.
    """
    if not _over_target(kwargs, max_chars, max_tokens):
        return kwargs
    messages = kwargs.get("messages") or []
    projected = [dict(item) for item in messages]
    changed = False
    if _args_retrievable(kwargs):
        replacements: dict[int, tuple[int, dict[str, Any]]] = {}
        for exchange in _old_exchanges(messages):
            replacements[exchange.start] = (exchange.end, _progress_message(messages[exchange.start], exchange))
            candidate_messages = []
            index = 0
            while index < len(messages):
                if index in replacements:
                    end, note = replacements[index]
                    candidate_messages.append(note)
                    index = end
                else:
                    candidate_messages.append(messages[index])
                    index += 1
            candidate = {**kwargs, "messages": candidate_messages}
            before = {**kwargs, "messages": projected}
            if not _smaller(before, candidate):
                replacements.pop(exchange.start)
                continue
            projected, changed = candidate_messages, True
            if not _over_target(candidate, max_chars, max_tokens):
                return candidate

    # Legacy exchanges without recoverable arguments keep their original
    # pairing. Only the result body can shrink, if a lookup tool is exposed.
    protected = _recent_tool_indexes(projected)
    candidates = [index for index, item in enumerate(projected)
                  if index not in protected and item.get("role") == "tool"
                  and isinstance(item.get("content"), str)]
    for index in candidates:
        content = projected[index]["content"]
        reference = _EVIDENCE.match(content)
        if not reference or len(content) < 1200:
            continue
        evidence_id, output_id = reference.group(1), result_reference(content)
        if output_id and _callable_tool(kwargs, "read_tool_result"):
            lookup = (f"read_tool_result(result_id='{output_id}', "
                      "section='text', offset=0, limit=4000)")
        elif _callable_tool(kwargs, "inspect_execution_evidence"):
            lookup = f"inspect_execution_evidence(evidence_id='{evidence_id}', offset=0, limit=4000)"
        else:
            continue
        head = content[reference.end():reference.end() + 700]
        item = {**projected[index], "content": (
            f"[evidence_id={evidence_id}]\n{head}\n"
            "[旧工具结果的详细内容未重复注入；上面仅为原文片段，不能据此推断省略数据。"
            f"涉及具体数值或验收时，调用 {lookup} 分页查看完整依据。]"
        )}
        candidate_messages = [*projected[:index], item, *projected[index + 1:]]
        candidate = {**kwargs, "messages": candidate_messages}
        if not _smaller({**kwargs, "messages": projected}, candidate):
            continue
        projected, changed = candidate_messages, True
        if not _over_target(candidate, max_chars, max_tokens):
            break
    return {**kwargs, "messages": projected} if changed else kwargs
