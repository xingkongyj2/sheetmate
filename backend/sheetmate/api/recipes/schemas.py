"""HTTP 请求数据契约。"""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel

from sheetmate.api.customers.schemas import TrialInput


class GlobalTrialBody(BaseModel):
    """内置公式试运行：按 id 取公式，表是直接上传的文件。"""

    formula_id: str
    params: dict[str, Any] = {}
    inputs: list[TrialInput] = []


class MatchBody(BaseModel):
    file_ids: list[str]


class RunBody(BaseModel):
    file_ids: list[str]
    inputs: dict[str, str] | None = None
    allow_repair: bool = True
    version: int | None = None
    save_recipe: bool = False
    scoped: bool = False          # 客户页「执行」发起：输入输出只记在这次执行里，不进文件页
    label: str | None = None      # 结果文件名里带上的标识（批量执行时用输入文件名区分每次的结果）


class NodeEditBody(BaseModel):
    """按节点直接修改流程：每项 {op, ...}，op 见 /recipes/{id}/nodes 的说明。"""

    edits: list[dict[str, Any]]
    base_version: int | None = None
    note: str = ""
