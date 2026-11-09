"""LLM 客户端（沿用 TabClaw 的 LLMClient，补充 JSON 输出、错误翻译和用量统计）。

OpenAI 兼容接口，默认连接 DeepSeek（deepseek-v4-pro 或 deepseek-flash）。
模型服务配置由设置页分别保存，并按固定优先级选择当前可用服务。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import openai
import httpx
from openai import AsyncOpenAI

from sheetmate.agent.content import user_content
from sheetmate.agent.structured import StructuredOutputError, complete_structured, validate_payload
from sheetmate.config import LLMSettings, get_llm_settings, is_deepseek_url
from sheetmate.conversation_log import record_event


log = logging.getLogger(__name__)


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
    message = "还没有配置模型的 API Key。请在「设置」里填写并保存 API Key。"

    def __init__(self) -> None:
        super().__init__(self.message)


_LEGACY_LLM_NOT_CONFIGURED_MESSAGES = (
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
            str(cfg.thinking_enabled), json.dumps(cfg.extra_body, sort_keys=True, ensure_ascii=False, default=str))


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


class LLMClient:
    """异步客户端。chat / stream_chat 与 TabClaw 一致，另加 complete_json。"""

    def __init__(self, cfg: LLMSettings):
        if not cfg.configured:
            raise LLMNotConfigured()
        self.cfg = cfg
        self.model = cfg.model
        self.client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=cfg.timeout,
                                  max_retries=cfg.runtime_policy.sdk_retries)
        self.usage = {"prompt_tokens": 0, "completion_tokens": 0}

    def _kwargs(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None, **extra: Any) -> dict[str, Any]:
        qwen = self.cfg.provider_id == "qwen"
        deepseek_thinking = not qwen and self.cfg.thinking_supported
        legacy_chat = not qwen and self.model == "deepseek-chat" and is_deepseek_url(self.cfg.base_url)
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
            "model": self.model,
            "messages": messages,
            "temperature": self.cfg.temperature if temperature is None else temperature,
        }
        if self.cfg.max_tokens > 0:
            kwargs["max_tokens"] = self.cfg.max_tokens
        extra_body = dict(self.cfg.extra_body)
        if qwen:
            extra_body.pop("thinking", None)
            extra_body["enable_thinking"] = self.cfg.thinking_enabled
        elif deepseek_thinking:
            extra_body["thinking"] = {"type": "enabled" if self.cfg.thinking_enabled else "disabled"}
        extra_body.update(extra.pop("extra_body", None) or {})
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
        return kwargs

    def _add_usage(self, usage: Any) -> None:
        if usage is None:
            return
        self.usage["prompt_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.usage["completion_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **extra: Any) -> Any:
        """非流式，返回 message 对象（有 content / tool_calls）。"""
        function = "sheetmate.agent.llm.LLMClient.chat"
        call_id = uuid.uuid4().hex
        started = time.monotonic()
        status = "error"
        try:
            kwargs = self._kwargs(messages, tools, **extra)
            record_event("llm.request", function, kwargs, call_id=call_id, base_url=self.cfg.base_url,
                         timeout=self.cfg.timeout, sdk_retries=self.cfg.runtime_policy.sdk_retries)
            resp = await self.client.chat.completions.create(**kwargs)
            record_event("llm.response", function, resp, call_id=call_id)
            self._add_usage(resp.usage)
            if not resp.choices:
                raise LLMError("模型服务返回了空响应，请检查接口兼容性后重试。")
            message = resp.choices[0].message
            # Keep request-local metadata on this response, not mutable client state:
            # the same LLMClient can serve concurrent agent requests.
            object.__setattr__(message, "_sheetmate_finish_reason", resp.choices[0].finish_reason)
            status = "completed"
            return message
        except asyncio.CancelledError as exc:
            status = "cancelled"
            record_event("llm.error", function, {"type": type(exc).__name__, "message": str(exc)}, call_id=call_id)
            raise
        except Exception as exc:
            status = "error"
            error = translate_error(exc)
            record_event("llm.error", function, {"type": type(exc).__name__, "message": str(exc),
                         "code": error.code, "translated_message": str(error)}, call_id=call_id)
            raise error from exc
        finally:
            record_event("llm.done", function, {"status": status}, call_id=call_id,
                         duration_ms=round((time.monotonic() - started) * 1000, 3))

    async def stream_chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                          tool_choice: str | None = None) -> AsyncIterator[Any]:
        """流式，逐个产出原始 chunk。tool_choice="none" 时模型只能回答文字。"""
        function = "sheetmate.agent.llm.LLMClient.stream_chat"
        call_id = uuid.uuid4().hex
        started = time.monotonic()
        status = "error"
        chunk_count = 0
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        kwargs = self._kwargs(messages, tools, stream=True, tool_choice=tool_choice if tools else None)
        finish_reason: str | None = None
        try:
            record_event("llm.request", function, kwargs, call_id=call_id, base_url=self.cfg.base_url,
                         timeout=self.cfg.timeout, sdk_retries=self.cfg.runtime_policy.sdk_retries)
            stream = await self.client.chat.completions.create(**kwargs)
            failure: BaseException | None = None
            try:
                saw_choice, has_content, has_reasoning = False, False, False
                async for chunk in stream:
                    chunk_count += 1
                    record_event("llm.chunk", function, chunk, call_id=call_id, chunk_index=chunk_count)
                    self._add_usage(getattr(chunk, "usage", None))
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
                                raise LLMError("模型输出达到长度上限，本轮响应不完整，请增加输出上限后重试。",
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
                    await stream.close()
                except BaseException:
                    if failure is None:
                        raise
                    log.warning("关闭模型回复流时发生异常，已保留原始停止原因")
        except (asyncio.CancelledError, GeneratorExit) as exc:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "consumer_closed"
            record_event("llm.error", function, {"type": type(exc).__name__, "message": str(exc)}, call_id=call_id)
            raise
        except Exception as exc:
            status = "error"
            error = translate_error(exc)
            if error.finish_reason is None:
                error.finish_reason = finish_reason
            record_event("llm.error", function, {"type": type(exc).__name__, "message": str(exc),
                         "code": error.code, "translated_message": str(error), "finish_reason": finish_reason},
                         call_id=call_id)
            raise error from exc
        finally:
            record_event("llm.response", function, {
                "content": "".join(content_parts), "reasoning_content": "".join(reasoning_parts),
                "tool_calls": [{"index": index, **call} for index, call in calls.items()],
                "finish_reason": finish_reason, "chunk_count": chunk_count,
            }, call_id=call_id, assembled=True)
            record_event("llm.done", function, {"status": status, "finish_reason": finish_reason},
                         call_id=call_id, duration_ms=round((time.monotonic() - started) * 1000, 3))

    async def complete_json(self, prompt: str, system: str | None = None, retries: int | None = None, *,
                            image_parts: list[dict[str, Any]] | None = None, max_tokens: int | None = None,
                            schema: dict[str, Any] | None = None, schema_name: str = "response",
                            validator: Callable[[Any], None] | None = None) -> Any:
        """Request JSON and, when supplied, enforce a schema before returning it.

        Protocol negotiation and bounded response repair have separate budgets.
        A successful response takes one model call for every configuration.
        """
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
        if max_tokens and self.cfg.thinking_supported and self.cfg.thinking_enabled:
            max_tokens = None
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
                msg = await self.chat(messages, response_format=response_format, max_tokens=max_tokens)
            except LLMError as exc:
                if len(formats) > 1 and _unsupported_format(exc):
                    record_event("llm.structured.fallback", "sheetmate.agent.llm.LLMClient._complete_json",
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
                record_event("llm.structured.validated", "sheetmate.agent.llm.LLMClient._complete_json",
                             {"value": value, "schema_name": schema_name, "format": output_format})
                return value
            except ValueError as exc:
                last_error = str(exc)
                record_event("llm.structured.invalid", "sheetmate.agent.llm.LLMClient._complete_json",
                             {"content": content, "error": last_error, "schema_name": schema_name,
                              "repairs_left": repairs_left, "format": output_format})
                if repairs_left <= 0:
                    break
                repairs_left -= 1
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
    不加限时的话，一次调用最坏要等 4 个 120 秒（解析失败重试 1 次，SDK 超时后又各重试 1 次），比整个任务还长。
    用户点「停止」引发的取消照常向上传递。"""
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

