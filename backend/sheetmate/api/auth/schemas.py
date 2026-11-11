"""HTTP 请求数据契约。"""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel


class LoginBody(BaseModel):
    account: str
    password: str


class RegisterBody(LoginBody):
    display_name: str = ""


class ProfileBody(BaseModel):
    display_name: str | None = None
    profile: dict[str, Any] | None = None

