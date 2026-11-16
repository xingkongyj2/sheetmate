"""模型设置 HTTP 接口。业务配置和连接验证由服务层处理。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response

from sheetmate.api.deps import current_user
from sheetmate.api.settings.schemas import LLMBody
from sheetmate.db.models import User
from sheetmate.services.settings import service as settings_service

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("/llm")
def get_llm_config(response: Response, _: User = Depends(current_user)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, **settings_service.get_llm_config()}


@router.put("/llm")
def put_llm_config(body: LLMBody, response: Response, _: User = Depends(current_user)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    update = settings_service.ModelSettingsUpdate(**dict(body))
    return {"ok": True, **settings_service.put_llm_config(update)}


@router.post("/llm/test")
async def test_llm(body: LLMBody, response: Response, _: User = Depends(current_user)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    update = settings_service.ModelSettingsUpdate(**dict(body))
    return await settings_service.test_llm_config(update)


# 脱敏模型：专门给脱敏模块识别联系方式和地址用，和对话模型分开保存，接口、字段完全一样。
@router.get("/privacy-llm")
def get_privacy_llm_config(response: Response, _: User = Depends(current_user)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, **settings_service.get_llm_config(settings_service.PRIVACY_LLM_ROOT)}


@router.put("/privacy-llm")
def put_privacy_llm_config(body: LLMBody, response: Response, _: User = Depends(current_user)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    update = settings_service.ModelSettingsUpdate(**dict(body))
    return {"ok": True, **settings_service.put_llm_config(update, settings_service.PRIVACY_LLM_ROOT)}


@router.post("/privacy-llm/test")
async def test_privacy_llm(body: LLMBody, response: Response, _: User = Depends(current_user)) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    update = settings_service.ModelSettingsUpdate(**dict(body))
    return await settings_service.test_llm_config(update, settings_service.PRIVACY_LLM_ROOT)
