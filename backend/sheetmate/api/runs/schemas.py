"""HTTP 请求数据契约。"""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel


class RerunBody(BaseModel):
    params: dict[str, Any] | None = None
    save_recipe: bool = False

