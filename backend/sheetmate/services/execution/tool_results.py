"""Durable scoped tool output storage and bounded Unicode pagination.

Only credentials, raw reasoning and inline binary/image content are removed.
Business contacts, numbers, scripts and source file identifiers remain exact.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from sheetmate.core.security.payload import canonical, sanitize_tool_payload
from sheetmate.db.models import AgentExecution, AgentToolOutput, User
from sheetmate.services.conversations.service import get_conversation
from sheetmate.core.errors import Forbidden, NotFound, ServiceError

_UNSET = object()
_RESULT_ID = re.compile(r"^tr_[a-f0-9]{64}$")
SECTIONS = frozenset({"text", "summary", "tables", "events", "public_text", "public_summary",
                      "public_details", "error_details", "payload"})
MAX_PAGE_CHARS = 8000
MAX_PAGE_JSON_CHARS = 12000


def _scope(db: Session, user_id: int, conversation_id: str, expected_customer_id: Any):
    user = db.get(User, user_id)
    if user is None:
        raise Forbidden("当前用户不存在。")
    conversation = get_conversation(db, user, conversation_id)
    if expected_customer_id is not _UNSET and conversation.customer_id != expected_customer_id:
        raise Forbidden("会话的客户范围已变化，请重新加载后重试。")
    return conversation


def save_tool_result(db: Session, *, user_id: int, conversation_id: str, tool_name: str,
                     call_id: str, payload: dict[str, Any], execution_id: str | None = None,
                     branch_id: str | None = None, expected_customer_id: Any = _UNSET) -> dict[str, Any]:
    conversation = _scope(db, user_id, conversation_id, expected_customer_id)
    if execution_id is not None:
        execution = db.get(AgentExecution, execution_id)
        if execution is None or execution.conversation_id != conversation_id or execution.user_id != user_id:
            raise Forbidden("工具结果不属于当前执行。")
    safe, count = sanitize_tool_payload(payload)
    encoded = canonical(safe)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    identity = canonical([user_id, conversation_id, conversation.customer_id, execution_id,
                          call_id, tool_name, branch_id, digest])
    result_id = "tr_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    db.execute(insert(AgentToolOutput).values(
        id=result_id, conversation_id=conversation_id, user_id=user_id,
        customer_id=conversation.customer_id, execution_id=execution_id,
        call_id=call_id, tool_name=tool_name[:128], branch_id=branch_id, payload=encoded,
        digest=digest, characters=len(encoded), sanitized_fields=count,
    ).on_conflict_do_nothing(index_elements=["id"]))
    db.flush()
    return {"result_id": result_id, "sha256": digest, "total_chars": len(encoded),
            "sections": sorted((set(safe) & SECTIONS) | {"payload"}), "sanitized_fields": count}


def read_tool_result(db: Session, *, user_id: int, conversation_id: str, result_id: str,
                     section: str = "text", offset: int = 0, limit: int = 4000,
                     branch_id: str | None = None, expected_customer_id: Any = _UNSET) -> dict[str, Any]:
    # Authorization precedes even result-ID validation or lookup. Saved IDs are
    # locators and confer no authority after customer access is revoked.
    conversation = _scope(db, user_id, conversation_id, expected_customer_id)
    if not isinstance(result_id, str) or not _RESULT_ID.fullmatch(result_id):
        raise NotFound("工具结果不存在。")
    output = db.get(AgentToolOutput, result_id)
    if (output is None or output.conversation_id != conversation_id
            or output.customer_id != conversation.customer_id
            or (branch_id is not None and output.branch_id != branch_id)):
        raise NotFound("工具结果不存在或不属于当前范围。")
    if section not in SECTIONS:
        raise ServiceError("不支持的工具结果分区。")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ServiceError("读取起点必须为非负整数。")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ServiceError("读取长度必须为正整数。")
    limit = min(limit, MAX_PAGE_CHARS)
    payload = json.loads(output.payload)
    if section != "payload" and section not in payload:
        raise NotFound("这个工具结果没有所请求的分区。")
    value = payload if section == "payload" else payload[section]
    content = value if isinstance(value, str) else canonical(value)
    total = len(content)
    page = {"result_id": result_id, "section": section,
            "encoding": "text" if isinstance(value, str) else "json",
            "content": content[offset:offset + limit], "offset": offset, "total_chars": total,
            "next_offset": None, "complete": False, "sha256": output.digest,
            "sanitized_fields": output.sanitized_fields}
    # Quotes, slashes and control characters can expand JSON enormously even
    # when a single Unicode field respects the ordinary character page limit.
    while len(canonical(page)) > MAX_PAGE_JSON_CHARS and page["content"]:
        page["content"] = page["content"][:max(1, len(page["content"]) // 2)]
    end = offset + len(page["content"])
    page["complete"] = end >= total
    page["next_offset"] = None if page["complete"] else end
    return page


def resolve_tool_result_events(db: Session, *, user_id: int, conversation_id: str, result_id: str,
                               branch_id: str | None = None,
                               expected_customer_id: Any = _UNSET) -> list[dict[str, Any]]:
    """Recover UI/control events internally, never injecting them into a model.

    This deliberately returns the full saved event structure so delivery cards
    and recipe flows survive checkpoint recovery. It has the same live scope
    checks as paginated model/user reads; the locator grants no permissions.
    """
    conversation = _scope(db, user_id, conversation_id, expected_customer_id)
    if not isinstance(result_id, str) or not _RESULT_ID.fullmatch(result_id):
        raise NotFound("工具结果不存在。")
    output = db.get(AgentToolOutput, result_id)
    if (output is None or output.conversation_id != conversation_id
            or output.customer_id != conversation.customer_id
            or (branch_id is not None and output.branch_id != branch_id)):
        raise NotFound("工具结果不存在或不属于当前范围。")
    payload = json.loads(output.payload)
    events = payload.get("events") or []
    if not isinstance(events, list):
        raise ServiceError("工具结果事件格式无效。")
    tables = payload.get("tables") or []
    return [*({"type": "table", "table": table} for table in tables if isinstance(table, dict)),
            *(event for event in events if isinstance(event, dict))]
