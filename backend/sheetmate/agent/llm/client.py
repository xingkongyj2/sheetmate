"""LLM 客户端（沿用 TabClaw 的 LLMClient，补充 JSON 输出、错误翻译和用量统计）。

OpenAI 兼容接口，默认连接 DeepSeek（deepseek-v4-pro 或 deepseek-flash）。
模型服务配置由设置页分别保存，并按固定优先级选择当前可用服务。
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import logging
import math
import os
import random
import re
import time
import uuid
import weakref
from collections.abc import AsyncIterator, Callable
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

import openai
import httpx
from openai import AsyncOpenAI

from sheetmate.agent.llm.content import user_content
from sheetmate.agent.memory.short_term.tool_context import prepare_context
from sheetmate.agent.llm.budget import BudgetExceeded, RequestBudget, context_profile, request_size
from sheetmate.agent.llm.cache import reported_cache_usage, request_prefix_profile, stable_request
from sheetmate.agent.llm.structured import StructuredOutputError, complete_structured, validate_payload
from sheetmate.agent.llm.protocol import LanguageModel
from sheetmate.agent.framework.shared.watchdog import Phase, WatchdogTimeout, phase, wait_owned
from sheetmate.core.config.settings import (
    AUTO_MAX_TOKENS, LLMSettings, get_llm_settings, get_privacy_llm_settings, is_deepseek_url,
)
from sheetmate.core.observability.conversation_log import record_event


log = logging.getLogger(__name__)
_LLM_STAGE: contextvars.ContextVar[str | None] = contextvars.ContextVar("sheetmate_llm_stage", default=None)
# 辅助结构化调用遇到截断后扩大额度的上限。
_UNCAPPED_GROWTH_LIMIT = 32768
# 预算预留按这个常见输出量估算，不按 AUTO_MAX_TOKENS 占满。
_AUTO_RESERVE_TOKENS = 8192
# 按（接口地址, 模型）记住服务端实际接受的输出上限；0 表示该模型不接受或无需传 max_tokens。
_ACCEPTED_MAX_TOKENS: dict[tuple[str, str], int] = {}
# 按（接口地址, 模型）记住明确不支持 stream_options={"include_usage": True} 的兼容端点。
_UNSUPPORTED_STREAM_OPTIONS: set[tuple[str, str]] = set()
_STREAM_OPTIONS_ERROR = re.compile(r"stream_options|include_usage", re.I)
# 按事件循环复用 AsyncOpenAI 连接池，避免每次请求重做 TCP/TLS 握手和泄漏未关闭的 httpx.AsyncClient。
_CLIENT_POOL: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, OrderedDict[tuple[str, str, float], AsyncOpenAI]] = weakref.WeakKeyDictionary()
_CLIENT_POOL_LIMIT = 16


def _pooled_openai_client(cfg: LLMSettings) -> AsyncOpenAI:
    """Return a shared AsyncOpenAI client bound to the current event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=cfg.timeout, max_retries=0)
    pool = _CLIENT_POOL.get(loop)
    if pool is None:
        pool = OrderedDict()
        _CLIENT_POOL[loop] = pool
    key = (cfg.base_url.rstrip("/"), hashlib.sha256(cfg.api_key.encode()).hexdigest(), float(cfg.timeout))
    existing = pool.get(key)
    if existing is not None:
        pool.move_to_end(key)
        return existing
    created = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=cfg.timeout, max_retries=0)
    pool[key] = created
    while len(pool) > _CLIENT_POOL_LIMIT:
        _, evicted = pool.popitem(last=False)
        loop.create_task(evicted.close())
    return created


async def close_llm_clients() -> None:
    """Close all pooled AsyncOpenAI clients bound to the current event loop."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    pool = _CLIENT_POOL.pop(loop, None)
    if not pool:
        return
    await asyncio.gather(*(client.close() for client in pool.values()), return_exceptions=True)


def _retry_delay(exc: BaseException, attempt: int) -> float:
    """Honor Retry-After header when present; otherwise bounded exponential backoff with jitter."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        raw = headers.get("retry-after") or headers.get("Retry-After")
        if raw is not None:
            try:
                seconds = float(str(raw).strip())
                if math.isfinite(seconds) and seconds >= 0:
                    return min(30.0, seconds)
            except ValueError:
                pass
    base = min(8.0, 0.25 * (2 ** attempt))
    return base * random.uniform(0.5, 1.0)


def _auxiliary_model_override() -> str:
    return os.environ.get("LLM_AUXILIARY_MODEL", "").strip()
_MAX_TOKENS_ERROR = re.compile(r"max_(?:completion_)?tokens|output tokens|completion tokens|maximum context|context length|上下文", re.I)
_MAX_TOKENS_PARAM = re.compile(r"max_(?:completion_)?tokens", re.I)
_CONTEXT_BOUND = re.compile(r"context|input|prompt|上下文|输入", re.I)
_NUM = r"(\d[\d,]*)"
_CONTEXT_LENGTH = re.compile(rf"(?:context length|context window|上下文(?:长度|窗口)?)\D{{0,12}}{_NUM}", re.I)
_INPUT_TOKENS = [re.compile(pattern, re.I) for pattern in (
    rf"{_NUM}\s*tokens?\s*from the (?:input|prompt|messages)",
    rf"(?:has|contains|with)\s*{_NUM}\s*(?:input|prompt)\s*tokens?",
    rf"{_NUM}\s*(?:input|prompt)\s*tokens?",
    rf"(?:input|prompt)\s*tokens?\D{{0,12}}{_NUM}",
    rf"输入\D{{0,6}}{_NUM}",
)]


def _number(match: re.Match[str] | None) -> int:
    return int(match.group(1).replace(",", "")) if match else 0


def _parse_window(message: str) -> int:
    """报错里说出的上下文窗口（Token）；说不出返回 0。"""
    size = _number(_CONTEXT_LENGTH.search(message))
    return size if size >= 1024 else 0


def _find_input_tokens(message: str) -> int:
    """报错里说出的输入 Token 数；说不出返回 0。"""
    for pattern in _INPUT_TOKENS:
        found = pattern.search(message)
        if found:
            return _number(found)
    return 0


