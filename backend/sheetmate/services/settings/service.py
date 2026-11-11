"""模型设置用例：读取、局部更新以及测试保存前的连接参数。"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from sheetmate.agent.llm import LLMError, test_connection
from sheetmate.core.config.settings import (
    DEEPSEEK_BASE_URL, DEEPSEEK_DEFAULT_MODEL, DEEPSEEK_MODELS, DEEPSEEK_THINKING_MODELS,
    QWEN_BASE_URL, QWEN_MODELS, LLMSettings, get_all_llm_settings,
    get_llm_provider_settings, save_llm_settings, select_llm_settings,
)
from sheetmate.core.config.model_policy import LLMCapabilities, LLMRuntimePolicy


@dataclass(frozen=True)
class ModelSettingsUpdate:
    """服务层更新命令，不依赖 HTTP 请求模型。None 表示保留当前值。"""

    provider_id: str = "deepseek"
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    thinking_enabled: bool | None = None
    enabled: bool | None = None
    timeout: float | None = None
    max_tokens: int | None = None
    capabilities: LLMCapabilities | None = None
    runtime_policy: LLMRuntimePolicy | None = None


PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek", "base_url": DEEPSEEK_BASE_URL,
     "models": list(DEEPSEEK_MODELS), "thinking_models": list(DEEPSEEK_THINKING_MODELS),
     "description": "使用 DeepSeek 官方接口，自动填写接口地址和支持的模型。"},
    {"id": "qwen", "name": "千问", "base_url": QWEN_BASE_URL,
     "models": list(QWEN_MODELS),
     "description": "使用阿里云百炼的 OpenAI 兼容接口，并提供常用模型作为输入建议。"},
    {"id": "custom", "name": "自定义兼容服务", "base_url": "", "models": [],
     "description": "用于代理、私有部署或其他兼容 OpenAI Chat Completions 的服务，需要手动填写接口地址和模型。"},
]


def _policy_view(cfg: LLMSettings) -> dict[str, Any]:
    return {"timeout": cfg.timeout, "max_tokens": cfg.max_tokens,
            "capabilities": cfg.capabilities.model_dump(), "runtime_policy": cfg.runtime_policy.model_dump()}


def _provider_view(provider: dict[str, Any], cfg: LLMSettings) -> dict[str, Any]:
    return {
        **provider,
        **_policy_view(cfg),
        "configured": cfg.configured,
        "enabled": cfg.enabled,
        "api_key": cfg.api_key,
        "key_hint": cfg.key_hint(),
        "base_url": cfg.base_url,
        "model": cfg.model,
        "thinking_supported": cfg.thinking_supported,
        "thinking_enabled": cfg.thinking_enabled and cfg.thinking_supported,
    }


def _view() -> dict[str, Any]:
    configs = get_all_llm_settings()
    active = select_llm_settings(configs)
    active_provider_id = active.provider_id if active.configured else None
    return {
        # 保留当前生效配置的顶层字段，兼容旧版前端。
        "configured": active.configured,
        "enabled": active.enabled,
        "active_provider_id": active_provider_id,
        "base_url": active.base_url,
        "model": active.model,
        "key_hint": active.key_hint(),
        "default_model": DEEPSEEK_DEFAULT_MODEL,
        "thinking_supported": active.thinking_supported,
        "thinking_enabled": active.thinking_enabled and active.thinking_supported,
        **_policy_view(active),
        "providers": [_provider_view(provider, configs[provider["id"]]) for provider in PROVIDERS],
    }


def get_llm_config() -> dict[str, Any]:
    return _view()


def put_llm_config(update: ModelSettingsUpdate) -> dict[str, Any]:
    save_llm_settings(update.provider_id, update.api_key, update.base_url, update.model, update.thinking_enabled,
                      timeout=update.timeout, max_tokens=update.max_tokens,
                      capabilities=update.capabilities, runtime_policy=update.runtime_policy, enabled=update.enabled)
    return _view()


async def test_llm_config(update: ModelSettingsUpdate) -> dict[str, Any]:
    current = get_llm_provider_settings(update.provider_id)
    cfg = replace(current,
                  enabled=current.enabled if update.enabled is None else update.enabled,
                  api_key=current.api_key if update.api_key is None else update.api_key.strip(),
                  base_url=current.base_url if update.base_url is None else update.base_url.strip(),
                  model=current.model if update.model is None else update.model.strip(),
                  thinking_enabled=current.thinking_enabled if update.thinking_enabled is None else update.thinking_enabled,
                  timeout=current.timeout if update.timeout is None else update.timeout,
                  max_tokens=current.max_tokens if update.max_tokens is None else update.max_tokens,
                  capabilities=current.capabilities if update.capabilities is None else current.capabilities.model_copy(
                      update=update.capabilities.model_dump(exclude_unset=True)),
                  runtime_policy=current.runtime_policy if update.runtime_policy is None else current.runtime_policy.model_copy(
                      update=update.runtime_policy.model_dump(exclude_unset=True)))
    try:
        result = await test_connection(cfg)
    except LLMError as exc:
        return {"ok": False, "error": {"type": "llm", "message": str(exc)}}
    return {"ok": True, **result}
