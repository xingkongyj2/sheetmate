"""HTTP 请求数据契约。"""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class TrialInput(BaseModel):
    """试运行用的一张表：客户的文件（可选 sheet），或者粘贴的表格文本（第一行是表头）。"""

    table: str  # 在试运行工作区里的表名，公式的「表」参数填这个名字
    file_id: str | None = None
    sheet: str | None = None
    text: str | None = None


class TrialBody(BaseModel):
    formula: dict[str, Any]  # 完整的公式定义（可以是还没保存的）
    params: dict[str, Any] = {}  # 各个参数的取值
    inputs: list[TrialInput] = []


class FormulaStatusBody(BaseModel):
    status: str  # active / deprecated


class FormulaGenerateBody(BaseModel):
    description: str = Field(min_length=1, max_length=12_000)
    file_ids: list[str] = Field(default_factory=list, max_length=5)
    formula: dict[str, Any] | None = None


class CustomerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    code: str | None = None
    description: str | None = None
    mascot: str | None = None
    primary_recipe_id: str | None = None


class MappingBody(BaseModel):
    mapping: dict[str, list[str]]


class MemoryBody(BaseModel):
    category: str = "domain_knowledge"
    key: str | None = None
    value: str = Field(min_length=1, max_length=12_000)

