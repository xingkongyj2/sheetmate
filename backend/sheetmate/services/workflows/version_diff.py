"""流程版本对比：某个版本和上一版相比改了什么（基本信息、输入、处理节点、输出、核对规则、列名对应）。

只比较用户能理解的处理定义；脚本按代码内容比较（每个版本的脚本 ID 都不同，内容可能一样），给出逐行差异。
"""

from __future__ import annotations

import difflib
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sheetmate.db.models import RecipeRecord
from sheetmate.engine.scripts.store import resolve_script_code
from sheetmate.services.workflows import service as recipe_service

MAX_DIFF_LINES = 600
_CODE_KEYS = {"script_id", "script_title", "script_filename", "code", "sql"}


def version_changes(db: Session, recipe_id: str, version: int) -> dict[str, Any]:
    """version 和它之前最近的一个版本对比；第一版没有可对比的版本，sections 为空。"""
    record = recipe_service.get_record(db, recipe_id, version)
    base = db.scalars(select(RecipeRecord).where(RecipeRecord.recipe_id == recipe_id, RecipeRecord.version < version)
                      .order_by(RecipeRecord.version.desc()).limit(1)).first()
    if base is None:
        return {"version": version, "base_version": None, "sections": []}
    return {"version": version, "base_version": base.version,
            "sections": diff_bodies(base.body or {}, record.body or {})}


