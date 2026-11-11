"""Conversation ownership, durable loop checkpoints and user steering."""
from __future__ import annotations

from contextlib import ExitStack, asynccontextmanager
from copy import deepcopy
from datetime import datetime
import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy import select

from sheetmate.core.config.settings import settings
from sheetmate.db.session import session_scope
from sheetmate.db.models import AgentExecution, AgentToolReceipt, Conversation, Message, User
from sheetmate.services.conversations import service as conversations
from sheetmate.services.execution import state as state_store
from sheetmate.services.files import service as files
from sheetmate.core.errors import Conflict, ServiceError
from sheetmate.services.execution.jobs import Job, jobs


def execution_root(execution_id: str) -> Path:
    return settings.workspaces_dir / "executions" / execution_id


def input_snapshot(db: Any, conv: Conversation, baseline: dict[str, Any] | None = None) -> dict[str, Any]:
    """Content identities, scope and source revisions; output creation is allowed."""
    records = files.list_files(db, customer_id=conv.customer_id, conversation_id=conv.id)
    previous = set((baseline or {}).get("files", {}))
    identities = {}
    for record in records:
        if baseline is not None and record.kind == "output" and record.id not in previous:
            continue
        path = Path(record.stored_path)
        digest = hashlib.sha256()
        if path.is_file():
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            identities[record.id] = digest.hexdigest()
        else:
            identities[record.id] = "missing"
    tables = {table.name: table.revision for table in conversations.workspace(conv.id).list_tables()
              if table.source == "uploaded" and (not table.file_id or table.file_id in identities)}
    return {"customer_id": conv.customer_id, "recipe_id": conv.recipe_id, "files": identities, "tables": tables}


def latest_execution(db: Any, conversation_id: str) -> AgentExecution | None:
    # Timestamps have second precision and execution IDs are random. The
    # unique originating user-message ID preserves a conversation's request order.
    return db.scalar(select(AgentExecution).where(AgentExecution.conversation_id == conversation_id)
                     .order_by(AgentExecution.request_message_id.desc()).limit(1))


def pending_steers(db: Any, conversation_id: str, execution_id: str | None = None) -> list[dict[str, Any]]:
    result = []
    for message in db.scalars(select(Message).where(Message.conversation_id == conversation_id,
                                                   Message.role == "user").order_by(Message.id)):
        steering = (message.meta or {}).get("steering") or {}
        if steering.get("status") == "queued" and (execution_id is None or steering.get("execution_id") == execution_id):
            result.append({"message_id": message.id, "content": message.content, "status": "queued",
                           "job_id": steering.get("job_id"), "execution_id": steering.get("execution_id")})
    return result


def execution_assistant_id(db: Any, execution: AgentExecution) -> int | None:
    """Recover the reply identity across the message/checkpoint commit gap."""
    if execution.assistant_message_id is not None:
        return execution.assistant_message_id
    return db.scalar(select(Message.id).where(
        Message.conversation_id == execution.conversation_id, Message.role == "assistant",
        Message.meta["execution_id"].as_string() == execution.id,
    ).order_by(Message.id).limit(1))


def execution_view(db: Any, conv: Conversation) -> dict[str, Any] | None:
    execution = latest_execution(db, conv.id)
    if execution is None:
        return None
    result: dict[str, Any] = {"execution_id": execution.id, "assistant_message_id": execution_assistant_id(db, execution),
                             "status": execution.status, "can_resume": False}
    job = jobs.get(conv.id)
    if job is not None:
        if job.cleanup_pending:
            result.update(status="cleaning", resume_reason="后台操作正在清理，请稍候。")
        return result
    if execution.status not in state_store.RECOVERABLE_STATUSES:
        return result
    reason = None
    if execution.expires_at <= datetime.now():
        reason = "上次任务的恢复窗口已过期，请重新发送需求。"
    elif execution.schema_version != state_store.SCHEMA_VERSION:
        reason = "上次任务的执行状态版本不兼容，请重新发送需求。"
    elif execution.recovery_attempts >= 3:
        reason = "已达到恢复次数上限，请重新发送需求。"
    elif db.scalar(select(AgentToolReceipt.id).where(AgentToolReceipt.execution_id == execution.id,
                    AgentToolReceipt.status == "started", AgentToolReceipt.side_effect.is_(True)).limit(1)):
        reason = "上次操作的结果尚未确认，为避免重复写入，请检查结果后重新发送需求。"
    elif execution.checkpoint.get("analysis_ready") and not (execution_root(execution.id) / "working.duckdb").exists():
        reason = "上次任务的工作区已丢失，请重新发送需求。"
    if reason is None:
        try:
            inputs = state_store.recovery_input_fingerprint(db, execution)
            if inputs.get("customer_id") != conv.customer_id or inputs.get("recipe_id") != conv.recipe_id:
                reason = "客户或流程关联已变化，无法从旧进度继续。"
        except ServiceError:
            reason = "上次任务的流程关联缺少有效执行回执，请重新发送需求。"
    if reason:
        result["resume_reason"] = reason
    else:
        result["can_resume"] = True
    return result


