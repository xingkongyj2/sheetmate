"""Transactional checkpoints and receipts for the agent loop.

These helpers flush but never commit. The loop must commit a started receipt
before executing its tool, and commit a recovery claim before contacting a
model. Recovery still uses the normal conversation lock and rechecks customer
authorization in the caller; a saved request never grants fresh permissions.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from sheetmate.db.models import AgentExecution, AgentToolReceipt, Conversation, Message, User, now
from sheetmate.services.errors import Conflict, Forbidden, NotFound, ServiceError


SCHEMA_VERSION = 1
DEFAULT_RECOVERY_WINDOW = timedelta(hours=4)
RECOVERABLE_STATUSES = frozenset({"running", "interrupted", "paused"})
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "expired", "blocked"})
STATUSES = RECOVERABLE_STATUSES | TERMINAL_STATUSES
MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
_FORBIDDEN_KEYS = frozenset({
    "apikey", "accesstoken", "refreshtoken", "authorization", "password", "passwordhash",
    "secret", "clientsecret", "privatekey", "reasoning", "reasoningcontent", "reasoningtext", "chainofthought", "thinking",
    "imagedata", "imagebytes", "inlineimage", "b64json", "base64",
})
_FORBIDDEN_SUFFIXES = ("apikey", "accesstoken", "refreshtoken", "clientsecret", "password", "passwordhash",
                       "privatekey", "reasoningcontent", "reasoningtext", "chainofthought", "imagebytes",
                       "imagedata", "base64")
_SECRET_VALUE = re.compile(
    r"(?:-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----|"
    r"\bBearer\s+[a-zA-Z0-9._~+/=-]{12,}|"
    r"(?<![a-zA-Z0-9_])(?:sk-(?:proj-|svcacct-)?[a-zA-Z0-9_-]{16,}|"
    r"gh[pousr]_[a-zA-Z0-9]{20,}|github_pat_[a-zA-Z0-9_]{20,}|AKIA[0-9A-Z]{16}|"
    r"eyJ[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,}\.[a-zA-Z0-9_-]{8,})(?![a-zA-Z0-9_])|"
    r"data:image/[a-zA-Z0-9.+-]+(?:;[a-zA-Z0-9=.+-]+)*,)", re.IGNORECASE
)
_ENCODED_SECRET = re.compile(
    r"[\"'](?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|"
    r"authorization|password|reasoning(?:_content)?)[\"']\s*:\s*[\"'][^\"']+",
    re.IGNORECASE,
)
_ASSIGNED_SECRET = re.compile(
    r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|client[_ -]?secret|"
    r"authorization|password)\b[\"']?\s*[=:：]\s*[\"'](?:Bearer\s+)?[^\"'\r\n]+[\"']",
    re.IGNORECASE,
)


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, RecursionError) as exc:
        raise ServiceError("执行状态必须是有效的 JSON。") from exc


def fingerprint(value: Any) -> str:
    """Stable digest of caller-supplied file hashes, table revisions or args."""
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _payload(value: Any, *, limit: int = MAX_CHECKPOINT_BYTES) -> dict[str, Any]:
    """Reject forbidden durable data, without rewriting authoritative evidence.

    Ordinary business contacts, identifiers, SQL and script code are preserved.
    This is a structural safeguard, not a general secret/PII classifier: callers
    must assemble a dedicated snapshot rather than serialize client objects.
    """
    if not isinstance(value, dict):
        raise ServiceError("执行状态必须是 JSON 对象。")

    def walk(item: Any, depth: int = 0, encoded_depth: int = 0) -> None:
        if depth > 80:
            raise ServiceError("执行状态嵌套过深。")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ServiceError("执行状态的字段名必须是字符串。")
                normalized = re.sub(r"[^a-z0-9]", "", key.lower())
                if normalized in _FORBIDDEN_KEYS or normalized.endswith(_FORBIDDEN_SUFFIXES):
                    raise ServiceError("执行状态不能包含密钥、原始推理或图片内容。")
                walk(child, depth + 1, encoded_depth)
        elif isinstance(item, list):
            for child in item:
                walk(child, depth + 1, encoded_depth)
        elif isinstance(item, str):
            if _SECRET_VALUE.search(item) or _ENCODED_SECRET.search(item) or _ASSIGNED_SECRET.search(item):
                raise ServiceError("执行状态不能包含密钥、原始推理或图片内容。")
            # Tool arguments are often nested JSON strings. Only parse actual
            # JSON shells, never unescape arbitrary business code or text.
            stripped = item.strip()
            if stripped[:1] in {"{", "[", '"'}:
                try:
                    decoded = json.loads(stripped)
                except (ValueError, RecursionError):
                    pass
                else:
                    if encoded_depth >= 3:
                        raise ServiceError("执行状态中的 JSON 文本编码层数过多。")
                    walk(decoded, depth + 1, encoded_depth + 1)
        elif item is None or isinstance(item, (bool, int)):
            return
        elif isinstance(item, float) and math.isfinite(item):
            return
        else:
            raise ServiceError("执行状态不能包含字节、非有限数字或运行时对象。")

    walk(value)
    encoded = _canonical(value)
    if len(encoded.encode("utf-8")) > limit:
        raise ServiceError("执行检查点过大，请保存结果引用而非完整数据。")
    # Copy at the boundary so later in-memory mutation cannot rewrite the
    # checkpoint without passing through version checks again.
    return json.loads(encoded)


def validate_inputs(saved: dict[str, Any], current: dict[str, Any]) -> None:
    """Require the same inputs; names alone are not content fingerprints."""
    if fingerprint(saved) != fingerprint(current):
        raise Conflict("输入文件或工作表版本已经变化，无法从旧检查点继续。")


def recovery_input_fingerprint(db: Session, execution: AgentExecution, *,
                               checkpoint: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply only a receipt-backed binding change, preserving receipt digests."""
    inputs = dict(execution.input_fingerprint)
    binding = (execution.checkpoint if checkpoint is None else checkpoint).get("acknowledged_recipe_binding")
    if binding is None:
        return inputs
    if (not isinstance(binding, dict) or not isinstance(binding.get("call_id"), str)
            or "recipe_id" not in binding
            or (binding["recipe_id"] is not None and not isinstance(binding["recipe_id"], str))):
        raise Conflict("执行中的流程绑定变更记录无效，不能恢复。")
    receipt = db.scalar(select(AgentToolReceipt).where(
        AgentToolReceipt.execution_id == execution.id, AgentToolReceipt.call_id == binding["call_id"],
        AgentToolReceipt.status == "completed", AgentToolReceipt.side_effect.is_(True)))
    if receipt is None or (receipt.result or {}).get("ok") is False:
        raise Conflict("流程绑定变更缺少已确认的工具回执，不能恢复。")
    inputs["recipe_id"] = binding["recipe_id"]
    return inputs


