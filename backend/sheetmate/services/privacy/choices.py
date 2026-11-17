"""用户在文件的脱敏维护页里手动做的列选择，和自动识别结果合在一起。

- mask：这一列一定脱敏（整列换成令牌），类型由用户选（联系方式 / 详细地址）；
- keep：这一列不按整列脱敏（自动识别误判时用）。列里夹着的明确手机号、地址仍会按单元格替换，这是发给 AI 前的底线；
- 没有选择：照自动识别（规则 + 脱敏模型看列头和内容）的结果走。
选择记在文件上，识别结果重新生成也不会丢；改动后下一轮对话开始时，工作区里这个文件的表会按新的选择重新生成。
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import delete, select

from sheetmate.db.models import PrivacyColumnChoice
from sheetmate.db.session import session_scope
from sheetmate.privacy import ACTION_LABELS, ACTIONS, KIND_LABELS, privacy

# 手动脱敏时可选的类型：电话号码、详细地址（和自动识别的两条规则一样）、通用（兜底：整列每一格都换成令牌，只能手动选，自动识别不会用它）
MANUAL_KINDS = ("phone", "address", "custom")
DEFAULT_KIND = "custom"


def load(file_id: str) -> dict[tuple[str, str], tuple[str, str | None, int]]:
    """{(工作表, 列名): (选择, 类型, 值数量)}"""
    with session_scope() as db:
        rows = db.scalars(select(PrivacyColumnChoice).where(PrivacyColumnChoice.file_id == file_id))
        return {(row.sheet, row.column): (row.choice, row.kind, row.values) for row in rows}


REVISION = "2"       # 脱敏处理方式变了就加一：工作区里按旧方式生成的表会按新方式重新生成


def signature(file_id: str) -> str:
    """这个文件的列选择的指纹：选择变了指纹就变，用来判断工作区里的表是不是过期了。"""
    picks = load(file_id)
    if not picks:
        return REVISION
    return REVISION + hashlib.sha1(repr(sorted(picks.items())).encode()).hexdigest()[:12]


def _entry(sheets: list[dict[str, Any]], name: str) -> dict[str, Any]:
    found = next((item for item in sheets if item["sheet"] == name), None)
    if found is None:
        found = {"sheet": name, "rows": 0, "columns": []}
        sheets.append(found)
    return found


def _manual_column(name: str, kind: str, values: int) -> dict[str, Any]:
    action = ACTIONS[kind]
    return {"name": name, "kind": kind, "kind_label": KIND_LABELS[kind], "action": action, "action_label": ACTION_LABELS[action],
            "source": "manual", "reason": "用户手动设置脱敏", "values": values}


def effective(file_id: str, sheets: list[dict[str, Any]], *, for_masking: bool = False) -> list[dict[str, Any]]:
    """自动识别结果 + 用户的选择。for_masking：给「生成令牌版」用，不脱敏的列也要记下来（不然会按列名被别处的识别结果误伤）。"""
    picks = load(file_id)
    if not picks:
        return sheets
    out = [{**sheet, "columns": list(sheet["columns"])} for sheet in sheets]
    guard = privacy.guard
    for (sheet_name, column), (choice, kind, values) in picks.items():
        if choice == "keep" and (any(item["name"] == column for sheet in sheets if sheet["sheet"] == sheet_name for item in sheet["columns"])
                                 or (guard is not None and guard.registry.kind_for(column))):
            continue                       # 自动识别出来的列不支持手动关闭（旧版本留下的「不脱敏」选择不再生效）
        entry = _entry(out, sheet_name)
        entry["columns"] = [item for item in entry["columns"] if item["name"] != column]
        if choice == "mask" and kind in KIND_LABELS:
            entry["columns"].append(_manual_column(column, kind, values))
        elif for_masking:
            entry["kept"] = [*entry.get("kept", []), column]
    if not for_masking:
        out = [sheet for sheet in out if sheet["columns"]]      # 展示用：没有被保护的列的工作表不列出
    return out


def save(file_id: str, changes: list[dict[str, Any]]) -> None:
    """changes：[{sheet, column, choice: 'mask'|'keep'|None, kind, values}]；choice 为空就是恢复成自动识别。"""
    with session_scope() as db:
        for change in changes:
            db.execute(delete(PrivacyColumnChoice).where(
                PrivacyColumnChoice.file_id == file_id, PrivacyColumnChoice.sheet == change["sheet"],
                PrivacyColumnChoice.column == change["column"]))
            if change.get("choice") in {"mask", "keep"}:
                db.add(PrivacyColumnChoice(file_id=file_id, sheet=change["sheet"], column=change["column"], choice=change["choice"],
                                           kind=change.get("kind") if change["choice"] == "mask" else None,
                                           values=int(change.get("values") or 0)))
