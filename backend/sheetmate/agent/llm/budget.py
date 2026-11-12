"""Task-wide request admission and deadline accounting, including nested calls.

Reported usage is kept separately by LLMClient. Admission uses estimates when
provider usage is unavailable; these estimates are deliberately visible.
"""

from __future__ import annotations

import json
import math
import time
from typing import Any


def request_size(kwargs: dict[str, Any]) -> tuple[int, int]:
    images = 0

    def text_only(value: Any) -> Any:
        nonlocal images
        if isinstance(value, dict):
            # JSON Schema 里的 "type" 可以是列表（如 ["string", "null"]），列表不可哈希，不能直接做集合成员判断。
            if isinstance(value.get("type"), str) and value["type"] in {"image_url", "input_image"}:
                images += 1
                return {"type": "image", "detail": (value.get("image_url") or {}).get("detail", "auto") if isinstance(value.get("image_url"), dict) else "auto"}
            return {key: text_only(item) for key, item in value.items()}
        if isinstance(value, list):
            return [text_only(item) for item in value]
        return value

    payload = {key: kwargs[key] for key in ("messages", "tools", "response_format") if key in kwargs}
    text = json.dumps(text_only(payload), ensure_ascii=False, separators=(",", ":"), default=str)
    # No provider-neutral tokenizer exists. UTF-8/2 is a conservative heuristic
    # for normal prose/schema, not a claim of exact tokenizer or image usage.
    return len(text), math.ceil(len(text.encode("utf-8")) / 2) + images * 4096