def acknowledge_recipe_binding(
    db: Session, execution_id: str, checkpoint: dict[str, Any], *, user_id: int,
    call_id: str, previous_recipe_id: str | None, recipe_id: str | None,
) -> dict[str, Any]:
    """Attach a proven internal binding transition in the receipt's commit.

    The caller must prove that this tool changed its host binding and that the
    current conversation matches its expected result. Customer/file/table
    identities are never relaxed by this acknowledgement.
    """
    execution = get_execution(db, execution_id, user_id=user_id)
    if recovery_input_fingerprint(db, execution).get("recipe_id") != previous_recipe_id:
        raise Conflict("流程绑定已在工具执行之外变化，不能确认恢复范围。")
    candidate = {**checkpoint, "acknowledged_recipe_binding": {"call_id": call_id, "recipe_id": recipe_id}}
    # Validate the receipt before allowing the checkpoint to reference it.
    recovery_input_fingerprint(db, execution, checkpoint=candidate)
    return candidate


def get_execution(
    db: Session, execution_id: str, *, user_id: int, conversation_id: str | None = None
) -> AgentExecution:
    query = select(AgentExecution).where(AgentExecution.id == execution_id, AgentExecution.user_id == user_id)
    if conversation_id is not None:
        query = query.where(AgentExecution.conversation_id == conversation_id)
    execution = db.scalar(query)
    if execution is None:
        raise NotFound("执行记录不存在。")
    return execution


def _message(db: Session, message_id: int, conversation_id: str, role: str) -> Message:
    message = db.get(Message, message_id)
    if message is None or message.conversation_id != conversation_id or message.role != role:
        raise Conflict("执行消息不属于当前会话或消息角色不正确。")
    return message