def diff_bodies(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    sections = [
        ("basic", "基本信息", _basic(before, after)),
        ("inputs", "输入", _inputs(before.get("inputs") or [], after.get("inputs") or [])),
        ("steps", "处理节点", _steps(before.get("steps") or [], after.get("steps") or [])),
        ("outputs", "输出", _outputs(before.get("output_spec") or [], after.get("output_spec") or [])),
        ("rules", "核对规则", _rules(before.get("validation_rules") or [], after.get("validation_rules") or [])),
        ("mapping", "列名对应", _mapping(before.get("field_mapping") or {}, after.get("field_mapping") or {})),
    ]
    return [{"key": key, "title": title, "items": items} for key, title, items in sections if items]


# ---------------------------------------------------------------------------


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _item(kind: str, title: str, *, details: list[str] | None = None, before: str = "", after: str = "",
          code: dict[str, Any] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"kind": kind, "title": title, "details": details or []}
    if before or after:
        item["before"], item["after"] = before, after
    if code:
        item["code"] = code
    return item


def _basic(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    items = []
    if _text(before.get("name")) != _text(after.get("name")):
        items.append(_item("modified", "流程名称", before=_text(before.get("name")), after=_text(after.get("name"))))
    if _text(before.get("description")) != _text(after.get("description")):
        items.append(_item("modified", "说明", before=_text(before.get("description")), after=_text(after.get("description"))))
    return items


def _list_change(label: str, old: list[Any], new: list[Any]) -> str:
    added = [str(v) for v in new if v not in old]
    removed = [str(v) for v in old if v not in new]
    parts = ([f"新增 {'、'.join(added)}"] if added else []) + ([f"去掉 {'、'.join(removed)}"] if removed else [])
    return f"{label}：{'；'.join(parts)}" if parts else ""


def _inputs(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    key = lambda inp: inp.get("source_file") or inp.get("alias") or ""  # noqa: E731
    before = {key(inp): inp for inp in old}
    after = {key(inp): inp for inp in new}
    items = []
    for name, inp in after.items():
        if name not in before:
            items.append(_item("added", name))
            continue
        prev = before[name]
        details = []
        if (prev.get("kind") or "table") != (inp.get("kind") or "table"):
            label = lambda k: "整份文件" if k == "file" else "数据表"  # noqa: E731
            details.append(f"读取方式：{label(prev.get('kind'))} → {label(inp.get('kind'))}")
        if (prev.get("sheet") or "") != (inp.get("sheet") or ""):
            details.append(f"工作表：{prev.get('sheet') or '未指定'} → {inp.get('sheet') or '未指定'}")
        columns = _list_change("必需列", prev.get("required_columns") or [], inp.get("required_columns") or [])
        if columns:
            details.append(columns)
        if details:
            items.append(_item("modified", name, details=details))
    items += [_item("removed", name) for name in before if name not in after]
    return items


def _tool_label(step: dict[str, Any]) -> str:
    kind = step.get("kind") or ("python" if step.get("formula") == "python_script" else "sql" if step.get("formula") == "custom_sql" else "formula")
    if kind == "python":
        return "Python 脚本"
    if kind == "sql":
        return "SQL"
    return "客户公式" if step.get("formula_kind") == "customer" else "内置公式"


def _code(step: dict[str, Any]) -> tuple[str, str]:
    """(语言, 代码)：Python 脚本读脚本文件（读不到就用内联代码），SQL 读 sql。"""
    params = step.get("params") or {}
    if (step.get("kind") or "") == "python" or params.get("script_id") or params.get("code"):
        try:
            return "python", resolve_script_code(params)
        except (OSError, ValueError, KeyError):
            return "python", _text(params.get("code"))
    sql = params.get("sql") or step.get("sql")
    return ("sql", sql) if isinstance(sql, str) and sql.strip() else ("", "")


def _code_diff(language: str, old: str, new: str) -> dict[str, Any] | None:
    if old == new:
        return None
    lines = [line for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=2)
             if not line.startswith(("---", "+++"))]
    added = sum(1 for line in lines if line.startswith("+"))
    removed = sum(1 for line in lines if line.startswith("-"))
    return {"language": language, "added": added, "removed": removed,
            "diff": "\n".join(lines[:MAX_DIFF_LINES]), "truncated": len(lines) > MAX_DIFF_LINES}


def _step_title(step: dict[str, Any]) -> str:
    return f"第 {step.get('id')} 步 · {_text(step.get('name')) or '未命名'}"


def _steps(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    before = {step.get("id"): step for step in old}
    after = {step.get("id"): step for step in new}
    items = []
    for step_id in sorted(after, key=lambda value: (value is None, value)):
        step = after[step_id]
        prev = before.get(step_id)
        if prev is None:
            language, code = _code(step)
            items.append(_item("added", _step_title(step), details=[_tool_label(step)],
                               after=_text(step.get("description")),
                               code=_code_diff(language, "", code) if code else None))
            continue
        details = []
        if _text(prev.get("name")) != _text(step.get("name")):
            details.append(f"名称：{_text(prev.get('name'))} → {_text(step.get('name'))}")
        if _tool_label(prev) != _tool_label(step):
            details.append(f"类型：{_tool_label(prev)} → {_tool_label(step)}")
        elif (prev.get("formula") or "") != (step.get("formula") or "") and _tool_label(step).endswith("公式"):
            details.append(f"公式：{prev.get('formula') or '无'} → {step.get('formula') or '无'}")
        if (prev.get("output") or "") != (step.get("output") or ""):
            details.append(f"输出表：{prev.get('output') or '无'} → {step.get('output') or '无'}")
        if prev.get("enabled", True) != step.get("enabled", True):
            details.append("已启用" if step.get("enabled", True) else "已停用")
        old_params = {k: v for k, v in (prev.get("params") or {}).items() if k not in _CODE_KEYS}
        new_params = {k: v for k, v in (step.get("params") or {}).items() if k not in _CODE_KEYS}
        changed = [k for k in dict.fromkeys([*old_params, *new_params]) if old_params.get(k) != new_params.get(k)]
        if changed:
            details.append(f"参数修改：{'、'.join(changed)}")
        old_language, old_code = _code(prev)
        new_language, new_code = _code(step)
        code = _code_diff(new_language or old_language, old_code, new_code)
        description_changed = _text(prev.get("description")) != _text(step.get("description"))
        if details or code or description_changed:
            items.append(_item("modified", _step_title(step), details=details,
                               before=_text(prev.get("description")) if description_changed else "",
                               after=_text(step.get("description")) if description_changed else "",
                               code=code))
    for step_id, prev in before.items():
        if step_id not in after:
            items.append(_item("removed", _step_title(prev)))
    return items


def _outputs(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    before = {out.get("name"): out for out in old}
    after = {out.get("name"): out for out in new}
    items = []
    for name, out in after.items():
        prev = before.get(name)
        if prev is None:
            items.append(_item("added", name, after=_text(out.get("description"))))
            continue
        details = []
        if (prev.get("source") or "") != (out.get("source") or ""):
            details.append(f"数据来源：{prev.get('source') or '无'} → {out.get('source') or '无'}")
        columns = _list_change("列", prev.get("columns") or [], out.get("columns") or [])
        if columns:
            details.append(columns)
        description_changed = _text(prev.get("description")) != _text(out.get("description"))
        if details or description_changed:
            items.append(_item("modified", name, details=details,
                               before=_text(prev.get("description")) if description_changed else "",
                               after=_text(out.get("description")) if description_changed else ""))
    items += [_item("removed", name) for name in before if name not in after]
    return items


def _rules(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    label = lambda rule: _text(rule.get("description")) or _text(rule.get("type"))  # noqa: E731
    before = [label(rule) for rule in old]
    after = [label(rule) for rule in new]
    return ([_item("added", text) for text in after if text not in before]
            + [_item("removed", text) for text in before if text not in after])


def _mapping(old: dict[str, list[str]], new: dict[str, list[str]]) -> list[dict[str, Any]]:
    items = []
    for standard, aliases in new.items():
        if standard not in old:
            items.append(_item("added", standard, details=[f"对应 {'、'.join(aliases)}"]))
        elif old[standard] != aliases:
            items.append(_item("modified", standard, details=[_list_change("原列名", old[standard], aliases)]))
    items += [_item("removed", standard) for standard in old if standard not in new]
    return items