async def queue_steer(user: User, conversation_id: str, content: str) -> dict[str, Any]:
    content = (content or "").strip()
    if not content:
        raise ServiceError("追加要求不能为空。")
    with session_scope() as db:
        conversations.get_conversation(db, user, conversation_id)
    job = jobs.get(conversation_id)
    if job is None or not job.execution_id:
        raise Conflict("任务已结束，请直接发送新消息。")
    async with job.control_lock:
        if jobs.get(conversation_id) is not job or not job.accepting or job.cancel_source:
            raise Conflict("任务正在结束或清理，请稍后发送新消息。")
        with session_scope() as db:
            conv = conversations.get_conversation(db, user, conversation_id)
            execution = state_store.get_execution(db, job.execution_id, user_id=user.id, conversation_id=conv.id)
            if execution.status != "running":
                raise Conflict("任务已结束，请直接发送新消息。")
            message = conversations.add_message(db, conv, "user", content, meta={"steering": {
                "execution_id": execution.id, "job_id": job.id, "status": "queued",
            }})
            event = {"type": "steer_queued", "message_id": message.id, "content": content, "status": "queued",
                     "execution_id": execution.id,
                     "job_id": job.id, "request_message_id": job.message_id,
                     "assistant_message_id": execution.assistant_message_id}
        await job.emit(event)
        return {key: event[key] for key in ("message_id", "status", "job_id", "execution_id")}


async def withdraw_steer(user: User, conversation_id: str, message_id: int) -> dict[str, Any]:
    with session_scope() as db:
        conversations.get_conversation(db, user, conversation_id)
    job = jobs.get(conversation_id)
    if job is None:
        raise Conflict("任务状态已变化，请刷新对话。")
    async with job.control_lock:
        with session_scope() as db:
            message = db.get(Message, message_id)
            steering = dict((message.meta or {}).get("steering") or {}) if message else {}
            if (message is None or message.conversation_id != conversation_id or message.role != "user"
                    or steering.get("execution_id") != job.execution_id or steering.get("status") != "queued"):
                raise Conflict("这条要求已被采纳或撤回，无法再撤回。")
            steering["status"] = "withdrawn"
            message.meta = {**(message.meta or {}), "steering": steering}
            event = {"type": "steer_withdrawn", "message_id": message.id, "content": message.content,
                     "status": "withdrawn", "job_id": job.id, "request_message_id": job.message_id,
                     "execution_id": job.execution_id}
        await job.emit(event)
        return {"message_id": message_id, "status": "withdrawn", "job_id": job.id, "execution_id": job.execution_id}


@asynccontextmanager
async def execution_resources(job: Job):
    resources = ExitStack()
    try:
        yield resources
    finally:
        # Resource lifetimes include the real synchronous work, even after the
        # async tool waiter has timed out or been cancelled.
        try:
            await job.wait_workers()
        finally:
            resources.close()