def _context_headroom(message: str) -> int:
    """报错同时给出上下文窗口和输入 Token 数时，算出输出还能用多少（留 2% 余量）；读不出返回 0。"""
    size, used = _parse_window(message), _find_input_tokens(message)
    if not size or not used or used >= size:
        return 0
    return max(0, size - used - max(256, size // 50))


_REQUESTED_OUTPUT = re.compile(rf"{_NUM}\s*tokens?\s*for the (?:completion|output)", re.I)
_REQUESTED_TOTAL = re.compile(rf"total of {_NUM}\s*tokens", re.I)
_OVERFLOW_WORDS = re.compile(r"exceed|too (?:large|long)|reduce|however|超出|超过", re.I)


def context_overflow_message(message: str) -> str | None:
    """模型服务说「输入 + 输出超过上下文窗口」时，写成使用者看得懂的话：窗口多大、输入多少、请求输出多少，再附服务端原文。
    不是这类报错返回 None。刻意不含「设置」二字：前端看到它会给「去设置」链接，而窗口不在设置页里改。"""
    window = _parse_window(message)
    if not window or not _OVERFLOW_WORDS.search(message):
        return None
    used, output, total = _find_input_tokens(message), _number(_REQUESTED_OUTPUT.search(message)), _number(_REQUESTED_TOTAL.search(message))
    facts = [f"模型窗口 {window:,} Token"]
    if used:
        facts.append(f"本次输入 {used:,}")
    if output:
        facts.append(f"请求输出 {output:,}")
    if total:
        facts.append(f"合计 {total:,}")
    if used and used >= window:
        advice = "仅输入就已超出窗口：请减少一次处理的文件或 sheet 数量，或把大表拆开分次处理。"
    else:
        advice = "输入加输出超出窗口：请减少一次处理的文件或 sheet 数量，或换用窗口更大的模型。"
    return f"输入内容超出了模型的上下文窗口，本次请求没有执行。{'，'.join(facts)}。{advice}服务端原文：{_short(message, 600)}"


def lower_max_tokens(kwargs: dict[str, Any], message: str, key: tuple[str, str]) -> bool:
    """服务端因 max_tokens 过大返回 400 时，按报错里的真实上限降级；解析不出就不再传。

    返回是否改动了请求。只缓存明确针对 max_tokens 参数、且与本次输入长度无关的结论。"""
    current = int(kwargs.get("max_tokens") or 0)
    if current <= 0 or not _MAX_TOKENS_ERROR.search(message):
        return False
    if _parse_window(message) and _find_input_tokens(message) and _context_headroom(message) < 256:
        return False        # 输入本身就占满窗口：降低输出没用，也不能按报错里别的数字乱猜，直接把报错交给使用者
    accepted = _context_headroom(message)
    if 256 <= accepted < current:
        # 上下文窗口减输入后的剩余额度；与本次输入长度有关，不缓存为模型固定上限。
        kwargs["max_tokens"] = accepted
        return True
    numbers = [int(item.replace(",", "")) for item in re.findall(r"\d[\d,]*", message)]
    candidates = [number for number in numbers if 256 <= number < current]
    accepted = max(candidates) if candidates else 0
    if accepted:
        kwargs["max_tokens"] = accepted
    else:
        kwargs.pop("max_tokens", None)
    if _MAX_TOKENS_PARAM.search(message) and (not accepted or not _CONTEXT_BOUND.search(message)):
        _ACCEPTED_MAX_TOKENS[key] = accepted
    return True
_AUXILIARY_JSON_STAGES = frozenset({
    "request_intent", "recipe_persistence", "title", "memory_extraction", "history_compaction", "customer_summary",
    "execution_plan", "plan_review", "plan_repair", "step_verdict", "answer_audit", "task_completion", "final_delivery",
})


@contextmanager
def llm_stage(stage: str):
    """Attribute nested and concurrent LLM calls without mutable client state."""
    token = _LLM_STAGE.set(stage)
    try:
        yield
    finally:
        _LLM_STAGE.reset(token)


class LLMError(RuntimeError):
    """调用模型失败。message 是给用户看的中文说明。"""

    code = "llm_error"

    def __init__(self, message: str, *, code: str | None = None, finish_reason: str | None = None):
        super().__init__(message)
        self.code = code or type(self).code
        self.finish_reason = finish_reason


class LLMTimeout(LLMError):
    """A request/deadline timeout, preserved for auxiliary-stage degradation."""

    code = "timeout"


class LLMNotConfigured(LLMError):
    code = "llm_not_configured"
    message = "还没有启用可用的模型。请在「设置」里填写并保存模型的 API Key，并开启「启用」。"

    def __init__(self) -> None:
        super().__init__(self.message)


_LEGACY_LLM_NOT_CONFIGURED_MESSAGES = (
    "还没有配置模型的 API Key。请在「设置」里填写并保存 API Key。",
    "还没有配置模型的 API Key。请在「设置」里填写 DeepSeek API Key（或在 .env 中设置 DEEPSEEK_API_KEY）。",
)


def contains_llm_not_configured_text(text: str | None) -> bool:
    """Recognize current and legacy setup failures embedded in stored text."""
    value = text or ""
    return LLMNotConfigured.code in value or any(
        message in value for message in (LLMNotConfigured.message, *_LEGACY_LLM_NOT_CONFIGURED_MESSAGES)
    )


def is_llm_not_configured_message(role: str, content: str | None,
                                  events: list[dict[str, Any]] | None = None) -> bool:
    """Recognize setup failures saved by current and legacy versions."""
    if role != "assistant":
        return False
    messages = (LLMNotConfigured.message, *_LEGACY_LLM_NOT_CONFIGURED_MESSAGES)
    return (content or "").strip() in {f"⚠️ {message}" for message in messages} or any(
        event.get("type") == "error" and (
            event.get("code") == LLMNotConfigured.code or event.get("content") in messages
        ) for event in events or []
    )


def translate_error(exc: Exception) -> LLMError:
    """把 SDK 异常翻译成用户看得懂的话。"""
    if isinstance(exc, LLMError):
        return exc
    if isinstance(exc, openai.AuthenticationError):
        return LLMError("API Key 无效或已过期，请到「设置」里检查。", code="authentication")
    if isinstance(exc, openai.PermissionDeniedError):
        return LLMError("这个 API Key 没有权限调用当前模型。", code="permission_denied")
    if isinstance(exc, openai.RateLimitError):
        return LLMError("模型服务限流或额度不足，请稍后再试或检查账户余额。", code="rate_limit")
    if isinstance(exc, (openai.APITimeoutError, httpx.TimeoutException, TimeoutError)):
        return LLMTimeout("模型响应超时，请稍后再试。")
    if isinstance(exc, (openai.APIConnectionError, httpx.TransportError, ConnectionError)):
        return LLMError("连接不上模型服务，请检查网络或「设置」里的接口地址。", code="connection_error")
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code in {400, 422} and re.search(
            r"(?:image|vision|multimodal).{0,100}(?:not supported|unsupported|not support)|"
            r"(?:not supported|unsupported|not support).{0,100}(?:image|vision|multimodal)|"
            r"content.{0,100}expected.{0,30}string.{0,60}(?:array|list)",
            str(exc.message), re.IGNORECASE,
        ):
            return LLMError("当前模型或接口不支持图片输入，请在「设置」中切换为支持图片的模型后重试。", code="unsupported_input")
        if exc.status_code in {400, 422}:
            overflow = context_overflow_message(str(exc.message))
            if overflow:
                return LLMError(overflow, code="context_overflow")
        if exc.status_code == 402:
            return LLMError("模型账户余额不足，请充值后再试。", code="quota_exceeded")
        if exc.status_code == 404:
            return LLMError("模型名称或接口地址不对，请到「设置」里检查。", code="model_not_found")
        return LLMError(f"模型服务返回错误（{exc.status_code}）：{_short(exc.message)}", code="api_error")
    return LLMError(f"调用模型出错：{_short(str(exc))}")


def _short(text: str, limit: int = 300) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"JSON 字段重复：{key[:80]}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> Any:
    raise ValueError(f"JSON 不支持 {value}")


def _json_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("JSON 数值超出有效范围")
    return result


def _without_trailing_commas(text: str) -> str:
    """Remove syntax commas only; SQL, scripts and all string contents stay intact."""
    result: list[str] = []
    quoted = escaped = False
    for index, char in enumerate(text):
        if quoted:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            result.append(char)
        elif char == ",":
            following = index + 1
            while following < len(text) and text[following].isspace():
                following += 1
            if following >= len(text) or text[following] not in "}]":
                result.append(char)
        else:
            result.append(char)
    return "".join(result)


def _json_boundary(content: str, start: int) -> int:
    stack: list[str] = []
    quoted = escaped = False
    for index in range(start, len(content)):
        char = content[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or (stack.pop(), char) not in {("{", "}"), ("[", "]")}:
                raise ValueError("JSON 括号不匹配")
            if not stack:
                return index + 1
    raise ValueError("JSON 输出不完整，请返回完整对象")


def parse_json(text: str, *, allow_text: bool = True) -> Any:
    """Parse JSON without modifying string values. Tool arguments forbid prose."""
    content = (text or "").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", content)
    if fenced:
        content = fenced.group(1).strip()
    decoder = json.JSONDecoder(object_pairs_hook=_json_object, parse_constant=_invalid_constant, parse_float=_json_float)
    try:
        return decoder.decode(content)
    except json.JSONDecodeError:
        pass
    repaired = _without_trailing_commas(content)
    try:
        return decoder.decode(repaired)
    except json.JSONDecodeError:
        if not allow_text:
            raise ValueError("工具参数必须是完整 JSON，不得包含说明文字或截断内容") from None
    starts = [i for i in (content.find("{"), content.find("[")) if i >= 0]
    if not starts:
        raise ValueError("返回内容里没有 JSON")
    start = min(starts)
    end = _json_boundary(content, start)
    value = decoder.decode(_without_trailing_commas(content[start:end]))
    # Multiple objects are ambiguous; never choose a business decision silently.
    if any(char in "{[" for char in content[end:]):
        raise ValueError("返回内容包含多个 JSON 或多余括号，请只返回一个对象")
    return value


_FORMAT_CACHE: OrderedDict[tuple[str, ...], tuple[str, float]] = OrderedDict()
_FORMAT_CACHE_LIMIT = 256
_FORMAT_CACHE_TTL = 1800.0


def _format_cache_key(cfg: LLMSettings) -> tuple[str, ...]:
    return (cfg.base_url.rstrip("/"), cfg.model, cfg.provider_id, cfg.capabilities.structured_output,
            hashlib.sha256(cfg.api_key.encode()).hexdigest(),
            cfg.capabilities.thinking_protocol, str(cfg.thinking_enabled),
            json.dumps(cfg.extra_body, sort_keys=True, ensure_ascii=False, default=str))


def _cache_format(key: tuple[str, ...], value: str) -> None:
    _FORMAT_CACHE[key] = (value, time.monotonic())
    _FORMAT_CACHE.move_to_end(key)
    while len(_FORMAT_CACHE) > _FORMAT_CACHE_LIMIT:
        _FORMAT_CACHE.popitem(last=False)


def _cached_format(key: tuple[str, ...]) -> str | None:
    entry = _FORMAT_CACHE.get(key)
    if entry is None:
        return None
    value, timestamp = entry
    if time.monotonic() - timestamp >= _FORMAT_CACHE_TTL:
        _FORMAT_CACHE.pop(key, None)
        return None
    return value


def _unsupported_format(exc: LLMError) -> bool:
    """Only a definite protocol rejection can change a cached capability."""
    cause = exc.__cause__
    if not isinstance(cause, openai.APIStatusError) or cause.status_code not in {400, 422, 501}:
        return False
    message = str(cause.message).lower()
    if re.search(r"invalid schema|schema validation|schema keyword|keyword.{0,80}(?:unsupported|not supported)", message):
        return False  # A bad contract must not permanently disable a working protocol.
    target = r"(?:response_format(?:\.type)?|json_schema|json_object)"
    unsupported = r"(?:not supported|unsupported|not implemented|not support(?:ed)?)"
    punctuation = r"[\s\"'`:=,-]*"
    return bool(re.search(
        rf"{target}{punctuation}(?:(?:is|are)\s+)?{unsupported}\b|"
        rf"\b(?:does not support|not support|unsupported|unknown parameter|unknown field|"
        rf"unrecognized parameter|unexpected keyword(?: argument)?){punctuation}(?:the\s+)?{punctuation}{target}\b|"
        rf"{target}{punctuation}extra inputs are not permitted", message))


def _complete_tool_stream(calls: dict[int, dict[str, str]], tools: list[dict[str, Any]] | None,
                          tool_choice: str | None) -> bool:
    """An omitted terminal SSE field can be repaired only for complete calls.

    A normal EOF is not evidence that arbitrary answer text is complete. Tool
    calls have an inspectable boundary: offered name, unique identity and one
    fully parsed JSON object. Execution still performs its ordinary schema and
    authorization checks; this never repairs an interrupted transport.
    """
    if not calls or tool_choice == "none":
        return False
    offered = {(item.get("function") or {}).get("name") for item in tools or []
               if isinstance(item, dict) and item.get("type") == "function"}
    identifiers = set()
    for call in calls.values():
        identifier, name = call.get("id", ""), call.get("name", "")
        if not identifier.strip() or identifier in identifiers or name not in offered:
            return False
        identifiers.add(identifier)
        try:
            arguments = parse_json(call.get("arguments", ""), allow_text=False)
        except (ValueError, TypeError, RecursionError):
            return False
        if not isinstance(arguments, dict):
            return False
    return True


class LLMClient:
    """异步客户端。chat / stream_chat 与 TabClaw 一致，另加 complete_json。"""

    def __init__(self, cfg: LLMSettings, *, pooled: bool = True):
        if not cfg.configured:
            raise LLMNotConfigured()
        self.cfg = cfg
        self.model = cfg.model
        self._pooled = pooled
        # Retry here so every network attempt consumes the same task budget.
        self._override_client: Any = (
            None if pooled else AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=cfg.timeout, max_retries=0)
        )
        self.budget = RequestBudget(cfg.runtime_policy)
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}
        self.usage_by_stage: dict[str, dict[str, int]] = {}
        self.usage_calls: list[dict[str, Any]] = []
        self._cache_prefixes: dict[tuple[str, str], dict[str, Any]] = {}
        self._projection_cutoffs: dict[tuple[str, str], dict[str, int]] = {}

    @property
    def client(self) -> Any:
        if self._override_client is not None:
            return self._override_client
        return _pooled_openai_client(self.cfg)

    @client.setter
    def client(self, value: Any) -> None:
        self._override_client = value

    def _accepted_key(self, model: str | None = None) -> tuple[str, str]:
        return (self.cfg.base_url.rstrip("/"), model or self.model)

    def _auto_output_tokens(self, model: str | None = None) -> int:
        """请求的输出 Token：先试最大值，服务端拒绝后用它报错里给出的真实上限（0 表示不传），并按模型记住。"""
        return _ACCEPTED_MAX_TOKENS.get(self._accepted_key(model), AUTO_MAX_TOKENS)

    def _output_reserve(self, kwargs: dict[str, Any]) -> int:
        """预算与上下文预留使用的输出量：自动最大值只按常见输出量预留，避免占满窗口和任务预算。"""
        requested = int(kwargs.get("max_tokens") or _AUTO_RESERVE_TOKENS)
        if requested == self._auto_output_tokens(kwargs.get("model")):
            return min(requested, _AUTO_RESERVE_TOKENS)
        return requested

    def _kwargs(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, **extra: Any) -> dict[str, Any]:
        context_metrics = extra.pop("_context_metrics", None)
        auxiliary = extra.pop("_auxiliary", False)
        stage_key = extra.pop("_stage_key", None) or _LLM_STAGE.get() or "execution"
        aux_model = _auxiliary_model_override() if auxiliary else ""
        active_model = aux_model or self.model
        extra_body = {**self.cfg.extra_body, **(extra.pop("extra_body", None) or {})}
        protocol = self.cfg.resolve_thinking_protocol(extra_body)
        qwen = protocol == "qwen"
        deepseek_thinking = protocol == "deepseek"
        legacy_chat = (self.cfg.capabilities.thinking_protocol == "auto" and protocol == "none" and active_model == "deepseek-chat"
                       and is_deepseek_url(self.cfg.base_url))
        if tools and deepseek_thinking:
            # 历史摘要没有原始思考字段；新模型携带 tools 时仍要求该字段存在。
            messages = [{"reasoning_content": "", **message} if message.get("role") == "assistant" else message
                        for message in messages]
        elif legacy_chat:
            # 从思考模型切换回 Chat 时，历史中的思考字段不属于它的请求格式。
            messages = [{key: value for key, value in message.items() if key != "reasoning_content"}
                        for message in messages]
        temperature = extra.pop("temperature", None)
        kwargs: dict[str, Any] = {
            "model": active_model,
            "messages": messages,
            "temperature": self.cfg.temperature if temperature is None else temperature,
        }
        output_tokens = self._auto_output_tokens(active_model)
        if output_tokens > 0:
            kwargs["max_tokens"] = output_tokens
        # An explicitly selected dialect controls both the primary switch and
        # auxiliary calls. Unknown/disabled protocols leave custom body fields
        # untouched; field names alone must never override explicit `none`.
        thinking_enabled = self.cfg.thinking_enabled and (
            not auxiliary or self.cfg.runtime_policy.auxiliary_thinking_enabled)
        if qwen:
            extra_body.pop("thinking", None)
            extra_body["enable_thinking"] = thinking_enabled
            template = extra_body.get("chat_template_kwargs")
            if isinstance(template, dict) and "enable_thinking" in template:
                extra_body["chat_template_kwargs"] = {**template, "enable_thinking": thinking_enabled}
        elif deepseek_thinking:
            extra_body.pop("enable_thinking", None)
            configured_thinking = extra_body.get("thinking")
            extra_body["thinking"] = {
                **(configured_thinking if isinstance(configured_thinking, dict) else {}),
                "type": "enabled" if thinking_enabled else "disabled",
            }
        if legacy_chat:
            # 兼容曾保存的额外参数和单次请求覆盖，Chat 不接收思考模式参数。
            for name in ("thinking", "reasoning_effort"):
                extra_body.pop(name, None)
                extra.pop(name, None)
        if extra_body:
            kwargs["extra_body"] = extra_body
        if tools:
            kwargs["tools"] = tools
        kwargs.update({k: v for k, v in extra.items() if v is not None})
        policy = self.cfg.runtime_policy
        projection_chars = policy.context_target_chars
        if policy.budget_enabled and policy.max_input_chars:
            projection_chars = (min(projection_chars, policy.max_input_chars) if projection_chars
                                else policy.max_input_chars)
        # 不预设模型窗口：只按字符目标整理较早的执行过程，超出窗口时由模型服务报错，报错原文会展示给使用者。
        cutoff = self._projection_cutoffs.setdefault((stage_key, active_model), {})
        prepared = prepare_context(kwargs, projection_chars, sticky_cutoff=cutoff)
        if context_metrics is not None:
            after_profile = context_profile(prepared)
            after_chars = after_profile["request"]["serialized_chars"]
            after_tokens = after_profile["request"]["estimated_tokens"]
            if prepared is kwargs:
                before_chars, before_tokens = after_chars, after_tokens
            else:
                before_chars, before_tokens = request_size(kwargs)
            context_metrics.update(after_profile)
            context_metrics["projection"] = {
                "applied": after_chars < before_chars,
                "target_chars": projection_chars,
                "before_serialized_chars": before_chars,
                "after_serialized_chars": after_chars,
                "saved_serialized_chars": max(0, before_chars - after_chars),
                "before_estimated_tokens": before_tokens,
                "after_estimated_tokens": after_tokens,
                "saved_estimated_tokens": max(0, before_tokens - after_tokens),
            }
        return stable_request(prepared)

    def _profile_cache_prefix(self, kwargs: dict[str, Any], stage: str) -> dict[str, Any]:
        # Stages can run concurrently. Compare only the preceding prepared
        # request in this stage/model, never use another stage's mutable prompt.
        key = (stage, str(kwargs.get("model") or self.model))
        profile, snapshot = request_prefix_profile(kwargs, self._cache_prefixes.get(key))
        self._cache_prefixes[key] = snapshot
        return profile

    async def _within_deadline(self, request: Any, *, streaming: bool = False, retrying: bool = False,
                               wait_phase: Phase | None = None) -> Any:
        try:
            remaining = self.budget.remaining_seconds()
        except BudgetExceeded as exc:
            if hasattr(request, "close"):
                request.close()
            raise LLMTimeout(str(exc), code=exc.code) from exc
        policy = self.cfg.runtime_policy
        owner_limit = policy.stream_idle_seconds if streaming else policy.llm_hard_idle_seconds
        hard = min(owner_limit, remaining) if remaining is not None else owner_limit
        code = ("task_timeout" if remaining is not None and remaining <= owner_limit else
                "stream_idle_timeout" if streaming else "llm_idle_timeout")
        name = "retrying_llm" if retrying else "streaming_llm" if streaming else "awaiting_llm"
        try:
            with phase(name, _LLM_STAGE.get() or "execution", state=wait_phase) as item:
                return await wait_owned(request, item=item, hard_seconds=hard,
                                        soft_seconds=0 if retrying else policy.llm_soft_idle_seconds,
                                        code=code, grace=policy.cancellation_grace_seconds)
        except WatchdogTimeout as exc:
            message = ("本次任务已达到总时长上限，已停止模型调用。" if code == "task_timeout" else str(exc))
            raise LLMTimeout(message, code=exc.code) from exc

    async def _create(self, kwargs: dict[str, Any], call_id: str, stage: str, *,
                      _request_size: tuple[int, int] | None = None) -> Any:
        # 只有「自动最大值」才允许根据服务端报错降级；调用方明确指定的值和用户开启的上限原样使用。
        model_name = kwargs.get("model") or self.model
        auto_sent = bool(kwargs.get("max_tokens")) and kwargs["max_tokens"] == self._auto_output_tokens(model_name)
        for attempt in range(self.cfg.runtime_policy.sdk_retries + 1):
            try:
                self.budget.reserve(call_id, kwargs, self._output_reserve(kwargs), size=_request_size)
            except BudgetExceeded as exc:
                cls = LLMTimeout if exc.code == "task_timeout" else LLMError
                raise cls(str(exc), code=exc.code) from exc
            started = time.monotonic()
            try:
                return await self._within_deadline(self.client.chat.completions.create(**kwargs))
            except BaseException as exc:
                self.budget.finish(call_id, None)
                if ("stream_options" in kwargs and isinstance(exc, openai.APIStatusError)
                        and exc.status_code in {400, 422} and _STREAM_OPTIONS_ERROR.search(str(exc.message))):
                    _UNSUPPORTED_STREAM_OPTIONS.add(self._accepted_key(model_name))
                    kwargs.pop("stream_options", None)
                    log.info("模型 %s 不支持 stream_options.include_usage，已自动回退重试", model_name)
                    return await self._create(kwargs, call_id, stage)
                if (auto_sent and isinstance(exc, openai.APIStatusError) and exc.status_code in {400, 422}
                        and lower_max_tokens(kwargs, str(exc.message), self._accepted_key(model_name))):
                    log.info("模型 %s 不接受 max_tokens=%s，已改为 %s 重试", model_name, AUTO_MAX_TOKENS,
                             kwargs.get("max_tokens", "不传"))
                    return await self._create(kwargs, call_id, stage)
                retryable = (isinstance(exc, (openai.APIConnectionError, openai.APITimeoutError))
                             or isinstance(exc, openai.APIStatusError) and
                             (exc.status_code in {408, 409, 429} or exc.status_code >= 500))
                will_retry = retryable and attempt < self.cfg.runtime_policy.sdk_retries
                self._record_usage(stage, f"{call_id}:attempt{attempt + 1}", None,
                                   "retry" if will_retry else "cancelled" if isinstance(exc, asyncio.CancelledError) else "error",
                                   round((time.monotonic() - started) * 1000), model=model_name)
                if not retryable or attempt >= self.cfg.runtime_policy.sdk_retries:
                    raise
                await self._within_deadline(asyncio.sleep(_retry_delay(exc, attempt)), retrying=True)

    def _add_usage(self, usage: Any) -> None:
        if usage is None:
            return
        self.usage["prompt_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.usage["completion_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)

    def _record_usage(self, stage: str, call_id: str, usage: Any, status: str, duration_ms: int, *,
                      model: str | None = None, response_model: str | None = None) -> None:
        """Record reported usage, keeping unavailable usage distinct from zero."""
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        cache = reported_cache_usage(usage)
        reasoning = int(getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", 0) or 0)
        totals = self.usage_by_stage.setdefault(stage, {"calls": 0, "usage_missing_calls": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "duration_ms": 0})
        values = {"prompt_tokens": prompt, "completion_tokens": completion,
                  "cached_tokens": cache["cached_tokens"], "cache_miss_tokens": cache["cache_miss_tokens"],
                  "cached_tokens_missing_calls": int(not cache["cached_tokens_available"]),
                  "cache_miss_tokens_missing_calls": int(not cache["cache_miss_tokens_available"]),
                  "reasoning_tokens": reasoning, "duration_ms": duration_ms}
        # Restored checkpoints can predate cache coverage counters. Their
        # absent cache fields remain unknown rather than becoming reported zero.
        for key in ("cached_tokens_missing_calls", "cache_miss_tokens_missing_calls"):
            totals.setdefault(key, totals["calls"])
        totals["calls"] += 1
        totals["usage_missing_calls"] += int(usage is None)
        for key, value in values.items():
            totals[key] = totals.get(key, 0) + value
        self.usage_calls.append({"call_id": call_id, "stage": stage, "status": status,
                                 "model": model or self.model, "provider_id": self.cfg.provider_id,
                                 "response_model": response_model,
                                 "usage_available": usage is not None, **values, **cache})

    def usage_snapshot(self) -> dict[str, Any]:
        """Read-only task cost snapshot; missing provider usage is explicit."""
        cache_totals = {key: sum(int(call.get(key, 0)) for call in self.usage_calls)
                        for key in ("cached_tokens", "cache_miss_tokens")}
        # A restored/lost call has no usage_calls entry. Charge its missing
        # report explicitly; active calls remain separately visible in budget.
        unrecorded = max(0, self.budget.calls - len(self.usage_calls) - len(self.budget.active))
        cache_missing = {f"{key}_missing_calls": unrecorded + sum(not call.get(f"{key}_available", False)
                                                                 for call in self.usage_calls)
                         for key in ("cached_tokens", "cache_miss_tokens")}
        return {**self.usage, **cache_totals, **cache_missing,
                "model_calls": self.budget.calls, "budget": self.budget.snapshot(),
                "usage_missing_calls": unrecorded + sum(not call["usage_available"] for call in self.usage_calls),
                "stages": {stage: dict(values) for stage, values in self.usage_by_stage.items()}}

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **extra: Any) -> Any:
        """非流式，返回 message 对象（有 content / tool_calls）。"""
        function = "sheetmate.agent.llm.client.LLMClient.chat"
        call_id = uuid.uuid4().hex
        stage = extra.pop("_stage", None) or _LLM_STAGE.get() or "chat"
        started = time.monotonic()
        status = "error"
        response = None
        failure = None
        request_model = self.model
        try:
            context_metrics: dict[str, Any] = {}
            kwargs = self._kwargs(messages, tools, _context_metrics=context_metrics, _stage_key=stage, **extra)
            request_model = kwargs["model"]
            context_metrics["cache_prefix"] = self._profile_cache_prefix(kwargs, stage)
            record_event("llm.context", function, context_metrics, call_id=call_id, stage=stage)
            record_event("llm.request", function, kwargs, call_id=call_id)
            req_meta = context_metrics.get("request")
            req_size = (req_meta["serialized_chars"], req_meta["estimated_tokens"]) if req_meta else None
            resp = await self._create(kwargs, call_id, stage, _request_size=req_size)
            response = resp
            self._add_usage(resp.usage)
            if not resp.choices:
                raise LLMError("模型服务返回了空响应，请检查接口兼容性后重试。")
            message = resp.choices[0].message
            # Keep request-local metadata on this response, not mutable client state:
            # the same LLMClient can serve concurrent agent requests.
            object.__setattr__(message, "_sheetmate_finish_reason", resp.choices[0].finish_reason)
            status = "completed"
            return message
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as exc:
            status = "error"
            error = translate_error(exc)
            failure = {"type": type(exc).__name__, "code": error.code, "message": str(error)}
            raise error from exc
        finally:
            usage = getattr(response, "usage", None)
            admitted = call_id in self.budget.active
            self.budget.finish(call_id, usage)
            if admitted:
                self._record_usage(stage, call_id, usage, status, round((time.monotonic() - started) * 1000),
                                   model=request_model, response_model=getattr(response, "model", None))
            record_event("llm.response", function, response, call_id=call_id, status=status,
                         stage=stage,
                         **({"error": failure} if failure else {}),
                         duration_ms=round((time.monotonic() - started) * 1000, 3))

    async def stream_chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                          tool_choice: str | None = None) -> AsyncIterator[Any]:
        """流式，逐个产出原始 chunk。tool_choice="none" 时模型只能回答文字。"""
        function = "sheetmate.agent.llm.client.LLMClient.stream_chat"
        call_id = uuid.uuid4().hex
        stage = _LLM_STAGE.get() or "execution"
        started = time.monotonic()
        status = "error"
        chunk_count = 0
        usage = None
        response_model = self.model
        error_info = None
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        context_metrics: dict[str, Any] = {}
        stream_options = ({"include_usage": True}
                          if self._accepted_key() not in _UNSUPPORTED_STREAM_OPTIONS else None)
        kwargs = self._kwargs(messages, tools, stream=True, stream_options=stream_options,
                              tool_choice=tool_choice if tools else None,
                              _context_metrics=context_metrics, _stage_key=stage)
        context_metrics["cache_prefix"] = self._profile_cache_prefix(kwargs, stage)
        finish_reason: str | None = None
        try:
            record_event("llm.context", function, context_metrics, call_id=call_id, stage=stage)
            record_event("llm.request", function, kwargs, call_id=call_id)
            req_meta = context_metrics.get("request")
            req_size = (req_meta["serialized_chars"], req_meta["estimated_tokens"]) if req_meta else None
            stream = await self._create(kwargs, call_id, stage, _request_size=req_size)
            failure: BaseException | None = None
            try:
                saw_choice, has_content, has_reasoning = False, False, False
                chunks = stream.__aiter__()
                stream_wait = Phase("streaming_llm", stage)
                while True:
                    try:
                        chunk = await self._within_deadline(anext(chunks), streaming=True, wait_phase=stream_wait)
                    except StopAsyncIteration:
                        break
                    chunk_count += 1
                    response_model = getattr(chunk, "model", None) or response_model
                    if getattr(chunk, "usage", None) is not None:
                        usage = chunk.usage
                    # 与执行器一致，只消费首个候选；usage-only chunk 不代表正文或结束。
                    choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
                    if choice is not None:
                        saw_choice = True
                        delta = choice.delta
                        if getattr(delta, "content", None):
                            content_parts.append(delta.content)
                        if getattr(delta, "reasoning_content", None):
                            reasoning_parts.append(delta.reasoning_content)
                        has_content = has_content or bool((getattr(delta, "content", None) or "").strip())
                        has_reasoning = has_reasoning or bool(getattr(delta, "reasoning_content", None))
                        for call in getattr(delta, "tool_calls", None) or []:
                            slot = calls.setdefault(call.index, {"name": "", "arguments": ""})
                            if getattr(call, "id", None):
                                slot["id"] = call.id
                            if call.function is not None:
                                slot["name"] += call.function.name or ""
                                slot["arguments"] += call.function.arguments or ""
                        reason = getattr(choice, "finish_reason", None)
                        if reason is not None:
                            finish_reason = reason
                            if reason == "length":
                                auto = int(kwargs.get("max_tokens") or 0)
                                hint = (f"本次请求的输出上限为 {auto}，服务端的结束原因是 length（长度截断）。"
                                        "常见原因：输入加输出用完了模型窗口（文件大、对话长时最容易），或模型服务自带更小的输出上限。"
                                        "请减少一次处理的 sheet 数量，或查看模型服务日志里这次请求的输入、输出 Token 数。"
                                        if auto else
                                        "当前未设置输出上限，且该模型不接受输出上限参数，长度由模型服务自身决定；可尝试精简任务或分步处理。")
                                raise LLMError(f"模型输出达到长度上限，本轮响应不完整。{hint}",
                                               code="output_limit", finish_reason=reason)
                            if reason == "content_filter":
                                raise LLMError("模型服务因内容过滤停止了输出，本轮响应不完整。",
                                               code="content_filter", finish_reason=reason)
                            if reason not in {"stop", "tool_calls"}:
                                raise LLMError("模型服务返回了无法识别的结束原因，不能确认本轮响应完整。",
                                               code="unexpected_finish_reason", finish_reason=reason)
                    yield chunk
                if not saw_choice:
                    raise LLMError("模型服务返回了空响应，本轮没有执行结果。", code="empty_response")
                if finish_reason is None and _complete_tool_stream(calls, tools, tool_choice):
                    # A few self-hosted gateways close SSE cleanly after a
                    # complete tool call but omit finish_reason. Tell every
                    # consumer about this narrow inference explicitly. A
                    # partial argument or timed-out read never reaches here.
                    finish_reason = "tool_calls"
                    record_event("llm.stream.compatibility", function, {
                        "reason": "complete_tools_without_finish_reason", "tool_calls": len(calls),
                        "inferred_finish_reason": finish_reason,
                    }, call_id=call_id, stage=stage)
                    yield SimpleNamespace(choices=[SimpleNamespace(index=0, finish_reason=finish_reason,
                        delta=SimpleNamespace(content=None, reasoning_content=None, tool_calls=None))],
                        usage=None, model=response_model, _sheetmate_compatibility="missing_tool_finish_reason")
                if finish_reason is None:
                    raise LLMError("模型回复流提前结束，未收到正常结束原因，本轮响应不完整。", code="stream_incomplete")
                if finish_reason == "tool_calls" and not calls:
                    raise LLMError("模型声明了工具调用，但没有返回实际操作，本轮尚未执行。", code="tool_protocol")
                if calls and any(not call["name"].strip() or not call["arguments"].strip() for call in calls.values()):
                    raise LLMError("模型返回的工具调用不完整，相关操作尚未执行。", code="tool_protocol")
                if not has_content and not calls:
                    message = ("模型只返回了思考内容，没有返回回答或实际操作，本轮尚未完成。" if has_reasoning else
                               "模型服务返回的回复内容为空，本轮没有执行结果。")
                    raise LLMError(message, code="empty_response")
                status = "completed"
            except BaseException as exc:
                failure = exc
                raise
            finally:
                try:
                    await asyncio.wait_for(stream.close(), 5)
                except BaseException:
                    if failure is None:
                        raise
                    log.warning("关闭模型回复流时发生异常，已保留原始停止原因")
        except (asyncio.CancelledError, GeneratorExit) as exc:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "consumer_closed"
            raise
        except Exception as exc:
            status = "error"
            error = translate_error(exc)
            if error.finish_reason is None:
                error.finish_reason = finish_reason
            error_info = {"type": type(exc).__name__, "code": error.code, "message": str(error)}
            raise error from exc
        finally:
            # Some compatible providers emit cumulative usage more than once;
            # account for the final usage object once per streamed request.
            self._add_usage(usage)
            admitted = call_id in self.budget.active
            self.budget.finish(call_id, usage)
            if admitted:
                self._record_usage(stage, call_id, usage, status, round((time.monotonic() - started) * 1000),
                                   model=kwargs["model"], response_model=response_model)
            record_event("llm.response", function, {
                "model": response_model,
                "content": "".join(content_parts), "reasoning_content": "".join(reasoning_parts),
                "tool_calls": [{"index": index, **call} for index, call in calls.items()],
                "finish_reason": finish_reason, "usage": usage,
            }, call_id=call_id, status=status, chunk_count=chunk_count, stage=stage,
                         **({"error": error_info} if error_info else {}),
                         duration_ms=round((time.monotonic() - started) * 1000, 3))

    async def complete_json(self, prompt: str, system: str | None = None, retries: int | None = None, *,
                            image_parts: list[dict[str, Any]] | None = None, max_tokens: int | None = None,
                            schema: dict[str, Any] | None = None, schema_name: str = "response",
                            validator: Callable[[Any], None] | None = None) -> Any:
        """Request JSON and, when supplied, enforce a schema before returning it.

        Protocol negotiation and bounded response repair have separate budgets.
        A successful response takes one model call for every configuration.
        """
        with llm_stage(schema_name if schema_name != "response" else (_LLM_STAGE.get() or "structured")):
            request = self._complete_json(prompt, system, retries, image_parts=image_parts, max_tokens=max_tokens,
                                          schema=schema, schema_name=schema_name, validator=validator)
            timeout = self.cfg.runtime_policy.structured_timeout
            if not timeout:
                return await request
            try:
                return await asyncio.wait_for(request, timeout)
            except asyncio.TimeoutError as exc:
                raise LLMTimeout(f"模型结构化返回超过 {timeout:g} 秒，已停止本次调用。") from exc

    async def _complete_json(self, prompt: str, system: str | None, retries: int | None, *,
                             image_parts: list[dict[str, Any]] | None, max_tokens: int | None,
                             schema: dict[str, Any] | None, schema_name: str,
                             validator: Callable[[Any], None] | None) -> Any:
        # Honour the stage's output headroom, including hidden reasoning. A
        # private server can ignore thinking-off, so explicit truncation may
        # still grow the budget.
        auxiliary = schema_name in _AUXILIARY_JSON_STAGES
        output_tokens = max_tokens
        if schema is not None:
            prompt += ("\n\n请只返回符合以下 JSON Schema 的 json，不要省略必填字段，不能把布尔值或数组写成字符串：\n"
                       + json.dumps(schema, ensure_ascii=False, separators=(",", ":")))
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        # DeepSeek 的 JSON 模式要求提示词里出现小写的 "json"，否则会报错
        if "json" not in f"{system or ''}{prompt}":
            prompt = f"{prompt}\n\n（请输出 json）"
        messages.append({"role": "user", "content": user_content(prompt, image_parts)})
        preferred = self.cfg.capabilities.structured_output
        formats = (["json_schema", "json_object", "prompt"] if preferred == "json_schema" and schema is not None
                   else ["prompt"] if preferred == "prompt" else ["json_object", "prompt"])
        cache_key = (*_format_cache_key(self.cfg), "schema" if schema is not None else "untyped",
                     "images" if image_parts else "text")
        cached = _cached_format(cache_key)
        if cached in formats:
            formats = formats[formats.index(cached):]
        repairs_left = self.cfg.runtime_policy.structured_retries if retries is None else max(0, retries)
        last_error = ""
        while True:
            output_format = formats[0]
            response_format: dict[str, Any] | None = None
            if output_format == "json_schema":
                response_format = {"type": "json_schema", "json_schema": {
                    "name": re.sub(r"[^a-zA-Z0-9_-]", "_", schema_name)[:64] or "response", "schema": schema,
                }}
            elif output_format == "json_object":
                response_format = {"type": "json_object"}
            try:
                msg = await self.chat(messages, response_format=response_format, max_tokens=output_tokens,
                                      _auxiliary=auxiliary)
            except LLMError as exc:
                if len(formats) > 1 and _unsupported_format(exc):
                    record_event("llm.structured.fallback", "sheetmate.agent.llm.client.LLMClient._complete_json",
                                 {"from": formats[0], "to": formats[1], "reason": str(exc),
                                  "schema_name": schema_name})
                    formats.pop(0)
                    _cache_format(cache_key, formats[0])
                    continue
                raise
            content = msg.content or ""
            try:
                if getattr(msg, "refusal", None):
                    raise LLMError("模型拒绝了本次结构化请求，请检查需求或模型配置。")
                if getattr(msg, "_sheetmate_finish_reason", None) == "length":
                    raise ValueError("输出被长度上限截断，请精简内容并返回完整 JSON")
                if not content.strip():
                    raise ValueError("返回内容为空，可能达到输出上限，请返回完整 JSON")
                value = parse_json(content)
                if schema is not None:
                    validate_payload(value, schema)
                if validator is not None:
                    validator(value)
                return value
            except ValueError as exc:
                last_error = str(exc)
                record_event("llm.structured.invalid", "sheetmate.agent.llm.client.LLMClient._complete_json",
                             {"error": last_error, "schema_name": schema_name,
                              "repairs_left": repairs_left, "format": output_format})
                if repairs_left <= 0:
                    break
                repairs_left -= 1
                if (auxiliary and getattr(msg, "_sheetmate_finish_reason", None) == "length"
                        and output_tokens and _UNCAPPED_GROWTH_LIMIT > output_tokens):
                    previous_tokens = output_tokens
                    output_tokens = min(_UNCAPPED_GROWTH_LIMIT, output_tokens * 2)
                    record_event("llm.structured.output_budget", "sheetmate.agent.llm.client.LLMClient._complete_json",
                                 {"schema_name": schema_name, "from": previous_tokens, "to": output_tokens,
                                  "reason": "output_truncated", "repairs_left": repairs_left})
                details = (json.dumps(exc.errors, ensure_ascii=False) if isinstance(exc, StructuredOutputError)
                           else last_error[:500])
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": (
                    f"上面的 json 尚不能使用。校验错误：{details}。"
                    "请只修正这些格式或字段错误，返回完整 JSON；保持原有业务含义，不猜测金额、列名或文件 ID。")})
        raise LLMError(f"模型没有返回符合要求的 JSON：{last_error}")