def create_execution(
    db: Session,
    conversation_id: str,
    user_id: int,
    request_message_id: int,
    assistant_message_id: int | None = None,
    *,
    request: dict[str, Any] | None = None,
    checkpoint: dict[str, Any] | None = None,
    input_fingerprint: dict[str, Any] | None = None,
    expires_at: datetime | None = None,
) -> AgentExecution:
    conversation = db.get(Conversation, conversation_id)
    actor = db.get(User, user_id)
    if conversation is None or actor is None:
        raise NotFound("会话或用户不存在。")
    if conversation.user_id != user_id and actor.role != "admin":
        raise Forbidden("不能为其他用户的会话创建执行记录。")
    _message(db, request_message_id, conversation_id, "user")
    if assistant_message_id is not None:
        _message(db, assistant_message_id, conversation_id, "assistant")
    safe_request = _payload(request or {})
    safe_inputs = _payload(input_fingerprint or {})
    safe_checkpoint = _payload(checkpoint or {})

    def existing_request() -> AgentExecution | None:
        existing = db.scalar(select(AgentExecution).where(AgentExecution.request_message_id == request_message_id))
        if existing is not None:
            if (existing.conversation_id != conversation_id or existing.user_id != user_id
                    or fingerprint(existing.request) != fingerprint(safe_request)
                    or fingerprint(existing.input_fingerprint) != fingerprint(safe_inputs)):
                raise Conflict("同一请求消息已经属于另一次执行。")
        return existing

    existing = existing_request()
    if existing is not None:
        return existing
    values = dict(
        conversation_id=conversation_id, user_id=user_id, request_message_id=request_message_id,
        assistant_message_id=assistant_message_id, request=safe_request, checkpoint=safe_checkpoint,
        input_fingerprint=safe_inputs, expires_at=expires_at or (now() + DEFAULT_RECOVERY_WINDOW),
    )
    # Do not use SAVEPOINT here: SQLite's legacy deferred-BEGIN behavior can
    # commit an outermost savepoint before the caller commits its transaction.
    db.execute(insert(AgentExecution).values(**values).on_conflict_do_nothing(
        index_elements=["request_message_id"],
    ))
    execution = existing_request()
    assert execution is not None
    return execution


