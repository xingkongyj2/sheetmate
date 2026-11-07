"""LLM 客户端（沿用 TabClaw 的 LLMClient，补充 JSON 输出、错误翻译和用量统计）。

OpenAI 兼容接口，默认连接 DeepSeek（deepseek-v4-pro 或 deepseek-flash）。
模型服务配置由设置页分别保存，并按固定优先级选择当前可用服务。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import openai
from openai import AsyncOpenAI

from sheetmate.agent.content import user_content
from sheetmate.config import LLMSettings, get_llm_settings, is_deepseek_url


log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """调用模型失败。message 是给用户看的中文说明。"""


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
        return LLMError("API Key 无效或已过期，请到「设置」里检查。")
    if isinstance(exc, openai.PermissionDeniedError):
        return LLMError("这个 API Key 没有权限调用当前模型。")
    if isinstance(exc, openai.RateLimitError):
        return LLMError("模型服务限流或额度不足，请稍后再试或检查账户余额。")
    if isinstance(exc, openai.APIConnectionError):
        return LLMError("连接不上模型服务，请检查网络或「设置」里的接口地址。")
    if isinstance(exc, openai.APITimeoutError):
        return LLMError("模型响应超时，请稍后再试。")
    if isinstance(exc, openai.APIStatusError):
        if exc.status_code in {400, 422} and re.search(
            r"(?:image|vision|multimodal).{0,100}(?:not supported|unsupported|not support)|"
            r"(?:not supported|unsupported|not support).{0,100}(?:image|vision|multimodal)|"
            r"content.{0,100}expected.{0,30}string.{0,60}(?:array|list)",
            str(exc.message), re.IGNORECASE,
        ):
            return LLMError("当前模型或接口不支持图片输入，请在「设置」中切换为支持图片的模型后重试。")
        if exc.status_code == 402:
            return LLMError("模型账户余额不足，请充值后再试。")
        if exc.status_code == 404:
            return LLMError("模型名称或接口地址不对，请到「设置」里检查。")
        return LLMError(f"模型服务返回错误（{exc.status_code}）：{_short(exc.message)}")
    return LLMError(f"调用模型出错：{_short(str(exc))}")


def _short(text: str, limit: int = 300) -> str:
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def parse_json(text: str) -> Any:
    """宽松解析 LLM 返回的 JSON：去掉 ```json 包裹、截取最外层括号、去掉尾逗号。"""
    content = (text or "").strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    starts = [i for i in (content.find("{"), content.find("[")) if i >= 0]
    if not starts:
        raise ValueError("返回内容里没有 JSON")
    start = min(starts)
    end = max(content.rfind("}"), content.rfind("]"))
    candidate = content[start : end + 1]
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    return json.loads(candidate)


class LLMClient:
    """异步客户端。chat / stream_chat 与 TabClaw 一致，另加 complete_json。"""

    def __init__(self, cfg: LLMSettings):
        if not cfg.configured:
            raise LLMNotConfigured()
        self.cfg = cfg
        self.model = cfg.model
        self.client = AsyncOpenAI(api_key=cfg.api_key, base_url=cfg.base_url, timeout=cfg.timeout, max_retries=1)
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
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": extra.pop("temperature", None) or self.cfg.temperature,
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
        try:
            resp = await self.client.chat.completions.create(**self._kwargs(messages, tools, **extra))
        except Exception as exc:
            raise translate_error(exc) from exc
        self._add_usage(resp.usage)
        return resp.choices[0].message

    async def stream_chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                          tool_choice: str | None = None) -> AsyncIterator[Any]:
        """流式，逐个产出原始 chunk。tool_choice="none" 时模型只能回答文字。"""
        kwargs = self._kwargs(messages, tools, stream=True, tool_choice=tool_choice if tools else None)
        try:
            stream = await self.client.chat.completions.create(**kwargs)
            async for chunk in stream:
                self._add_usage(getattr(chunk, "usage", None))
                yield chunk
        except Exception as exc:
            raise translate_error(exc) from exc

    async def complete_json(self, prompt: str, system: str | None = None, retries: int = 1, *,
                            image_parts: list[dict[str, Any]] | None = None, max_tokens: int | None = None) -> Any:
        """要求模型只输出 JSON，解析失败时带着错误重试。
        max_tokens 给输出很小的调用（核对类）设一个小上限，免得模型空转到默认的 8192 才停；
        思考开着时推理内容也算在 max_tokens 里，小上限会把答案挤没，所以此时忽略它。"""
        if max_tokens and self.cfg.thinking_supported and self.cfg.thinking_enabled:
            max_tokens = None
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        # DeepSeek 的 JSON 模式要求提示词里出现小写的 "json"，否则会报错
        if "json" not in f"{system or ''}{prompt}":
            prompt = f"{prompt}\n\n（请输出 json）"
        messages.append({"role": "user", "content": user_content(prompt, image_parts)})
        use_json_mode = True
        last_error = ""
        for _ in range(retries + 1):
            try:
                msg = await self.chat(messages, response_format={"type": "json_object"} if use_json_mode else None,
                                      max_tokens=max_tokens)
            except LLMError as exc:
                if use_json_mode and "response_format" in str(exc):
                    use_json_mode = False
                    continue
                raise
            content = msg.content or ""
            try:
                return parse_json(content)
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "user", "content": f"上面的输出不是合法 JSON（{last_error}）。请只输出一个 JSON 对象，不要任何其他文字。"})
        raise LLMError(f"模型没有返回合法的 JSON：{last_error}")


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
        value = await asyncio.wait_for(llm.complete_json(prompt, system=system, **extra), timeout)
    except asyncio.TimeoutError:
        log.warning("%s超时：%g 秒内没有返回，已放弃这次调用", label, timeout)
        return AuxResult(reason=f"模型 {timeout:g} 秒内没有返回", timed_out=True)
    except LLMError as exc:
        log.warning("%s没有完成（%.1f 秒）：%s", label, time.monotonic() - started, exc)
        return AuxResult(reason=_reason_text(exc), error=exc)
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
    """设置页「测试连接」：发一个最小请求。"""
    client = LLMClient(cfg)
    extra: dict[str, Any] = {}
    if cfg.thinking_supported:
        # 连接探测无需思考，避免极小的输出预算被思考内容耗尽。
        extra["extra_body"] = ({"enable_thinking": False} if cfg.provider_id == "qwen"
                               else {"thinking": {"type": "disabled"}})
    msg = await client.chat([{"role": "user", "content": "回复两个字：你好"}], max_tokens=10, **extra)
    reply = (msg.content or "").strip()[:50]
    if not reply:
        raise LLMError("模型返回了空回复，请检查模型设置后重试。")
    return {"ok": True, "model": cfg.model, "reply": reply}