# ---------------------------------------------------------------------------
# 可有可无的辅助调用：限时、失败不抛出（思考模式的计划审查、步骤核对、回答核对）
# ---------------------------------------------------------------------------


@dataclass
class AuxResult:
    """辅助调用的结果。成功时 value 是解析好的 JSON；失败时 reason 是给用户看的一句话。"""

    value: Any = None
    reason: str = ""
    timed_out: bool = False
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return not self.reason


def _reason_text(exc: Exception) -> str:
    """异常信息整理成一句话（去掉结尾标点，方便放进括号里）。"""
    text = " ".join(str(exc).split()).rstrip("。.!！")
    return text[:160] or type(exc).__name__


async def aux_json(llm: Any, prompt: str, *, system: str | None = None, timeout: float,
                   max_tokens: int | None = None, image_parts: list[dict[str, Any]] | None = None,
                   schema: dict[str, Any] | None = None, schema_name: str = "response",
                   label: str = "辅助调用") -> AuxResult:
    """限时的 JSON 调用，给计划审查、步骤核对、回答核对这类「可有可无」的环节用。

    超时、模型报错和意外异常都不往外抛，而是把原因带回去，由调用方降级并如实告诉用户，同时写一行日志。
    遵循调用方给定的超时上限，并受运行时 hard_idle 约束，避免被主模型长超时静默放大。
    用户点「停止」引发的取消照常向上传递。"""
    cfg = getattr(llm, "cfg", None)
    policy = getattr(cfg, "runtime_policy", None)
    hard_idle = getattr(policy, "llm_hard_idle_seconds", None)
    if isinstance(hard_idle, (int, float)) and hard_idle > 0:
        timeout = min(timeout, float(hard_idle))
    extra: dict[str, Any] = {}
    if image_parts:
        extra["image_parts"] = image_parts
    if max_tokens:
        extra["max_tokens"] = max_tokens
    started = time.monotonic()
    try:
        request = (complete_structured(llm, prompt, schema=schema, schema_name=schema_name, system=system, **extra)
                   if schema is not None else llm.complete_json(prompt, system=system, **extra))
        value = await asyncio.wait_for(request, timeout)
    except asyncio.TimeoutError:
        log.warning("%s超时：%g 秒内没有返回，已放弃这次调用", label, timeout)
        return AuxResult(reason=f"模型 {timeout:g} 秒内没有返回", timed_out=True)
    except LLMError as exc:
        log.warning("%s没有完成（%.1f 秒）：%s", label, time.monotonic() - started, exc)
        return AuxResult(reason=_reason_text(exc), error=exc, timed_out=isinstance(exc, LLMTimeout))
    except Exception as exc:  # 辅助环节出任何问题都不能拖垮主流程
        log.exception("%s出现意外错误", label)
        return AuxResult(reason=f"调用出错（{type(exc).__name__}）", error=exc)
    return AuxResult(value=value)


