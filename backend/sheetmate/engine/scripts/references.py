"""把展示和审计数据中的 Python 源码替换成独立脚本文件引用。"""

from __future__ import annotations

import json
from typing import Any

from sheetmate.engine.scripts.store import bind_script, script_params

_PYTHON_TOOLS = {"run_python_script", "inspect_python_script", "python_script"}


def externalize_script_text(text: str, *, python: bool = False) -> str:
    """工具返回可以是 JSON 后接说明；只处理可识别的 JSON 数据。"""
    first, separator, rest = text.partition("\n")
    for raw, suffix in ((text, ""), (first, separator + rest)):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            continue
        updated = externalize_script_references(parsed, python=python)
        if updated != parsed:
            return json.dumps(updated, ensure_ascii=False) + suffix
        return text
    return text


def externalize_script_references(value: Any, *, python: bool = False, title: str = "") -> Any:
    """递归迁移旧运行、节点和事件，原数据不就地修改。"""
    if isinstance(value, list):
        return [externalize_script_references(item, python=python, title=title) for item in value]
    if not isinstance(value, dict):
        return value
    tool = value.get("tool")
    python = python or (isinstance(tool, str) and tool in _PYTHON_TOOLS) or value.get("formula") == "python_script" or value.get("kind") == "python"
    title = str(value.get("script_title") or value.get("title") or value.get("name") or value.get("description") or value.get("label") or title)
    item = dict(value)
    if python and isinstance(item.get("code"), str):
        try:
            item = script_params(item, title=title)
        except (ValueError, OSError) as exc:
            # 无效调用也不能把一大段源码复制进每份事件；错误仍交由原工具返回。
            item.pop("code", None)
            item["script_error"] = str(exc)
    for key, field in list(item.items()):
        if isinstance(field, (dict, list)):
            item[key] = externalize_script_references(
                field, python=python and key in {"params", "debug_params", "report", "attempts", "steps", "request", "response"}, title=title,
            )
        elif key in {"debug_result", "text"} and isinstance(field, str):
            item[key] = externalize_script_text(field, python=python)
    return item


def bind_script_references(value: Any, **scope: Any) -> None:
    """新建的脚本随所属记录绑定权限；已有脚本的来源不会被改写。"""
    if isinstance(value, list):
        for item in value:
            bind_script_references(item, **scope)
    elif isinstance(value, dict):
        if isinstance(value.get("script_id"), str):
            try:
                bind_script(value["script_id"], **scope)
            except (ValueError, OSError):
                # 保留损坏引用，让查看器显示具体错误，不阻断整条历史记录。
                pass
        for field in value.values():
            if isinstance(field, (dict, list)):
                bind_script_references(field, **scope)
            elif isinstance(field, str) and field.lstrip().startswith("{"):
                try:
                    bind_script_references(json.loads(field.split("\n", 1)[0]), **scope)
                except ValueError:
                    pass