def context_profile(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Describe request size by context source without retaining prompt text.

    The estimate intentionally shares ``request_size``'s provider-neutral
    heuristic. Image payload bytes are replaced by a marker and counted as a
    fixed allowance so this profile is safe to emit into diagnostics.
    """
    def measure(value: Any) -> dict[str, int]:
        images = 0

        def omit_image_bytes(item: Any) -> Any:
            nonlocal images
            if isinstance(item, dict):
                if isinstance(item.get("type"), str) and item["type"] in {"image_url", "input_image"}:
                    images += 1
                    detail = (item.get("image_url") or {}).get("detail", "auto") \
                        if isinstance(item.get("image_url"), dict) else "auto"
                    return {"type": "image", "detail": detail}
                return {key: omit_image_bytes(child) for key, child in item.items()}
            if isinstance(item, list):
                return [omit_image_bytes(child) for child in item]
            return item

        serialized = json.dumps(omit_image_bytes(value), ensure_ascii=False,
                                separators=(",", ":"), default=str)
        encoded = len(serialized.encode("utf-8"))
        return {"serialized_chars": len(serialized),
                "estimated_tokens": math.ceil(encoded / 2) + images * 4096,
                "image_parts": images}

    messages = kwargs.get("messages") or []
    by_role: dict[str, dict[str, int]] = {}
    for message in messages:
        role = str(message.get("role") or "unknown") if isinstance(message, dict) else "unknown"
        totals = by_role.setdefault(role, {"messages": 0, "serialized_chars": 0,
                                           "estimated_tokens": 0, "image_parts": 0})
        item = measure(message)
        totals["messages"] += 1
        for key, value in item.items():
            totals[key] += value

    tools = kwargs.get("tools") or []
    tool_profile = measure(tools)
    format_present = "response_format" in kwargs
    format_profile = measure(kwargs.get("response_format")) if format_present else {
        "serialized_chars": 0, "estimated_tokens": 0, "image_parts": 0,
    }
    total_chars, total_tokens = request_size(kwargs)
    return {
        "estimate_method": "serialized UTF-8 bytes / 2 plus 4096 tokens per image; heuristic, not provider tokenizer",
        "request": {"serialized_chars": total_chars, "estimated_tokens": total_tokens},
        "output_budget_tokens": int(kwargs.get("max_tokens") or 0),
        "messages": {"count": len(messages), "by_role": by_role},
        "tool_schema": {"count": len(tools) if isinstance(tools, list) else 0, **tool_profile},
        "output_schema": {"present": format_present, **format_profile},
    }


class BudgetExceeded(RuntimeError):
    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class RequestBudget:
    def __init__(self, policy: Any):
        self.policy = policy
        self.started = time.monotonic()
        self.calls = 0
        self.prompt_tokens = 0
        self.total_tokens = 0
        self.estimated_calls = 0
        self.active: dict[str, tuple[int, int]] = {}
        self.attempted: set[str] = set()

    def remaining_seconds(self) -> float | None:
        if not self.policy.budget_enabled:
            return None
        limit = self.policy.max_task_seconds
        if not limit:
            return None
        remaining = limit - (time.monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExceeded("本次任务已达到总时长上限，已停止后续模型调用。", "task_timeout")
        return remaining

    def reserve(self, call_id: str, kwargs: dict[str, Any]) -> None:
        self.remaining_seconds()
        chars, prompt = request_size(kwargs)
        policy = self.policy
        output = int(kwargs.get("max_tokens") or 8192)
        pending_prompt = sum(item[0] for item in self.active.values())
        pending_total = sum(sum(item) for item in self.active.values())
        if policy.budget_enabled:
            if policy.max_input_chars and chars > policy.max_input_chars:
                raise BudgetExceeded("本轮上下文超过单次输入上限，请缩小处理范围或精简历史后继续。", "input_budget")
            if policy.max_model_calls and self.calls >= policy.max_model_calls:
                raise BudgetExceeded("本次任务已达到模型调用次数上限，已保留当前结果。", "model_call_budget")
            if policy.max_prompt_tokens and self.prompt_tokens + pending_prompt + prompt > policy.max_prompt_tokens:
                raise BudgetExceeded("本次任务已达到输入 Token 预算，已保留当前结果。", "prompt_token_budget")
            if policy.max_total_tokens and self.total_tokens + pending_total + prompt + output > policy.max_total_tokens:
                raise BudgetExceeded("本次任务的剩余 Token 预算不足以安全发起下一次调用。", "token_budget")
        self.calls += 1
        self.attempted.add(call_id)
        self.active[call_id] = (prompt, output)

    def finish(self, call_id: str, usage: Any) -> None:
        reservation = self.active.pop(call_id, None)
        if reservation is None:
            return
        if usage is None:
            prompt, output = reservation
            self.estimated_calls += 1
        else:
            prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
            output = int(getattr(usage, "completion_tokens", 0) or 0)
        self.prompt_tokens += prompt
        self.total_tokens += prompt + output

    def export_state(self) -> dict[str, Any]:
        """Serialize accounting only; prompt contents and policy never enter checkpoints."""
        return {"version": 1, "calls": self.calls, "prompt_tokens": self.prompt_tokens,
                "total_tokens": self.total_tokens, "estimated_calls": self.estimated_calls,
                "active": {call_id: list(reservation) for call_id, reservation in self.active.items()},
                "attempted": sorted(self.attempted),
                "elapsed_seconds": max(0.0, time.monotonic() - self.started)}

    def restore_state(self, state: dict[str, Any]) -> None:
        """Restore consumed limits, charging lost in-flight calls their reserved maximum.

        Monotonic clocks cannot survive a process restart. Only active execution
        time is restored here; checkpoint expiry belongs to the recovery owner.
        Validate before mutation so a broken checkpoint cannot buy a fresh budget.
        """
        if not isinstance(state, dict) or type(state.get("version")) is not int or state["version"] != 1:
            raise ValueError("不支持的模型预算检查点版本")

        def count(value: Any) -> int:
            if type(value) is not int or value < 0:
                raise ValueError("模型预算检查点包含无效计数")
            return value

        calls = count(state.get("calls"))
        prompt_tokens = count(state.get("prompt_tokens"))
        total_tokens = count(state.get("total_tokens"))
        estimated_calls = count(state.get("estimated_calls"))
        elapsed = state.get("elapsed_seconds")
        if type(elapsed) not in {int, float} or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("模型预算检查点包含无效耗时")
        active, attempted = state.get("active"), state.get("attempted")
        if (not isinstance(active, dict) or not isinstance(attempted, list)
                or any(not isinstance(identifier, str) or not identifier for identifier in attempted)):
            raise ValueError("模型预算检查点包含无效调用记录")
        reservations: dict[str, tuple[int, int]] = {}
        for identifier, reservation in active.items():
            if (not isinstance(identifier, str) or not identifier
                    or not isinstance(reservation, (list, tuple)) or len(reservation) != 2):
                raise ValueError("模型预算检查点包含无效在途预留")
            reservations[identifier] = (count(reservation[0]), count(reservation[1]))
        attempted_ids = set(attempted)
        if (total_tokens < prompt_tokens or calls < len(attempted_ids)
                or estimated_calls + len(reservations) > calls
                or not reservations.keys() <= attempted_ids):
            raise ValueError("模型预算检查点计数不一致")

        self.calls = calls
        self.prompt_tokens = prompt_tokens + sum(item[0] for item in reservations.values())
        self.total_tokens = total_tokens + sum(sum(item) for item in reservations.values())
        self.estimated_calls = estimated_calls + len(reservations)
        self.active = {}
        self.attempted = attempted_ids
        self.started = time.monotonic() - elapsed

    def snapshot(self) -> dict[str, Any]:
        enabled = self.policy.budget_enabled
        return {"budget_enabled": enabled,
                "model_calls": self.calls, "model_calls_limit": self.policy.max_model_calls if enabled else 0,
                "accounted_prompt_tokens": self.prompt_tokens, "accounted_total_tokens": self.total_tokens,
                "prompt_tokens_limit": self.policy.max_prompt_tokens if enabled else 0,
                "total_tokens_limit": self.policy.max_total_tokens if enabled else 0,
                "estimated_usage_calls": self.estimated_calls, "inflight_calls": len(self.active),
                "reserved_tokens": sum(sum(item) for item in self.active.values()),
                "elapsed_seconds": round(time.monotonic() - self.started, 3),
                "task_seconds_limit": self.policy.max_task_seconds if enabled else 0,
                "input_chars_limit": self.policy.max_input_chars if enabled else 0,
                "estimate_method": "utf8_bytes/2 + 4096/image; missing usage reserves requested output"}
