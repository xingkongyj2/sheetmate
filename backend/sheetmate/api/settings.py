"""模型设置：查看 / 保存 / 测试连接。默认 DeepSeek，只填 API Key 就能用。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from sheetmate.agent.llm import LLMError, test_connection
from sheetmate.api.deps import current_user
from sheetmate.config import DEEPSEEK_BASE_URL, DEEPSEEK_DEFAULT_MODEL, DEEPSEEK_MODELS, LLMSettings, get_llm_settings, save_llm_settings
from sheetmate.db.models import User

router = APIRouter(prefix="/api/settings", tags=["settings"])

PROVIDERS = [
    {"id": "deepseek", "name": "DeepSeek（推荐）", "base_url": DEEPSEEK_BASE_URL,
     "models": list(DEEPSEEK_MODELS)},
    {"id": "siliconflow", "name": "硅基流动", "base_url": "https://api.siliconflow.cn/v1",
     "models": ["deepseek-ai/DeepSeek-V3", "Qwen/Qwen2.5-72B-Instruct"]},
    {"id": "qwen", "name": "阿里云百炼", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "models": ["qwen-plus", "qwen-max", "deepseek-v3"]},
    {"id": "openai", "name": "OpenAI 兼容", "base_url": "https://api.openai.com/v1", "models": ["gpt-4o", "gpt-4o-mini"]},
    {"id": "custom", "name": "自定义", "base_url": "", "models": []},
]


class LLMBody(BaseModel):
    api_key: str | None = None  # 不传表示保持原来的 Key
    base_url: str | None = None
    model: str | None = None
    thinking_enabled: bool | None = None


def _view(cfg: LLMSettings) -> dict[str, Any]:
    return {"configured": cfg.configured, "base_url": cfg.base_url, "model": cfg.model, "key_hint": cfg.key_hint(),
            "default_model": DEEPSEEK_DEFAULT_MODEL, "providers": PROVIDERS, "thinking_enabled": cfg.thinking_enabled}


@router.get("/llm")
def get_llm_config(_: User = Depends(current_user)) -> dict[str, Any]:
    return {"ok": True, **_view(get_llm_settings())}


@router.put("/llm")
def put_llm_config(body: LLMBody, _: User = Depends(current_user)) -> dict[str, Any]:
    cfg = save_llm_settings(body.api_key, body.base_url, body.model, body.thinking_enabled)
    return {"ok": True, **_view(cfg)}


@router.post("/llm/test")
async def test_llm(body: LLMBody, _: User = Depends(current_user)) -> dict[str, Any]:
    current = get_llm_settings()
    cfg = replace(current, api_key=body.api_key or current.api_key, base_url=body.base_url or current.base_url,
                  model=body.model or current.model,
                  thinking_enabled=current.thinking_enabled if body.thinking_enabled is None else body.thinking_enabled)
    try:
        result = await test_connection(cfg)
    except LLMError as exc:
        return {"ok": False, "error": {"type": "llm", "message": str(exc)}}
    return {"ok": True, **result}
