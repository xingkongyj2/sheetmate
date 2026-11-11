"""HTTP 请求数据契约。"""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict
from sheetmate.core.config.model_policy import LLMCapabilities, LLMMaxTokens, LLMRuntimePolicy, LLMTimeout


ProviderId = Literal["deepseek", "qwen", "custom"]


class LLMBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    provider_id: ProviderId = "deepseek"
    api_key: str | None = None  # 不传表示保持原来的 Key
    base_url: str | None = None
    model: str | None = None
    thinking_enabled: bool | None = None
    enabled: bool | None = None
    timeout: LLMTimeout | None = None
    max_tokens: LLMMaxTokens | None = None
    capabilities: LLMCapabilities | None = None
    runtime_policy: LLMRuntimePolicy | None = None

