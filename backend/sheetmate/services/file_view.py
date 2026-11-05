"""Resolve semantic file selections against the conversation's existing file catalog.

No workbook reads, exports or new records: the frontend receives the original file identity.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from sheetmate.agent.intent import FileSelection
from sheetmate.agent.llm import LLMError, is_llm_not_configured_message
from sheetmate.agent.prompts import FILE_VIEW_REPLY_PROMPT
from sheetmate.db.database import session_scope
from sheetmate.db.models import Message
from sheetmate.services.errors import ServiceError


def file_references(conversation_id: str, message_id: int, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    allowed = {file["file_id"] for file in files}
    with session_scope() as db:
        messages = list(db.scalars(select(Message).where(Message.conversation_id == conversation_id, Message.id <= message_id)
                                  .order_by(Message.id.desc()).limit(12)))
        references = []
        for message in reversed(messages):
            if is_llm_not_configured_message(message.role, message.content, message.events):
                continue
            ids = [file_id for file_id in (message.meta or {}).get("attachments", []) if file_id in allowed]
            ids += [event["file_id"] for event in message.events or []
                    if event.get("type") == "file" and event.get("file_id") in allowed]
            references.append({"role": message.role, "content": (message.content or "")[-1500:],
                               "file_ids": list(dict.fromkeys(ids))})
    return references


def select_files(selection: FileSelection, files: list[dict[str, Any]],
                 references: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {file["file_id"]: file for file in files}
    if any(file_id not in by_id for file_id in selection.file_ids):
        raise ServiceError("没有找到指定文件，或该文件不属于当前会话或客户。请确认文件名或查看范围。")
    candidates = [by_id[file_id] for file_id in selection.file_ids] if selection.file_ids else list(by_id.values())
    if selection.kinds:
        candidates = [file for file in candidates if file.get("kind") in selection.kinds]
    if selection.selection == "selected":
        return candidates if selection.file_ids else []
    if selection.selection == "latest":
        return sorted(candidates, key=lambda file: file.get("created_at") or "", reverse=True)[:1]
    if selection.selection == "recent":
        candidates_by_id = {file["file_id"]: file for file in candidates}
        for reference in reversed(references):
            selected = [candidates_by_id[file_id] for file_id in reference.get("file_ids", []) if file_id in candidates_by_id]
            if selected:
                return selected
        return []
    return candidates


async def file_view_answer(llm: Any, message: str, selection: FileSelection, files: list[dict[str, Any]]) -> str:
    """Describe the files already delivered, without giving the model processing tools."""
    context = {
        "request": message,
        "selection": selection.selection,
        "kinds": list(selection.kinds),
        "files": [{"filename": file["filename"], "kind": file.get("kind"),
                   "sheets": [sheet.get("sheet") for sheet in file.get("sheets", [])]}
                  for file in files],
    }
    try:
        reply = await llm.chat([{"role": "system", "content": FILE_VIEW_REPLY_PROMPT},
                               {"role": "user", "content": json.dumps(context, ensure_ascii=False)}], max_tokens=300)
        answer = (reply.content or "").strip()
    except LLMError:
        if not files:
            raise
        # The original file cards are the delivery; a failed description cannot revoke them.
        return ""
    return answer or ("" if files else "当前范围内没有找到对应文件。")
