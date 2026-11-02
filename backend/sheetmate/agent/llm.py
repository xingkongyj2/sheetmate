"""LLM 客户端（沿用 TabClaw 的 LLMClient，补充 JSON 输出、错误翻译和用量统计）。

OpenAI 兼容接口，默认连接 DeepSeek（deepseek-v4-pro，可选 deepseek-flash）。
只要配置 DEEPSEEK_API_KEY 就能用；也可以在设置页换成其他兼容服务。
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Callable
from typing import Any

import openai
from openai import AsyncOpenAI

from sheetmate.config import DEEPSEEK_MODELS, LLMSettings, get_llm_settings, is_deepseek_url


class LLMError(RuntimeError):
    """调用模型失败。message 是给用户看的中文说明。"""


class LLMNotConfigured(LLMError):
    def __init__(self) -> None:
        super().__init__("还没有配置模型的 API Key。请在「设置」里填写 DeepSeek API Key（或在 .env 中设置 DEEPSEEK_API_KEY）。")


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
        if tools and self.model in DEEPSEEK_MODELS and is_deepseek_url(self.cfg.base_url):
            # 历史摘要没有原始思考字段；新模型携带 tools 时仍要求该字段存在。
            messages = [{"reasoning_content": "", **message} if message.get("role") == "assistant" else message
                        for message in messages]
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": extra.pop("temperature", None) or self.cfg.temperature,
        }
        if self.cfg.max_tokens > 0:
            kwargs["max_tokens"] = self.cfg.max_tokens
        extra_body = dict(self.cfg.extra_body)
        if self.model in DEEPSEEK_MODELS and is_deepseek_url(self.cfg.base_url):
            extra_body["thinking"] = {"type": "enabled" if self.cfg.thinking_enabled else "disabled"}
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

    async def complete_json(self, prompt: str, system: str | None = None, retries: int = 1) -> Any:
        """要求模型只输出 JSON，解析失败时带着错误重试。"""
        messages: list[dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        # DeepSeek 的 JSON 模式要求提示词里出现小写的 "json"，否则会报错
        if "json" not in f"{system or ''}{prompt}":
            prompt = f"{prompt}\n\n（请输出 json）"
        messages.append({"role": "user", "content": prompt})
        use_json_mode = True
        last_error = ""
        for _ in range(retries + 1):
            try:
                msg = await self.chat(messages, response_format={"type": "json_object"} if use_json_mode else None)
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
    if cfg.model in DEEPSEEK_MODELS and is_deepseek_url(cfg.base_url):
        # 连接探测无需思考，避免极小的输出预算被思考内容耗尽。
        extra["extra_body"] = {**cfg.extra_body, "thinking": {"type": "disabled"}}
    msg = await client.chat([{"role": "user", "content": "回复两个字：你好"}], max_tokens=10, **extra)
    reply = (msg.content or "").strip()[:50]
    if not reply:
        raise LLMError("模型返回了空回复，请检查模型设置后重试。")
    return {"ok": True, "model": cfg.model, "reply": reply}
