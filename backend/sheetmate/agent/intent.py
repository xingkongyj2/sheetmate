from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sheetmate.agent.llm import LLMError
from sheetmate.agent.prompts import INTENT_PROMPT
from sheetmate.agent.tools import AgentContext


@dataclass(frozen=True)
class FileSelection:
    selection: str
    file_ids: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequestIntent:
    mode: str
    files: FileSelection | None = None
    action: str = "process"


def _file_selection(value: Any) -> FileSelection:
    if not isinstance(value, dict) or not isinstance(value.get("selection"), str) or value["selection"] not in {"selected", "all", "latest", "recent"}:
        raise LLMError("暂时无法确定要查看哪些文件，请说明文件名或查看范围。")
    file_ids, kinds = value.get("file_ids", []), value.get("kinds", [])
    if not isinstance(file_ids, list) or not all(isinstance(file_id, str) and file_id for file_id in file_ids):
        raise LLMError("暂时无法确定要查看哪些文件，请说明文件名或查看范围。")
    if not isinstance(kinds, list) or any(not isinstance(kind, str) or kind not in {"input", "expected", "output"} for kind in kinds):
        raise LLMError("暂时无法确定要查看的文件类型，请说明是输入文件还是输出文件。")
    return FileSelection(value["selection"], tuple(dict.fromkeys(file_ids)), tuple(dict.fromkeys(kinds)))


async def infer_request(llm: Any, ctx: AgentContext, message: str, history: list[dict[str, Any]], *,
                        files: list[dict[str, Any]] | None = None,
                        file_references: list[dict[str, Any]] | None = None) -> RequestIntent:
    context = {
        "history": [{"role": item["role"], "content": str(item.get("content", ""))[-4000:]} for item in history[-8:]],
        "tables": [{"name": table.name, "role": table.role, "columns": table.column_names,
                    "file_id": table.file_id, "filename": table.filename, "created_by": table.created_by}
                   for table in ctx.visible_tables()],
        "files": [{key: file.get(key) for key in ("file_id", "filename", "kind", "created_at", "scope_label", "tables", "media_type")}
                  | {"sheets": [sheet.get("sheet") for sheet in file.get("sheets", [])]}
                  for file in files or []],
        "file_references": file_references or [],
        "message": message,
    }
    image_parts = [part for item in history[-8:] for part in item.get("image_parts", [])] + ctx.image_parts
    image_kwargs = {"image_parts": image_parts} if image_parts else {}
    result = await llm.complete_json(json.dumps(context, ensure_ascii=False), system=INTENT_PROMPT, **image_kwargs)
    mode = result.get("mode") if isinstance(result, dict) else None
    if not isinstance(mode, str) or mode not in {"analyze", "modify", "create"}:
        raise LLMError("暂时无法确定这次需求的处理方式，本次尚未处理文件，请重试。")
    action = result.get("action", "process")
    if action == "chat":
        return RequestIntent("analyze", action="chat")
    if action == "show_files":
        return RequestIntent("analyze", _file_selection(result.get("files")), action="show_files")
    if action != "process":
        raise LLMError("暂时无法确定这次需求的处理方式，本次尚未处理文件，请重试。")
    return RequestIntent(mode)


async def infer_mode(llm: Any, ctx: AgentContext, message: str, history: list[dict[str, Any]]) -> str:
    """Compatibility for callers that only need the processing mode."""
    return (await infer_request(llm, ctx, message, history)).mode