def _cas(
    db: Session, execution: AgentExecution, expected_version: int, values: dict[str, Any],
    *, allowed_statuses: frozenset[str] = RECOVERABLE_STATUSES,
) -> AgentExecution:
    result = db.execute(
        update(AgentExecution)
        .where(AgentExecution.id == execution.id, AgentExecution.version == expected_version,
               AgentExecution.status.in_(allowed_statuses))
        .values(**values, version=expected_version + 1, updated_at=now())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise Conflict("执行状态已被更新或已结束，请刷新后重试。")
    db.refresh(execution)
    return execution


def save_checkpoint(
    db: Session,
    execution_id: str,
    checkpoint: dict[str, Any],
    *,
    user_id: int,
    expected_version: int | None = None,
    status: str = "running",
    assistant_message_id: int | None = None,
) -> AgentExecution:
    if status not in STATUSES:
        raise ServiceError("执行状态不正确。")
    execution = get_execution(db, execution_id, user_id=user_id)
    if assistant_message_id is not None:
        _message(db, assistant_message_id, execution.conversation_id, "assistant")
    values: dict[str, Any] = {"checkpoint": _payload(checkpoint), "status": status}
    if assistant_message_id is not None:
        values["assistant_message_id"] = assistant_message_id
    return _cas(db, execution, execution.version if expected_version is None else expected_version, values)


def discover_recoverable(db: Session, *, limit: int = 100) -> list[AgentExecution]:
    """Use the status index; expiry/compatibility gates are checked at claim."""
    return list(db.scalars(select(AgentExecution).where(AgentExecution.status.in_(RECOVERABLE_STATUSES))
                           .order_by(AgentExecution.updated_at, AgentExecution.id).limit(max(1, min(limit, 1000)))))


def claim_recovery(
    db: Session,
    execution_id: str,
    *,
    user_id: int,
    expected_version: int,
    current_inputs: dict[str, Any],
    enabled: bool = True,
    max_attempts: int = 3,
    at: datetime | None = None,
) -> AgentExecution:
    """Claim under the conversation lock, then COMMIT before any remote work.

    Rejections do not silently mutate state. The caller may persist an expired
    or blocked terminal event and present the reason to the user.
    """
    execution = get_execution(db, execution_id, user_id=user_id)
    if not enabled:
        raise Conflict("自动恢复已关闭，检查点已保留。")
    if execution.status not in RECOVERABLE_STATUSES:
        raise Conflict("该执行已经结束，不能继续。")
    if execution.schema_version != SCHEMA_VERSION:
        raise Conflict("执行检查点版本不兼容。")
    if execution.expires_at <= (at or now()):
        raise Conflict("执行检查点已过期，请重新发起请求。")
    if execution.recovery_attempts >= max(0, max_attempts):
        raise Conflict("已达到恢复次数上限，请重新发起请求。")
    validate_inputs(recovery_input_fingerprint(db, execution), current_inputs)
    _payload(execution.checkpoint)
    uncertain = db.scalar(select(AgentToolReceipt.id).where(
        AgentToolReceipt.execution_id == execution_id,
        AgentToolReceipt.side_effect.is_(True), AgentToolReceipt.status == "started",
    ).limit(1))
    if uncertain is not None:
        raise Conflict("有工具的副作用尚未确认，不能自动重放执行。")
    return _cas(db, execution, expected_version, {
        "status": "running", "recovery_attempts": execution.recovery_attempts + 1,
    })


@dataclass(frozen=True)
class ToolStart:
    receipt: AgentToolReceipt
    should_execute: bool


def start_tool(
    db: Session,
    execution_id: str,
    *,
    user_id: int,
    call_id: str,
    tool_name: str,
    arguments: dict[str, Any],
    input_fingerprint: dict[str, Any] | None = None,
    side_effect: bool = True,
) -> ToolStart:
    """Commit a new start before the tool; reuse completed outcomes verbatim.

    A prior unacknowledged side effect is deliberately not retried. Pure reads
    may be replayed under the caller's normal conversation execution lock.
    """
    execution = get_execution(db, execution_id, user_id=user_id)
    if execution.status not in RECOVERABLE_STATUSES:
        raise Conflict("该执行已经结束，不能启动工具。")
    if not call_id or len(call_id) > 200 or not tool_name or len(tool_name) > 128:
        raise ServiceError("工具调用身份不正确。")
    args_digest = fingerprint(arguments)
    inputs_digest = fingerprint(execution.input_fingerprint if input_fingerprint is None else input_fingerprint)

    def existing_call() -> ToolStart | None:
        receipt = db.scalar(select(AgentToolReceipt).where(
            AgentToolReceipt.execution_id == execution_id, AgentToolReceipt.call_id == call_id,
        ))
        if receipt is None:
            return None
        if (receipt.tool_name != tool_name or receipt.arguments_digest != args_digest
                or receipt.input_digest != inputs_digest or receipt.side_effect != side_effect):
            raise Conflict("工具调用身份已被其他参数或输入使用。")
        if receipt.status == "started" and receipt.side_effect:
            raise Conflict("该工具的副作用尚未确认，不能重复执行。")
        return ToolStart(receipt, receipt.status == "started")

    existing = existing_call()
    if existing is not None:
        return existing
    values = dict(
        execution_id=execution_id, call_id=call_id, tool_name=tool_name,
        arguments_digest=args_digest, input_digest=inputs_digest, side_effect=side_effect,
    )
    inserted = db.execute(insert(AgentToolReceipt).values(**values).on_conflict_do_nothing(
        index_elements=["execution_id", "call_id"],
    ))
    # A newly inserted start is owned by this caller. A conflicting prior start
    # must pass the uncertainty gate instead of executing a second side effect.
    if inserted.rowcount == 1:
        receipt = db.scalar(select(AgentToolReceipt).where(
            AgentToolReceipt.execution_id == execution_id, AgentToolReceipt.call_id == call_id,
        ))
        assert receipt is not None
        return ToolStart(receipt, True)
    existing = existing_call()
    assert existing is not None
    return existing


def finish_tool(
    db: Session,
    execution_id: str,
    *,
    user_id: int,
    call_id: str,
    result: dict[str, Any],
    succeeded: bool = True,
) -> AgentToolReceipt:
    """Store the observed outcome once, even if cancellation raced its return."""
    get_execution(db, execution_id, user_id=user_id)
    receipt = db.scalar(select(AgentToolReceipt).where(
        AgentToolReceipt.execution_id == execution_id, AgentToolReceipt.call_id == call_id,
    ))
    if receipt is None:
        raise Conflict("没有找到工具开始记录，不能凭空登记执行成功。")
    safe_result = _payload(result)
    status = "completed" if succeeded else "failed"
    if receipt.status != "started":
        if receipt.status != status or fingerprint(receipt.result) != fingerprint(safe_result):
            raise Conflict("工具执行回执已存在，不能改写真实结果。")
        return receipt
    updated = db.execute(update(AgentToolReceipt).where(
        AgentToolReceipt.id == receipt.id, AgentToolReceipt.status == "started",
    ).values(status=status, result=safe_result, finished_at=now()).execution_options(synchronize_session=False))
    if updated.rowcount != 1:
        raise Conflict("工具回执已被其他执行更新。")
    db.refresh(receipt)
    return receipt
