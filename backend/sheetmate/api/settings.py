"""模型设置：查看 / 保存 / 测试连接。默认 DeepSeek，只填 API Key 就能用。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel

from sheetmate.agent.llm import LLMError, test_connection
from sheetmate.api.deps import current_user
from sheetmate.config import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_DEFAULT_MODEL,
    DEEPSEEK_MODELS,
    DEEPSEEK_THINKING_MODELS,
    QWEN_BASE_URL,
    QWEN_MODELS,
    LLMSettings,
    get_all_llm_settings,
    get_llm_provider_settings,
    save_llm_settings,
)
from sheetmate.db.models import User

router = APIRouter(prefix="/api/settings", tags=["settings"])

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
ProviderId = Literal["deepseek", "qwen", "custom"]


class LLMBody(BaseModel):
    provider_id: ProviderId = "deepseek"
    api_key: str | None = None  # 不传表示保持原来的 Key
    base_url: str | None = None
    model: str | None = None
    thinking_enabled: bool | None = None


def _provider_view(provider: dict[str, Any], cfg: LLMSettings) -> dict[str, Any]:
    return {
        **provider,
        "configured": cfg.configured,
        "api_key": cfg.api_key,
        "key_hint": cfg.key_hint(),
        "base_url": cfg.base_url,
        "model": cfg.model,
        "thinking_supported": cfg.thinking_supported,
        "thinking_enabled": cfg.thinking_enabled and cfg.thinking_supported,
    }


def _view() -> dict[str, Any]:
    configs = get_all_llm_settings()
    active = next((configs[provider["id"]] for provider in PROVIDERS if configs[provider["id"]].configured),
                  configs["deepseek"])
    active_provider_id = active.provider_id if active.configured else None
    return {
        # 保留当前生效配置的顶层字段，兼容旧版前端。
        "configured": active.configured,
        "active_provider_id": active_provider_id,
        "base_url": active.base_url,
        "model": active.model,
        "key_hint": active.key_hint(),
        "default_model": DEEPSEEK_DEFAULT_MODEL,
        "thinking_supported": active.thinking_supported,
        "thinking_enabled": active.thinking_enabled and active.thinking_supported,
        "providers": [_provider_view(provider, configs[provider["id"]]) for provider in PROVIDERS],
    }


@router.get("/llm")
def get_llm_config(response: Response, _: User = Depends(current_user)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, **_view()}


@router.put("/llm")
def put_llm_config(body: LLMBody, response: Response, _: User = Depends(current_user)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    save_llm_settings(body.provider_id, body.api_key, body.base_url, body.model, body.thinking_enabled)
    return {"ok": True, **_view()}


@router.post("/llm/test")
async def test_llm(body: LLMBody, response: Response, _: User = Depends(current_user)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    current = get_llm_provider_settings(body.provider_id)
    cfg = replace(current,
                  api_key=current.api_key if body.api_key is None else body.api_key.strip(),
                  base_url=current.base_url if body.base_url is None else body.base_url.strip(),
                  model=current.model if body.model is None else body.model.strip(),
                  thinking_enabled=current.thinking_enabled if body.thinking_enabled is None else body.thinking_enabled)
    try:
        result = await test_connection(cfg)
    except LLMError as exc:
        return {"ok": False, "error": {"type": "llm", "message": str(exc)}}
    return {"ok": True, **result}