_factory: Callable[[], Any] | None = None


def set_llm_factory(factory: Callable[[], Any] | None) -> None:
    global _factory
    _factory = factory


def get_llm() -> LLMClient:
    if _factory is not None:
        return _factory()
    return LLMClient(get_llm_settings())


async def test_connection(cfg: LLMSettings) -> dict[str, Any]:
    """设置页「测试连接」：沿用输出预算，并限制整次测试的等待时间。"""
    client = LLMClient(cfg)
    extra: dict[str, Any] = {}
    if cfg.thinking_supported:
        # 已知协议的连接探测无需思考；中转或其他模型仍可能默认开启思考。
        extra["extra_body"] = ({"enable_thinking": False} if cfg.provider_id == "qwen"
                               else {"thinking": {"type": "disabled"}})
    try:
        # 思考内容可能计入输出额度，不能为两个字的回复强制缩成 10 Token。
        msg = await asyncio.wait_for(
            client.chat([{"role": "user", "content": "回复两个字：你好"}], **extra), cfg.timeout,
        )
    except (asyncio.TimeoutError, LLMTimeout) as exc:
        raise LLMTimeout(
            f"连接测试超时（等待上限为 {cfg.timeout:g} 秒）。模型可能响应较慢，"
            "可在「高级配置」中增加「请求超时」后重试。"
        ) from exc
    finally:
        await client.client.close()
    reply = (msg.content or "").strip()[:50]
    if not reply:
        if getattr(msg, "_sheetmate_finish_reason", None) == "length":
            raise LLMError(
                "模型输出达到 Token 上限，尚未生成回复。"
                "请在「高级配置」中增加「输出 Token 上限」后重试。"
            )
        if getattr(msg, "reasoning_content", None):
            raise LLMError(
                "模型只返回了思考内容，没有返回最终回复。"
                "请增加「输出 Token 上限」，或关闭模型思考后重试。"
            )
        raise LLMError("模型服务已响应，但返回的回复内容为空，请检查模型或接口兼容性后重试。")
    return {"ok": True, "model": cfg.model, "reply": reply}