# ---------------------------------------------------------------------------
# 工厂：每次请求按最新配置创建客户端；测试时可以替换成假的 LLM
# ---------------------------------------------------------------------------

_factory: Callable[[], LanguageModel] | None = None
_privacy_factory: Callable[[], LanguageModel] | None = None


def set_llm_factory(factory: Callable[[], LanguageModel] | None) -> None:
    global _factory
    _factory = factory


def set_privacy_llm_factory(factory: Callable[[], LanguageModel] | None) -> None:
    """测试时替换脱敏模型。"""
    global _privacy_factory
    _privacy_factory = factory


def get_llm() -> LanguageModel:
    """所有「正常模型」调用的唯一入口：返回的模型外面包了一层脱敏拦截器（发出去先脱敏，回来在本机还原）。"""
    from sheetmate.agent.llm.guarded import GuardedLLM

    inner = _factory() if _factory is not None else LLMClient(get_llm_settings())
    return GuardedLLM(inner)


def privacy_llm_configured() -> bool:
    return _privacy_factory is not None or get_privacy_llm_settings().configured


def get_privacy_llm() -> LanguageModel:
    """脱敏模型：设置页里单独配置，专门给脱敏模块识别联系方式和地址用。

    它必须读到真实数据才能判断，所以不经过拦截器；建议使用内网私有部署的模型，地址由使用者自己决定。"""
    if _privacy_factory is not None:
        return _privacy_factory()
    cfg = get_privacy_llm_settings()
    if not cfg.configured:
        raise LLMError("还没有配置脱敏模型。", code="privacy_llm_not_configured")
    return LLMClient(cfg)


