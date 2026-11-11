"""HTTP 请求数据契约。"""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel


class CreateBody(BaseModel):
    customer_id: str | None = None
    recipe_id: str | None = None
    title: str = ""


class UpdateBody(BaseModel):
    title: str | None = None
    customer_id: str | None = None
    recipe_id: str | None = None


class BindBody(BaseModel):
    customer_id: str | None = None
    new_customer: dict[str, Any] | None = None


class ChatBody(BaseModel):
    message: str
    plan_steps: list[dict[str, Any]] | None = None  # 调用方自带的计划步骤（高级用法；界面不再让用户确认计划）
    attachments: list[str] | None = None
    thinking: bool = False  # 思考模式：后端先规划、再逐步执行并核对，不需要用户确认计划


class MessageBody(BaseModel):
    message: str
    attachments: list[str] | None = None


class LoadFileBody(BaseModel):
    file_id: str


class RoleBody(BaseModel):
    role: str

