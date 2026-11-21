"""管理后台请求契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class CreateUserBody(BaseModel):
    account: str
    password: str
    display_name: str = ""
    role: Literal["admin", "member"] = "member"
    customer_ids: list[str] | None = None


class UpdateUserBody(BaseModel):
    display_name: str | None = None
    role: Literal["admin", "member"] | None = None
    password: str | None = None
    customer_ids: list[str] | None = None


class CustomerMembersBody(BaseModel):
    user_ids: list[int]