async def test_connection(cfg: LLMSettings) -> dict[str, Any]:
    """设置页「测试连接」：只是探测接口是否可用，等待时间不超过 5 分钟。"""
    client = LLMClient(replace(cfg, thinking_enabled=False), pooled=False)
    wait = min(cfg.timeout, 300)
    try:
        # 思考内容可能计入输出额度，不能为两个字的回复强制缩成 10 Token。
        msg = await asyncio.wait_for(
            client.chat([{"role": "user", "content": "回复两个字：你好"}], _auxiliary=True), wait,
        )
    except (asyncio.TimeoutError, LLMTimeout) as exc:
        raise LLMTimeout(
            f"连接测试超时（等待上限为 {wait:g} 秒）。模型可能响应较慢或接口地址不通，请稍后重试。"
        ) from exc
    finally:
        await client.client.close()
    reply = (msg.content or "").strip()[:50]
    if not reply:
        if getattr(msg, "_sheetmate_finish_reason", None) == "length":
            raise LLMError(
                "模型输出达到 Token 上限，尚未生成回复。"
                "可关闭模型思考后重试，或检查模型服务自带的输出上限。"
            )
        if getattr(msg, "reasoning_content", None):
            raise LLMError(
                "模型只返回了思考内容，没有返回最终回复。"
                "可关闭模型思考后重试。"
            )
        raise LLMError("模型服务已响应，但返回的回复内容为空，请检查模型或接口兼容性后重试。")
    return {"ok": True, "model": cfg.model, "reply": reply}
