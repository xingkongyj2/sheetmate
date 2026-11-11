"""HTTP 请求数据契约。"""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel


class MatchBody(BaseModel):
    file_ids: list[str]


class RunBody(BaseModel):
    file_ids: list[str]
    inputs: dict[str, str] | None = None
    allow_repair: bool = True
    version: int | None = None
    save_recipe: bool = False

