"""Shared conversational workflow edit request contract."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RecipeEditTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["workflow", "input", "output", "step"]
    aliases: list[str] | None = Field(default=None, max_length=100)
    name: str | None = Field(default=None, max_length=512)
    source: str | None = Field(default=None, max_length=512)
    sources: list[str] | None = Field(default=None, max_length=100)
    step_id: int | None = Field(default=None, ge=1, strict=True)
    focus: Literal["sql", "python", "formula", "name"] | None = None  # 节点里的「AI 修改」：只改这一步的 SQL / 脚本 / 公式，或只改这个输入/输出文件的名字


class RecipeEditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipe_id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1, strict=True)
    target: RecipeEditTarget
    operation: Literal["modify", "add", "remove", "replace"] = "modify"
