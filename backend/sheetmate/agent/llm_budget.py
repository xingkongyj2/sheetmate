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
            if value.get("type") in {"image_url", "input_image"}:
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

    def snapshot(self) -> dict[str, Any]:
        return {"model_calls": self.calls, "model_calls_limit": self.policy.max_model_calls,
                "accounted_prompt_tokens": self.prompt_tokens, "accounted_total_tokens": self.total_tokens,
                "prompt_tokens_limit": self.policy.max_prompt_tokens, "total_tokens_limit": self.policy.max_total_tokens,
                "estimated_usage_calls": self.estimated_calls, "inflight_calls": len(self.active),
                "reserved_tokens": sum(sum(item) for item in self.active.values()),
                "elapsed_seconds": round(time.monotonic() - self.started, 3),
                "task_seconds_limit": self.policy.max_task_seconds, "input_chars_limit": self.policy.max_input_chars,
                "estimate_method": "utf8_bytes/2 + 4096/image; missing usage reserves requested output"}