class ExecutionControl:
    def __init__(self, job: Job, user_id: int):
        self.job, self.user_id = job, user_id
        with session_scope() as db:
            execution = state_store.get_execution(db, job.execution_id, user_id=user_id)
            self.version = execution.version
            self.checkpoint = deepcopy(execution.checkpoint)
            self.request = deepcopy(execution.request)
            self.assistant_message_id = execution_assistant_id(db, execution)
            if self.assistant_message_id is not None:
                self.checkpoint["ui"] = {**(self.checkpoint.get("ui") or {}),
                                         "assistant_message_id": self.assistant_message_id}
        self.ctx: Any = None
        self.llm: Any = None
        self.ui_snapshot: Any = None
        self.runtime: Any = None
        self._recipe_binding_starts: dict[str, dict[str, str | None]] = {}

    def snapshot(self, runtime_state: dict[str, Any] | None = None) -> dict[str, Any]:
        from sheetmate.agent.memory.short_term.runtime import durable_value
        checkpoint = deepcopy(self.checkpoint)
        if runtime_state is not None:
            checkpoint["runtime"] = deepcopy(runtime_state)
        if callable(self.ui_snapshot):
            checkpoint["ui"] = self.ui_snapshot()
        if self.ctx is not None:
            checkpoint["context"] = {
                "mode": self.ctx.mode, "tool_group": self.ctx.tool_group,
                "enabled_tool_groups": sorted(self.ctx.enabled_tool_groups),
                "requested_tool_groups": list(self.ctx.requested_tool_groups),
                "save_recipe_requested": self.ctx.save_recipe_requested,
                "recipe_edit": self.ctx.recipe_edit,
                "replace_file_ids": list(self.ctx.replace_file_ids),
                "evidence_store": self.ctx.evidence_store,
                "saved_recipes": self.ctx.host.saved_recipes, "deleted_recipes": self.ctx.host.deleted_recipes,
                "last_recipe": self.ctx.host._last_recipe.model_dump(mode="json") if self.ctx.host._last_recipe else None,
            }
        budget = getattr(self.llm, "budget", None)
        if budget is not None and callable(getattr(budget, "export_state", None)):
            checkpoint["request_budget"] = budget.export_state()
            checkpoint["llm_usage"] = {"usage": self.llm.usage, "stages": self.llm.usage_by_stage,
                                       "calls": self.llm.usage_calls}
        return durable_value(checkpoint)

    def persist(self, runtime_state: dict[str, Any] | None = None, *, status: str = "running",
                receipt: tuple[str, dict[str, Any]] | None = None) -> None:
        checkpoint = self.snapshot(runtime_state)
        with session_scope() as db:
            if receipt is not None:
                call_id, result = receipt
                completed = state_store.finish_tool(db, self.job.execution_id, user_id=self.user_id, call_id=call_id,
                                                   result=result, succeeded=bool(result.get("ok", True)))
                before = self._recipe_binding_starts.get(call_id)
                host = getattr(self.ctx, "host", None)
                if before is not None and host is not None and completed.status == "completed":
                    conv = db.get(Conversation, self.job.key)
                    current_recipe = getattr(host, "recipe_id", None)
                    if (conv is not None and current_recipe != before["recipe_id"]
                            and conv.customer_id == before["customer_id"]
                            and getattr(host, "customer_id", None) == before["customer_id"]
                            and conv.recipe_id == current_recipe):
                        checkpoint = state_store.acknowledge_recipe_binding(db, self.job.execution_id, checkpoint,
                            user_id=self.user_id, call_id=call_id, previous_recipe_id=before["recipe_id"],
                            recipe_id=current_recipe)
            execution = state_store.save_checkpoint(db, self.job.execution_id, checkpoint,
                user_id=self.user_id, expected_version=self.version, status=status,
                assistant_message_id=self.assistant_message_id)
            version = execution.version
        self.checkpoint, self.version = checkpoint, version
        if receipt is not None:
            self._recipe_binding_starts.pop(receipt[0], None)

    async def save(self, state: dict[str, Any]) -> None:
        self.persist(state)

    async def start_tool(self, call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
        readonly = name in {"table_info", "preview_table", "value_counts", "column_stats", "list_files",
                            "show_files", "list_recipes", "get_recipe", "inspect_python_script",
                            "inspect_task", "inspect_history", "inspect_memory", "inspect_evidence", "read_tool_result"}
        with session_scope() as db:
            start = state_store.start_tool(db, self.job.execution_id, user_id=self.user_id,
                call_id=call_id, tool_name=name, arguments=arguments, side_effect=not readonly)
            if start.should_execute and not readonly and self.ctx is not None:
                host = getattr(self.ctx, "host", None)
                conv = db.get(Conversation, self.job.key)
                execution = state_store.get_execution(db, self.job.execution_id, user_id=self.user_id)
                expected = state_store.recovery_input_fingerprint(db, execution)
                if (conv is not None and host is not None
                        and conv.customer_id == getattr(host, "customer_id", None) == expected.get("customer_id")
                        and conv.recipe_id == getattr(host, "recipe_id", None) == expected.get("recipe_id")):
                    self._recipe_binding_starts[call_id] = {
                        "customer_id": conv.customer_id, "recipe_id": conv.recipe_id,
                    }
            return None if start.should_execute else deepcopy(start.receipt.result)

    async def finish_tool(self, call_id: str, result: dict[str, Any], state: dict[str, Any]) -> None:
        self.persist(state, receipt=(call_id, result))

    async def poll(self) -> list[dict[str, Any]]:
        from sheetmate.agent.memory.short_term.task_state import advance_task
        from sheetmate.services.conversations.chat import _hydrate_task
        events, messages = [], []
        async with self.job.control_lock:
            if self.job.cancel_source:
                return []
            with session_scope() as db:
                conv = db.get(Conversation, self.job.key)
                if conv is None:
                    raise Conflict("会话已被删除。")
                for message in db.scalars(select(Message).where(Message.conversation_id == conv.id,
                                                               Message.role == "user").order_by(Message.id)):
                    steering = dict((message.meta or {}).get("steering") or {})
                    if steering.get("execution_id") != self.job.execution_id or steering.get("status") == "withdrawn":
                        continue
                    if steering.get("status") == "queued":
                        steering["status"] = "committed"
                        message.meta = {**(message.meta or {}), "steering": steering}
                        conv.task_state = advance_task(conv.task_state, message.id, relation="continue", file_ids=[])
                        events.append({"type": "steer_committed", "message_id": message.id, "content": message.content,
                            "execution_id": self.job.execution_id,
                            "status": "committed", "job_id": self.job.id, "request_message_id": self.job.message_id,
                            "assistant_message_id": self.assistant_message_id})
                    if steering.get("status") == "committed":
                        messages.append({"message_id": message.id, "content": message.content})
                if self.ctx is not None:
                    self.ctx.task_state = _hydrate_task(db, conv.id, conv.task_state)
            for event in events:
                await self.job.emit(event)
        if events and self.ctx is not None and getattr(self.ctx, "execution_action", None) == "process":
            from sheetmate.agent.framework.shared.persistence import infer_recipe_persistence
            original = self.request.get("message", "")
            requirements = original + "\n\n用户执行中更正（后面的要求优先）：\n" + "\n".join(
                item["content"] for item in messages)
            allowed = await infer_recipe_persistence(self.llm, requirements, [], recipe_edit=self.ctx.recipe_edit)
            self.ctx.save_recipe_requested = self.ctx.host.save_recipe_requested = allowed
        return messages

    async def seal_if_idle(self) -> bool:
        """A committed mailbox item may outlive its not-yet-written loop state."""
        known = {item["message_id"] for item in (self.runtime.state if self.runtime else {}).get("steers", [])}
        async with self.job.control_lock:
            with session_scope() as db:
                for message in db.scalars(select(Message).where(Message.conversation_id == self.job.key,
                                                               Message.role == "user")):
                    steering = (message.meta or {}).get("steering") or {}
                    if (steering.get("execution_id") == self.job.execution_id
                            and steering.get("status") in {"queued", "committed"} and message.id not in known):
                        return False
            self.job.accepting = False
            return True

    def restore_context(self, ctx: Any, llm: Any) -> None:
        from sheetmate.recipe.definition.schema import Recipe
        self.ctx, self.llm = ctx, llm
        data = self.checkpoint.get("context") or {}
        for name in ("mode", "tool_group", "save_recipe_requested"):
            if name in data:
                setattr(ctx, name, data[name])
        ctx.enabled_tool_groups = set(data.get("enabled_tool_groups", []))
        ctx.requested_tool_groups = tuple(data.get("requested_tool_groups", []))
        ctx.replace_file_ids = tuple(data.get("replace_file_ids", []))
        ctx.recipe_edit = dict(data.get("recipe_edit", {}))
        ctx.host.recipe_edit = dict(ctx.recipe_edit)
        ctx.evidence_store.update(data.get("evidence_store", {}))
        ctx.host.save_recipe_requested = ctx.save_recipe_requested
        ctx.host.saved_recipes = list(data.get("saved_recipes", []))
        ctx.host.deleted_recipes = list(data.get("deleted_recipes", []))
        if data.get("last_recipe"):
            ctx.host._last_recipe = Recipe.model_validate(data["last_recipe"])
        budget = getattr(llm, "budget", None)
        if budget is not None and self.checkpoint.get("request_budget"):
            budget.restore_state(self.checkpoint["request_budget"])
            inflight = (self.checkpoint.get("runtime") or {}).get("inflight_model") or {}
            before = inflight.get("budget_calls_before")
            if isinstance(before, int) and budget.calls <= before:
                # The process can disappear inside a provider call, after its
                # last checkpoint and before its internal reservation flushes.
                from sheetmate.agent.llm.budget import request_size
                messages = ((self.checkpoint.get("runtime") or {}).get("loop") or {}).get("messages", [])
                _, prompt = request_size({"messages": messages})
                budget.calls = before + 1
                budget.prompt_tokens += prompt
                budget.total_tokens += prompt + int(getattr(getattr(llm, "cfg", None), "max_tokens", 8192))
                budget.estimated_calls += 1
            usage = self.checkpoint.get("llm_usage") or {}
            llm.usage = deepcopy(usage.get("usage", llm.usage))
            llm.usage_by_stage = deepcopy(usage.get("stages", {}))
            llm.usage_calls = deepcopy(usage.get("calls", []))
