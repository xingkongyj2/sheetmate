"""对话编排：一条用户消息从进来到回答完成的全过程（对应 TabClaw app.py 里 /api/chat 的逻辑）。

1. 保存用户消息；历史太长先压缩（auto-compact）
2. 组装上下文：模式（分析/修改）、客户（记忆、流程、整体处理逻辑）、工作区里的表、公式库
3. 选择执行方式：思考模式（计划 → 逐项 ReAct + 核对）/ 按给定计划逐步执行 / 多表并行 / 直接 ReAct
4. 事件实时推给前端（文本、工具调用、结果表、Recipe 步骤、文件…），同时精简后存进助手消息，刷新页面能还原执行明细
5. 结束后：分析模式记一条运行记录（工作流）、首轮生成标题、后台抽取记忆
"""

from __future__ import annotations

import asyncio
import time
from contextlib import aclosing
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from sheetmate.agent.framework.react.executor import AgentExecutor
from sheetmate.agent.framework.shared.intent import RequestIntent, infer_request
from sheetmate.agent.llm.client import (
    LLMError,
    LLMNotConfigured,
    contains_llm_not_configured_text,
    get_llm,
    is_llm_not_configured_message,
)
from sheetmate.agent.memory.long_term.extraction import extract_memories
from sheetmate.agent.framework.react.multi_agent import execute_multi, should_activate
from sheetmate.agent.framework.planning.planner import check_clarification
from sheetmate.agent.framework.shared.persistence import infer_recipe_persistence
from sheetmate.agent.framework.shared.recorder import WorkflowRecorder
from sheetmate.agent.memory.short_term.summarization import (
    HISTORY_TOKEN_BUDGET,
    RECENT_HISTORY_TOKENS,
    RECENT_USER_ANCHORS,
    bounded_history,
    compact_history,
    compaction_batch,
    fallback_title,
    make_title,
    primer_block,
    summary_without_primer,
)
from sheetmate.agent.memory.short_term.tokens import history_tokens
from sheetmate.agent.framework.planning.executor import THINKING_TOOL_BUDGET, execute_thinking
from sheetmate.agent.tools.contracts import AgentContext
from sheetmate.agent.tools.registry import ToolRegistry
from sheetmate.agent.memory.short_term.task_state import advance_task
from sheetmate.core.config.settings import settings
from sheetmate.core.observability.conversation_log import (
    bind_trace,
    current_trace,
    error_data,
    new_turn,
    record_event,
    trace_phase,
)
from sheetmate.db.session import session_scope
from sheetmate.db.models import (
    AgentExecution,
    Conversation,
    Customer,
    FileRecord,
    Message,
    RecipeProfile,
    RecipeRecord,
    User,
)
from sheetmate.engine.workbooks.images import model_image_data_url
from sheetmate.engine.workbooks.reader import ReadError
from sheetmate.engine.scripts.references import bind_script_references, externalize_script_references
from sheetmate.services.conversations import service as conv_service
from sheetmate.services.customers import service as customer_service
from sheetmate.services.files import service as file_service
from sheetmate.services.memory import long_term as memory_service
from sheetmate.services.workflows import service as recipe_service
from sheetmate.services.workflows import deletion as recipe_deletion
from sheetmate.services.memory.repair_experiences import learn_verified_repairs
from sheetmate.services.workflows import runs as run_service
from sheetmate.services.conversations.delivery import select_delivery
from sheetmate.services.conversations.file_view import file_references, file_view_answer, select_files
from sheetmate.services.execution.workspace import AnalysisWorkspace, analysis_workspace
from sheetmate.core.errors import Conflict, ServiceError
from sheetmate.services.conversations.host import ConversationHost
from sheetmate.services.memory.table_profiles import file_memories_text, restore_workspace_memories
from sheetmate.services.execution.jobs import Job, jobs
from sheetmate.services.execution import state as execution_state
from sheetmate.services.execution.control import (
    ExecutionControl,
    execution_resources,
    execution_root,
    input_snapshot,
    latest_execution,
)

_registry = ToolRegistry()
_background: set[asyncio.Task] = set()  # 持有后台任务的引用，防止被垃圾回收
_titles_inflight: set[str] = set()
_MAX_MESSAGE_IMAGES = 10
_MAX_HISTORY_IMAGES = 8
_MAX_IMAGE_REQUEST_BYTES = 20 * 1024 * 1024
_KEEP_EVENTS = {
    "model_call_start", "model_call_done", "completion_check_start", "completion_check", "execution_stop", "execution_status",
    "note", "reply", "tool_call", "tool_result", "table", "file", "delivery_manifest", "script", "clarify", "memory_saved", "field_mapping_saved", "plan_start", "step_start", "step_done",
    "reflect_start", "reflect_done", "think_start", "plan_review", "plan_repair", "step_check", "audit_start", "audit_done", "agent_pool_start", "agent_start", "agent_done", "aggregate_start", "error", "compacted",
    "recipe_learn_start", "recipe_evidence", "recipe_generated", "recipe_run_start", "recipe_input", "recipe_step_start",
    "recipe_step_done", "recipe_step_error", "recipe_repair", "recipe_output", "recipe_validation", "recipe_run_done",
    "recipe_verify", "recipe_refine", "recipe_diff", "recipe_completed", "recipe_saved", "recipe_deleted", "recipe_failed", "recipe_view",
}


def _attachment_records(db: Session, conv: Conversation, attachments: list[str]) -> list[FileRecord]:
    records = []
    for file_id in dict.fromkeys(attachments):
        record = file_service.get_file(db, file_id)
        if not file_service.can_access_in_conversation(db, record, conversation_id=conv.id, customer_id=conv.customer_id):
            raise ServiceError("只能发送本会话或当前客户的附件")
        records.append(record)
    if sum(((record.profile or {}).get("media_type") or "").startswith("image/") for record in records) > _MAX_MESSAGE_IMAGES:
        raise ServiceError(f"每条消息最多发送 {_MAX_MESSAGE_IMAGES} 张图片")
    return records


def _image_parts(records: list[FileRecord]) -> list[dict[str, Any]]:
    parts = []
    encoded_size = 0
    for record in records:
        if not ((record.profile or {}).get("media_type") or "").startswith("image/"):
            continue
        url = model_image_data_url(Path(record.stored_path))
        encoded_size += len(url)
        if encoded_size > _MAX_IMAGE_REQUEST_BYTES:
            raise ServiceError("本条消息的图片内容过大，请减少图片数量或压缩后重试")
        parts.extend([
            {"type": "text", "text": f"附件图片：{record.filename}（file_id={record.id}）"},
            {"type": "image_url", "image_url": {"url": url}},
        ])
    return parts


def _hydrate_task(db: Session, conversation_id: str, state: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve source text within this conversation; never trust arbitrary message IDs."""
    task = dict(state or {})
    ids = [value for value in task.get("source_message_ids", []) if isinstance(value, int)]
    if ids:
        sources = db.scalars(select(Message).where(
            Message.conversation_id == conversation_id, Message.role == "user", Message.id.in_(ids),
        ).order_by(Message.id)).all()
        task["requirements"] = [{"message_id": item.id, "content": item.content} for item in sources]
    else:
        task["requirements"] = []
    return task


def _advance_conversation_task(conversation_id: str, message_id: int, *, relation: str,
                               save_recipe: bool) -> dict[str, Any]:
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        request = db.get(Message, message_id)
        if conv is None or request is None or request.conversation_id != conversation_id or request.role != "user":
            raise ServiceError("当前任务的来源消息不可用")
        state = advance_task(conv.task_state, message_id, relation=relation,
                             file_ids=(request.meta or {}).get("attachments", []))
        state["save_recipe_requested"] = save_recipe
        conv.task_state = state
        return _hydrate_task(db, conversation_id, state)


def build_context(user_id: int, conversation_id: str, llm: Any) -> tuple[AgentContext, ConversationHost]:
    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            raise ServiceError("当前用户不存在，无法读取对话上下文")
        conv = conv_service.get_conversation(db, user, conversation_id)
        customer = db.get(Customer, conv.customer_id) if conv.customer_id else None
        library = recipe_service.library_for(db, conv.customer_id)
        mode = conv.mode
        customer_info = {"id": customer.id, "name": customer.name, "code": customer.code,
                         "description": customer.description, "summary": customer.summary} if customer else None
        if customer is not None and not conv.recipe_id:
            primary = recipe_service.resolve_customer_recipe(db, customer.id)
            conv.recipe_id = primary.recipe_id if primary else None
        recipe_id = conv.recipe_id
        if customer_info is not None:
            customer_info["recipe_id"] = recipe_id
            selected_record = db.scalars(select(RecipeRecord).where(RecipeRecord.recipe_id == recipe_id,
                                                                      RecipeRecord.customer_id == customer.id)
                                         .order_by(RecipeRecord.version.desc()).limit(1)).first() if recipe_id else None
            if selected_record is not None:
                recipe_service.resolve_customer_recipe(db, customer.id, recipe_id)
                recipe_service.ensure_profile(db, selected_record)
            profile = db.get(RecipeProfile, recipe_id) if recipe_id else None
            if recipe_id:
                # 旧客户总结属于历史流程口径；选定流程后以其独立说明为准，避免混入旧规则。
                customer_info["summary"] = ""
                customer_info["recipe_name"] = selected_record.name if selected_record else ""
                customer_info["recipe_summary"] = profile.summary if profile and profile.customer_id == customer.id else ""
        available_records = file_service.list_files(
            db, customer_id=conv.customer_id, conversation_id=conv.id,
        )
        available = {record.id for record in available_records}
        ws = conv_service.workspace(conversation_id)
        # 旧版本删除了原件后可能留下输入表；不能再把它们当作可用原件交给流程。
        for table in ws.list_tables():
            if table.source == "uploaded" and table.file_id and table.file_id not in available:
                ws.drop(table.name)
        restore_workspace_memories(ws, available_records)
        file_memory = file_memories_text(available_records, {t.file_id for t in ws.list_tables() if t.file_id})
        task_state = _hydrate_task(db, conversation_id, conv.task_state)
        evidence_store = {}
        for previous in db.scalars(select(Message).where(
            Message.conversation_id == conversation_id, Message.role == "assistant",
        ).order_by(Message.id.desc()).limit(20)):
            for event in previous.events or []:
                evidence = event.get("evidence") or {}
                evidence_id = evidence.get("id")
                record = event.get("evidence_record")
                if event.get("type") == "tool_result" and evidence_id and isinstance(record, dict):
                    evidence_store.setdefault(evidence_id, record)
    host = ConversationHost(user_id=user_id, conversation_id=conversation_id,
                            customer_id=customer_info["id"] if customer_info else None, ws=ws, recipe_id=recipe_id)
    ctx = AgentContext(mode=mode, ws=ws, library=library, host=host, llm=llm, conversation_id=conversation_id,
                       customer=customer_info, max_repair=settings.repair_max_attempts,
                       verify_rounds=settings.verify_max_rounds, file_memory=file_memory)
    ctx.task_state = task_state
    ctx.evidence_store = evidence_store
    return ctx, host


def start_chat(user: User, conversation_id: str, message: str, *,
               plan_steps: list[dict[str, Any]] | None = None, attachments: list[str] | None = None,
               thinking: bool = False, recipe_edit: dict[str, Any] | None = None,
               http_request: dict[str, Any] | None = None) -> Job:
    # Authorise before creating a log; untrusted ids must not create arbitrary files.
    with session_scope() as db:
        conv_service.get_conversation(db, user, conversation_id)
    trace = new_turn(conversation_id)
    with bind_trace(trace):
        record_event("turn.start", "sheetmate.services.conversations.chat.start_chat", {"user_id": user.id, "source": "chat"})
        record_event("chat.input", "sheetmate.services.conversations.chat.start_chat",
                     http_request if http_request is not None else {
                         "body": {"message": message, "plan_steps": plan_steps, "attachments": attachments,
                                  "thinking": thinking, "recipe_edit": recipe_edit}})
        try:
            return _start_chat(user, conversation_id, message, plan_steps=plan_steps,
                               attachments=attachments, thinking=thinking, recipe_edit=recipe_edit)
        except BaseException as exc:
            record_event("turn.error", "sheetmate.services.conversations.chat.start_chat", error_data(exc))
            record_event("turn.end", "sheetmate.services.conversations.chat.start_chat", {"status": "rejected"})
            raise


def _start_chat(user: User, conversation_id: str, message: str, *,
                plan_steps: list[dict[str, Any]] | None = None, attachments: list[str] | None = None,
                thinking: bool = False, recipe_edit: dict[str, Any] | None = None) -> Job:
    text = (message or "").strip()
    if not text:
        raise ServiceError("消息不能为空")
    with session_scope() as db:
        conv = conv_service.get_conversation(db, user, conversation_id)
        if jobs.running(conversation_id):
            raise Conflict("上一个任务还在处理中，请稍等它完成")
        from sheetmate.services.workflows.editing import validate_recipe_edit
        edit = validate_recipe_edit(db, user, conv, recipe_edit)
        submitted = list(dict.fromkeys(attachments or []))
        for record in _attachment_records(db, conv, submitted):
            file_service.retain_for_conversation(db, record, conv.id)
        meta: dict[str, Any] = {"attachments": submitted}
        trace = current_trace()
        if trace is not None:
            meta["log_turn_id"] = trace.turn_id
        if plan_steps:
            meta["plan"] = plan_steps
        if thinking:
            meta["thinking"] = True
        if edit:
            meta["recipe_edit"] = {key: edit[key] for key in ("recipe_id", "version", "target", "operation")}
        message_id = conv_service.add_message(db, conv, "user", text, meta=meta).id
        execution = execution_state.create_execution(db, conversation_id, user.id, message_id,
            request={"message": text, "plan_steps": plan_steps, "use_planning": thinking, "recipe_edit": edit},
            input_fingerprint=input_snapshot(db, conv))
        execution_id = execution.id
    user_id = user.id

    async def work(job: Job) -> None:
        try:
            await _run_chat(job, user_id, conversation_id, text, plan_steps, message_id, thinking)
        finally:
            _cleanup_execution_workspace(execution_id)

    return jobs.start(conversation_id, work, message_id=message_id, execution_id=execution_id)


def _cleanup_execution_workspace(execution_id: str) -> None:
    import shutil
    with session_scope() as db:
        execution = db.get(AgentExecution, execution_id)
        terminal = execution is None or execution.status in execution_state.TERMINAL_STATUSES
    if terminal:
        shutil.rmtree(execution_root(execution_id), ignore_errors=True)


def resume_chat(user: User, conversation_id: str) -> Job:
    """Claim once before starting work; a click can never create two writers."""
    if jobs.running(conversation_id):
        raise Conflict("当前任务仍在执行或清理，请稍候。")
    with session_scope() as db:
        conv = conv_service.get_conversation(db, user, conversation_id)
        execution = latest_execution(db, conv.id)
        if execution is None:
            raise Conflict("没有可以继续的任务。")
        execution = execution_state.claim_recovery(db, execution.id, user_id=user.id,
            expected_version=execution.version, current_inputs=input_snapshot(db, conv, execution.input_fingerprint))
        request = db.get(Message, execution.request_message_id)
        assert request is not None
        text, message_id = request.content, request.id
        options = deepcopy(execution.request)
        execution_id = execution.id
    async def work(job: Job) -> None:
        try:
            await _run_chat(job, user.id, conversation_id, text, options.get("plan_steps"), message_id,
                            bool(options.get("use_planning")), resumed=True)
        finally:
            _cleanup_execution_workspace(execution_id)
    with bind_trace(new_turn(conversation_id)):
        return jobs.start(conversation_id, work, message_id=message_id, execution_id=execution_id)


async def _run_chat(job: Job, user_id: int, conversation_id: str, message: str, plan_steps: list[dict[str, Any]] | None,
                    message_id: int, thinking: bool = False, *, resumed: bool = False) -> None:
    started_at = time.monotonic()
    first_reply_at: float | None = None
    control = ExecutionControl(job, user_id) if job.execution_id else None
    saved = (control.checkpoint.get("ui") or {}) if control else {}
    recorder = WorkflowRecorder(message)
    recorder.request_message_id = message_id
    recorder.job_id = job.id
    stored: list[dict[str, Any]] = _restore_ui_events(saved.get("stored", []), user_id, conversation_id)
    committed_delivery = control.checkpoint.get("delivery_manifest") if control else None
    if control and resumed:
        pending = (control.checkpoint.get("runtime") or {}).get("last_tool_events", [])
        for pending_event in _restore_ui_events(pending, user_id, conversation_id):
            if pending_event not in stored and pending_event.get("type") in _KEEP_EVENTS:
                stored.append(_slim(pending_event))
        if committed_delivery and committed_delivery not in stored:
            stored.append(_slim(committed_delivery))
    delivery_candidates = _restore_delivery_candidates(stored, saved.get("delivery_candidates", {}))
    # Interruption notices do not become the continued answer or its verdict.
    if resumed:
        stored = [event for event in stored if event.get("type") not in {"execution_status", "execution_stop", "error"}]
        for event in stored:
            recorder.observe(event)
        recorder.result, recorder.error = {}, ""
        recorder._active_calls.clear()
        recorder.active_call = None
    recorder.message_id = saved.get("assistant_message_id")
    recorder.execution_id = job.execution_id
    text_parts: list[str] = list(saved.get("text_parts", []))
    reply_parts: list[str] = []
    candidate_parts: list[str] = list(saved.get("candidate_parts", []))
    candidate_final = saved.get("candidate_final", "")
    final_text = saved.get("final_text", "")
    delivery_finished = bool(saved.get("delivery_finished", False))
    replace_file_ids: list[str] = list(saved.get("replace_file_ids", []))
    if resumed:
        text_parts, candidate_parts = [], []
        candidate_final = final_text = ""
        delivery_finished = bool(committed_delivery)
        if delivery_finished:
            job.accepting = False
    analysis: AnalysisWorkspace | None = None
    run_id = saved.get("run_id")
    llm = None
    last_checkpoint = 0.0

    def checkpoint(*, force: bool = False) -> None:
        nonlocal last_checkpoint
        if force or time.monotonic() - last_checkpoint >= 1:
            snapshot = getattr(llm, "usage_snapshot", None)
            if callable(snapshot):
                recorder.llm_usage = snapshot()
            _save_assistant(conversation_id, "".join(text_parts).strip() or final_text, stored, recorder, run_id, finalize=False)
            if control:
                control.assistant_message_id = recorder.message_id
            last_checkpoint = time.monotonic()

    if control:
        control.ui_snapshot = lambda: {"stored": _checkpoint_ui_events(stored), "assistant_message_id": recorder.message_id,
            "text_parts": list(text_parts), "candidate_parts": list(candidate_parts), "candidate_final": candidate_final,
            "final_text": final_text, "delivery_candidates": deepcopy(delivery_candidates),
            "delivery_finished": delivery_finished, "replace_file_ids": list(replace_file_ids), "run_id": run_id}

    async def finish_execution(content: str) -> None:
        await job.wait_workers()
        job.accepting = False
        recorder.finish(content)
        snapshot = getattr(llm, "usage_snapshot", None)
        if callable(snapshot):
            recorder.llm_usage = snapshot()
        await emit({"type": "execution_status", **recorder.execution(),
                    "request_message_id": recorder.request_message_id, "job_id": recorder.job_id,
                    "assistant_message_id": recorder.message_id})
        # Learning reads the durable terminal answer and tool evidence, never a
        # candidate answer or the running UI checkpoint written by emit().
        verified = recorder.result.get("status") == "completed" and recorder.result.get("verified") is True
        if verified:
            _save_assistant(conversation_id, content, stored, recorder, run_id)
        if control:
            result = recorder.execution().get("status")
            status = {"completed": "completed", "failed": "failed", "cancelled": "cancelled",
                      "interrupted": "interrupted"}.get(result, "paused")
            control.persist(control.runtime.state if control.runtime else None, status=status)
        if verified and recorder.message_id is not None:
            try:
                with session_scope() as db:
                    learned = learn_verified_repairs(
                        db, conversation_id=conversation_id, user_id=user_id,
                        assistant_message_id=recorder.message_id, execution_id=job.execution_id,
                    )
                    items = [memory_service.memory_dict(item) for item in learned]
                if items:
                    event = {"type": "memory_saved", "items": items, "source": "verified_repair"}
                    stored.append(_slim(event))
                    _save_assistant(conversation_id, content, stored, recorder, run_id)
                    # Terminal checkpoints are immutable; the finished
                    # assistant message retains this optional learning receipt.
                    record_event("memory.saved", "sheetmate.services.memory.repair_experiences.learn_verified_repairs",
                                 {"items": items})
                    await job.emit(event)
            except Exception as exc:
                record_event("memory.error", "sheetmate.services.memory.repair_experiences.learn_verified_repairs", error_data(exc))

    def cancel_execution() -> None:
        user_stop = job.cancel_source == "user"
        recorder.error = "用户停止了生成" if user_stop else "执行被系统中断，尚未完成"
        recorder.record_stop({"status": "cancelled" if user_stop else "interrupted",
                              "code": "user_stop" if user_stop else "system_cancelled",
                              "summary": recorder.error, "source": job.cancel_source or "system"})

    checkpoint(force=True)

    async def finish_reply() -> None:
        if not reply_parts:
            return
        event = {"type": "reply", "content": "".join(reply_parts)}
        reply_parts.clear()
        stored.append(_slim(event))
        checkpoint(force=True)
        await job.emit(event)

    async def emit(event: dict[str, Any]) -> None:
        nonlocal final_text, first_reply_at
        if event.get("type") == "text_chunk" and event.get("content") and first_reply_at is None:
            first_reply_at = time.monotonic()
            record_event("chat.first_reply", "sheetmate.services.conversations.chat._run_chat",
                         {"elapsed_seconds": round(first_reply_at - started_at, 3), "job_id": job.id})
        if event.get("type") in {"model_call_done", "completion_check"}:
            snapshot = getattr(llm, "usage_snapshot", None)
            if callable(snapshot):
                event = {**event, "usage": snapshot()}
        event = externalize_script_references(event)
        if event.get("type") == "file" and event.get("delivery_status") == "candidate":
            if event.get("file_id") in delivery_candidates:
                # 工具重复展示本轮候选时仍须等待交付确认，并沿用原始生成证据。
                event = {**event, "explicit": False}
            if not event.get("explicit"):
                delivery_candidates.setdefault(event["file_id"], dict(event))
        bind_script_references(event, user_id=user_id, conversation_id=conversation_id)
        recorder.observe(event)
        if event["type"] not in {"text_chunk", "final_text"}:
            # 在下一个过程块之前保存已经展示的正文，历史恢复时保持同样顺序。
            await finish_reply()
        if event["type"] == "text_chunk":
            text_parts.append(event["content"])
            reply_parts.append(event["content"])
        elif event["type"] == "note":
            if event.get("retracted"):
                text_parts.clear()
                final_text = ""
            stored.append(_slim(event))
        elif event["type"] == "final_text":
            content = event.get("content", "")
            if content and not text_parts:
                text_parts.append(content)
                reply_parts.append(content)
            final_text = "".join(text_parts).strip()
            await finish_reply()
            checkpoint(force=True)
            return  # reply 结束当前正文块，兼顾实时展示与历史恢复。
        elif event["type"] in _KEEP_EVENTS:
            stored.append(_slim(event))
        if analysis is not None and event["type"] == "table":
            name = event.get("table", {}).get("name")
            if name and analysis.ws.exists(name):
                analysis.publish(name)
        checkpoint(force=event["type"] != "text_chunk" and event["type"] not in {"thinking", "recipe_thinking"})
        await job.emit(event)

    async def finalize_deliveries(answer: str) -> str:
        """单步生成不代表最终交付；收尾后只发布明确选定的正式文件。"""
        nonlocal delivery_finished
        if not delivery_candidates or delivery_finished:
            return ""
        recorder.finish(answer)
        confirmed = False
        finals: list[str] = []
        status = "incomplete"
        summary = "本轮尚未完成最终结果确认，生成的文件留在执行记录中，未作为正式结果交付。"
        if recorder.result.get("status") == "completed":
            await job.emit({"type": "activity", "text": "正在确认最终交付文件"})
            try:
                effective_request = control.runtime.effective_message(message) if control and control.runtime else message
                selection = await select_delivery(llm, request=effective_request, answer=answer,
                                                   candidates=list(delivery_candidates.values()), events=stored)
                confirmed, finals, summary = selection.confirmed, selection.file_ids, selection.summary
            except Exception as exc:
                summary = f"最终交付核对失败：{str(exc).strip()[:200] or type(exc).__name__}"
            status = "confirmed" if confirmed else "unverified"
        with session_scope() as db:
            replaced = file_service.finalize_delivery(db, conversation_id=conversation_id,
                candidate_file_ids=list(delivery_candidates), final_file_ids=finals if confirmed else [], summary=summary,
                replace_file_ids=replace_file_ids if confirmed else [])
            final_files = [file_service.file_dict(file_service.get_file(db, file_id)) for file_id in finals] if confirmed else []
            manifest = {"type": "delivery_manifest", "candidate_file_ids": list(delivery_candidates),
                        "final_file_ids": finals if confirmed else [], "status": status, "summary": summary,
                        "replaced_file_ids": replaced, "files": final_files}
            if control and confirmed:
                # File replacement and its recovery receipt share one commit.
                # Recovery must never repeat selection after a successful swap.
                durable_delivery = control.snapshot(control.runtime.state if control.runtime else None)
                durable_delivery["delivery_manifest"] = deepcopy(manifest)
                durable_delivery.setdefault("ui", {})["delivery_finished"] = True
                persisted = execution_state.save_checkpoint(db, job.execution_id, durable_delivery,
                    user_id=user_id, expected_version=control.version, assistant_message_id=recorder.message_id)
                delivery_version = persisted.version
        if control and confirmed:
            control.checkpoint, control.version = durable_delivery, delivery_version
        if status == "unverified":
            recorder.record_stop({"status": "incomplete", "code": "delivery_unverified", "summary": summary,
                                  "scope": "task", "verified": False})
        delivery_finished = True
        await emit(manifest)
        if status == "unverified":
            return "已生成文件，但最终交付版本尚未确认，本轮未交付正式结果。" + summary
        if status == "incomplete":
            return summary
        return ""

    try:
        # 用户已看到具体目标并确认后直接执行，不再通过模型判断第二次授权。
        confirmation = deepcopy(control.checkpoint.get("confirmed_recipe_deletion")) if control else None
        confirmed_ids = None
        durable_confirmation = None
        async with job.control_lock:
            with session_scope() as db:
                user = db.get(User, user_id)
                assert user is not None
                confirmed_ids = (set(confirmation["recipe_ids"]) if confirmation else None) if resumed else (
                    recipe_deletion.resolve_confirmation(db, user, conversation_id, message))
                # A queued correction may revoke the original confirmation.
                # Let the normal request path adopt every accepted message.
                has_corrections = control and any(
                    ((item.meta or {}).get("steering") or {}).get("execution_id") == job.execution_id
                    and ((item.meta or {}).get("steering") or {}).get("status") in {"queued", "committed"}
                    for item in db.scalars(select(Message).where(
                        Message.conversation_id == conversation_id, Message.role == "user")))
                if has_corrections:
                    confirmed_ids = None
                if confirmed_ids is not None:
                    conv = db.get(Conversation, conversation_id)
                    assert conv is not None
                    conv.mode = "analyze"
                    job.accepting = False
                    if control:
                        # Consuming the one-time confirmation and remembering
                        # its exact action must share the same commit.
                        durable_confirmation = control.snapshot()
                        if confirmation is None:
                            source_inputs = execution_state.get_execution(
                                db, job.execution_id, user_id=user_id).input_fingerprint
                            if any(source_inputs.get(key) != getattr(conv, key) for key in ("customer_id", "recipe_id")):
                                raise Conflict("客户或流程绑定已经变化，请重新发送需求。")
                            expected_recipe_id = conv.recipe_id
                            if conv.customer_id and conv.recipe_id in confirmed_ids:
                                customer = db.get(Customer, conv.customer_id)
                                remaining = list(db.scalars(select(RecipeRecord.recipe_id).where(
                                    RecipeRecord.customer_id == conv.customer_id,
                                    RecipeRecord.recipe_id.not_in(confirmed_ids)).group_by(RecipeRecord.recipe_id)
                                    .order_by(func.min(RecipeRecord.created_at), func.min(RecipeRecord.id))))
                                expected_recipe_id = (customer.primary_recipe_id if customer.primary_recipe_id in remaining
                                                      else remaining[0] if remaining else None)
                            confirmation = {
                                "action": "delete_recipes", "recipe_ids": sorted(confirmed_ids), "status": "confirmed",
                                "input_fingerprint": deepcopy(source_inputs), "expected_recipe_id": expected_recipe_id,
                            }
                        durable_confirmation["confirmed_recipe_deletion"] = confirmation
                        execution = execution_state.save_checkpoint(db, job.execution_id, durable_confirmation,
                            user_id=user_id, expected_version=control.version,
                            assistant_message_id=recorder.message_id)
                        confirmation_version = execution.version
            if durable_confirmation is not None:
                control.checkpoint, control.version = durable_confirmation, confirmation_version
        if confirmed_ids is not None:
            ctx, host = build_context(user_id, conversation_id, None)
            await job.emit({"type": "start", "mode": "analyze", "customer": ctx.customer["name"] if ctx.customer else None,
                            "resumed": resumed, "request_message_id": message_id,
                            "assistant_message_id": recorder.message_id, "job_id": job.id})
            deleted: list[dict[str, Any]] = []
            if confirmed_ids:
                host.delete_recipe_ids = set(confirmed_ids)
                call_id = "confirmed_recipe_deletion"
                arguments = {"recipe_ids": sorted(confirmed_ids)}
                result = None
                if control:
                    with session_scope() as db:
                        start = execution_state.start_tool(db, job.execution_id, user_id=user_id,
                            call_id=call_id, tool_name="delete_recipes", arguments=arguments,
                            input_fingerprint=control.checkpoint["confirmed_recipe_deletion"].get("input_fingerprint"))
                        if not start.should_execute:
                            result = deepcopy(start.receipt.result)
                call_event = {"type": "tool_call", "id": call_id, "tool": "delete_recipes",
                              "label": "删除已确认的流程", "params": arguments}
                if _slim(call_event) not in stored:
                    await emit(call_event)
                if result is None:
                    record_event("tool.input", "sheetmate.services.conversations.host.ConversationHost.delete_recipes_confirmed",
                                 arguments, call_id=call_id, tool="delete_recipes")
                    try:
                        deleted = host.delete_recipes_confirmed(sorted(confirmed_ids))
                    except BaseException as exc:
                        record_event("tool.error", "sheetmate.services.conversations.host.ConversationHost.delete_recipes_confirmed",
                                     error_data(exc), call_id=call_id, tool="delete_recipes", status="error")
                        raise
                    record_event("tool.result", "sheetmate.services.conversations.host.ConversationHost.delete_recipes_confirmed",
                                 {"deleted": deleted}, call_id=call_id, tool="delete_recipes", status="ok")
                    answer = f"已删除 {len(deleted)} 个流程。"
                    result = {"ok": True, "deleted": deleted, "text": answer}
                    if control:
                        durable_result = control.snapshot()
                        durable_result["confirmed_recipe_deletion"] = {
                            **control.checkpoint["confirmed_recipe_deletion"], "status": "completed",
                        }
                        with session_scope() as db:
                            execution_state.finish_tool(db, job.execution_id, user_id=user_id, call_id=call_id,
                                                        result=result)
                            execution = execution_state.get_execution(db, job.execution_id, user_id=user_id)
                            # Deleting a customer's active recipe can select
                            # its next primary recipe. That is this action's
                            # own authorized scope change, not new input.
                            conv = db.get(Conversation, conversation_id)
                            if (execution.input_fingerprint.get("recipe_id") in confirmed_ids
                                    and conv.customer_id == execution.input_fingerprint.get("customer_id")
                                    and conv.recipe_id == durable_result["confirmed_recipe_deletion"].get("expected_recipe_id")):
                                durable_result = execution_state.acknowledge_recipe_binding(db, job.execution_id,
                                    durable_result, user_id=user_id, call_id=call_id,
                                    previous_recipe_id=execution.input_fingerprint["recipe_id"], recipe_id=conv.recipe_id)
                            execution = execution_state.save_checkpoint(db, job.execution_id, durable_result,
                                user_id=user_id, expected_version=control.version,
                                assistant_message_id=recorder.message_id)
                            result_version = execution.version
                        control.checkpoint, control.version = durable_result, result_version
                deleted, answer = result["deleted"], result["text"]
                result_events = [{"type": "recipe_deleted", **item, "tool_call_id": call_id} for item in deleted]
                result_events.append({"type": "tool_result", "id": call_id, "tool": "delete_recipes", "ok": True,
                                      "summary": answer, "text": answer})
                for event in result_events:
                    if _slim(event) in stored:
                        await job.emit({**event, "replayed": True})
                    else:
                        await emit(event)
            else:
                answer = "本次没有删除流程。"
            if resumed and any(event.get("type") == "reply" and event.get("content") == answer for event in stored):
                text_parts.append(answer)
                final_text = answer
                await job.emit({"type": "reply", "content": answer, "replayed": True})
                checkpoint(force=True)
            else:
                await emit({"type": "text_chunk", "content": answer})
                await emit({"type": "final_text", "content": answer})
            await finish_execution(answer)
            _save_assistant(conversation_id, answer, stored, recorder, None)
            if deleted:
                await job.emit({"type": "recipes_changed", "recipes": [],
                                "deleted_recipe_ids": [item["recipe_id"] for item in deleted]})
                customer_service.schedule_summary_refresh(host.customer_id)
            await job.emit({"type": "done"})
            return
        llm = get_llm()
        ctx, host = build_context(user_id, conversation_id, llm)
        if control:
            from sheetmate.agent.memory.short_term.runtime import AgentRuntime
            control.restore_context(ctx, llm)
            control.runtime = AgentRuntime(control.checkpoint.get("runtime"), save=control.save, poll=control.poll,
                                          start_tool=control.start_tool, finish_tool=control.finish_tool)
            ctx.runtime = control.runtime
        host.request_message_id = message_id
        trace = current_trace()
        host.request_turn_id = trace.turn_id if trace is not None else None
        with session_scope() as db:
            conv = db.get(Conversation, conversation_id)
            request = db.get(Message, message_id)
            assert conv is not None and request is not None
            records = _attachment_records(db, conv, (request.meta or {}).get("attachments", []))
            ctx.image_parts = _image_parts(records)
            edit = (request.meta or {}).get("recipe_edit")
            if edit:
                from sheetmate.services.workflows.editing import resolve_edit_recipe, validate_recipe_edit
                user = db.get(User, user_id)
                assert user is not None
                saved_here = any(item["recipe_id"] == edit["recipe_id"] for item in host.saved_recipes)
                adapted_here = (host._last_recipe is not None and host._last_recipe.id == edit["recipe_id"]
                                and host._last_recipe.metadata.get("revision_base_version") == edit["version"])
                # A completed adaptation can be awaiting final delivery when
                # recovery starts, including an explicitly unsaved revision.
                if not (resumed and ctx.recipe_edit and (saved_here or adapted_here)):
                    ctx.recipe_edit = validate_recipe_edit(db, user, conv, edit)
                    base, _, _ = resolve_edit_recipe(db, user, conv, edit["recipe_id"])
                    host._edit_base_recipe = base.model_copy(deep=True)
                host.recipe_edit = dict(ctx.recipe_edit)
        history = await _history(conversation_id, llm, emit, current_message_id=message_id)
        _limit_history_images(history, ctx.image_parts)
        ctx.runtime_image_parts = [part for item in history for part in item.get("image_parts", [])] + ctx.image_parts
        files = host.list_files()
        references = file_references(conversation_id, message_id, files)
        saved_intent = control.checkpoint.get("intent") if control else None
        if control and await control.runtime.consume(ctx):
            saved_intent = None
        while True:
            effective_request = control.runtime.effective_message(message) if control else message
            intent = RequestIntent(**saved_intent) if saved_intent else await infer_request(
                llm, ctx, effective_request, history, plan_steps, files=files, file_references=references)
            if control and await control.runtime.consume(ctx):
                saved_intent = None
                continue
            if control and intent.files is not None and not await control.seal_if_idle():
                saved_intent = None
                continue
            break
        record_event("chat.intent.result", "sheetmate.agent.framework.shared.intent.infer_request", intent)
        ctx.mode = intent.mode
        ctx.requested_tool_groups = tuple(intent.tool_groups)
        ctx.execution_action = intent.action
        host.defer_delivery = intent.action == "process"
        replace_file_ids = list(intent.replace_file_ids)
        ctx.replace_file_ids = intent.replace_file_ids
        if intent.files is not None:
            selected_files = select_files(intent.files, files, references)
            with session_scope() as db:
                conv = db.get(Conversation, conversation_id)
                if conv is not None:
                    conv.mode = "analyze"
                request = db.get(Message, message_id)
                if request is not None:
                    request.meta = {**(request.meta or {}), "mode": "analyze", "action": "show_files"}
            await job.emit({"type": "start", "mode": "analyze", "customer": ctx.customer["name"] if ctx.customer else None})
            for file in selected_files:
                await emit({"type": "file", **{key: file[key] for key in (
                    "file_id", "filename", "kind", "kind_label", "size", "customer_id", "conversation_id", "run_id", "created_at", "sheets", "download_url", "media_type", "preview_url",
                ) if key in file}, "explicit": True})
            answer = await file_view_answer(llm, effective_request, intent.files, selected_files)
            if answer:
                await emit({"type": "text_chunk", "content": answer})
            await emit({"type": "final_text", "content": answer})
            await finish_execution(answer)
            _save_assistant(conversation_id, answer, stored, recorder, None)
            await job.emit({"type": "done"})
            return
        if intent.action == "process":
            ctx.save_recipe_requested = (ctx.save_recipe_requested if saved_intent else intent.save_recipe if intent.save_recipe is not None
                                         else await infer_recipe_persistence(llm, effective_request, history, recipe_edit=ctx.recipe_edit))
            host.save_recipe_requested = ctx.save_recipe_requested
            if not saved_intent:
                ctx.task_state = _advance_conversation_task(
                    conversation_id, message_id, relation=intent.task_relation,
                    save_recipe=ctx.save_recipe_requested,
                )
                if control:
                    for adopted in control.runtime.state.get("steers", []):
                        ctx.task_state = _advance_conversation_task(conversation_id, adopted["message_id"],
                            relation="continue", save_recipe=ctx.save_recipe_requested)
            if control:
                control.checkpoint["intent"] = asdict(intent)
                control.persist(control.runtime.state)
    except asyncio.CancelledError:
        cancel_execution()
        await finish_execution("")
        _save_assistant(conversation_id, "（已停止，尚未处理文件）", stored, recorder, None)
        raise
    except Exception as exc:
        record_event("chat.error", "sheetmate.services.conversations.chat._run_chat.context", error_data(exc))
        event = {"type": "error", "content": str(exc), "code": getattr(exc, "code", "execution_error")}
        if isinstance(exc, LLMNotConfigured):
            event["code"] = exc.code
        await emit(event)
        await finish_execution("")
        _save_assistant(conversation_id, f"⚠️ {exc}", stored, recorder, None)
        await job.emit({"type": "done"})
        return

    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is not None:
            conv.mode = ctx.mode
        request = db.get(Message, message_id)
        if request is not None:
            request.meta = {**(request.meta or {}), "mode": ctx.mode, "action": intent.action,
                            "replace_file_ids": replace_file_ids}
    # 思考模式：计划的各项、补做和自检共用一份更大的工具预算；闲聊（chat）不规划
    thinking = thinking and intent.action == "process"
    executor = AgentExecutor(llm, _registry, max_iterations=settings.agent_max_iterations,
                             **({"max_tool_calls": THINKING_TOOL_BUDGET} if thinking else {}))

    async def guard_creation_delivery(*, interrupted: bool = False) -> str:
        nonlocal candidate_final
        summary = (_creation_fallback(stored, interrupted=interrupted) if ctx.mode == "create" else "")
        summary = summary or _script_fallback(stored, mode=ctx.mode, interrupted=interrupted)
        if not summary:
            return ""
        recorder.record_stop({"status": "incomplete", "code": "missing_delivery", "summary": summary})
        previous = candidate_final or "".join(candidate_parts).strip() or final_text or "".join(text_parts).strip()
        candidate_parts.clear()
        candidate_final = ""
        if previous:
            # 候选正文从未展示；仅保留诊断记录，不让未经交付检查的声明进入用户过程。
            await emit({"type": "note", "content": previous, "retracted": True})
        await emit({"type": "text_chunk", "content": summary})
        await emit({"type": "final_text", "content": summary})
        return summary

    async def publish_candidate() -> None:
        nonlocal candidate_final
        content = candidate_final or "".join(candidate_parts)
        candidate_parts.clear()
        candidate_final = ""
        if content:
            await emit({"type": "text_chunk", "content": content})
            await emit({"type": "final_text", "content": content})

    try:
        async with execution_resources(job) as resources:
            if intent.action == "process":
                ready = bool(control and control.checkpoint.get("analysis_ready"))
                analysis = resources.enter_context(analysis_workspace(ctx.ws,
                    root=execution_root(job.execution_id) if control else None, resume=ready))
                ctx.ws = host.ws = analysis.ws
                host.analysis = analysis
                for table in ctx.ws.list_tables():
                    if table.file_id:
                        host.file_path(table.file_id)
                if resumed:
                    for event in stored:
                        if event.get("type") == "table":
                            name = (event.get("table") or {}).get("name")
                            if name and analysis.ws.exists(name):
                                analysis.publish(name)
                if ctx.mode == "analyze" and run_id is None:
                    with session_scope() as db:
                        run_id = run_service.create_run(db, kind="analysis", user_id=user_id, customer_id=ctx.host.customer_id,
                                                        conversation_id=conversation_id, request_text=message).id
                if control:
                    control.checkpoint["analysis_ready"] = True
                    control.persist(control.runtime.state)
            await job.emit({"type": "start", "mode": ctx.mode, "customer": ctx.customer["name"] if ctx.customer else None,
                            "resumed": resumed, "request_message_id": message_id,
                            "assistant_message_id": recorder.message_id, "job_id": job.id})
            if intent.action == "chat":
                stream = executor.converse(ctx, message, history)
            elif thinking:
                stream = execute_thinking(executor, ctx, message, history, plan_steps)
            elif plan_steps:
                stream = executor.execute_plan(ctx, message, plan_steps, history)
            elif should_activate(ctx, message):
                stream = execute_multi(executor, ctx, message, history)
            else:
                stream = executor.execute(ctx, message, history)
            async with aclosing(stream):
                async for event in stream:
                    # 新建/修改的完成声明要先核对实际交付，再作为固定正文发布。
                    # 分析中的脚本失败也需要同样保护，避免事后替换已经显示的结论。
                    guarded_reply = intent.action == "process" and (ctx.mode in {"create", "modify"}
                        or bool(delivery_candidates) or bool(_script_fallback(stored, mode=ctx.mode)))
                    if guarded_reply and event["type"] == "text_chunk":
                        candidate_parts.append(event["content"])
                    elif guarded_reply and event["type"] == "final_text":
                        candidate_final = event.get("content", "")
                    else:
                        await emit(event)
            # Close intake atomically with the last queue check. Requests that
            # arrived during a final audit still get their own full-goal pass.
            if control:
                while True:
                    if await control.seal_if_idle():
                        break
                    await control.runtime.consume(ctx)
                    candidate_parts.clear()
                    candidate_final = ""
                    stream = executor.continue_runtime(ctx, message, history)
                    async with aclosing(stream):
                        async for event in stream:
                            if event["type"] == "text_chunk":
                                candidate_parts.append(event["content"])
                            elif event["type"] == "final_text":
                                candidate_final = event.get("content", "")
                            else:
                                await emit(event)
    except asyncio.CancelledError:
        cancel_execution()
        answer = await guard_creation_delivery(interrupted=True)
        if not answer:
            if delivery_candidates:
                candidate_parts.clear()
                candidate_final = ""
            else:
                await publish_candidate()
            summary = _recipe_fallback(stored, interrupted=True) or "（已停止）"
            separator = "\n\n" if text_parts else ""
            await emit({"type": "text_chunk", "content": separator + summary})
            answer = "".join(text_parts).strip()
            await emit({"type": "final_text", "content": answer})
        await finalize_deliveries(answer)
        await finish_execution(answer)
        _save_assistant(conversation_id, answer, stored, recorder, run_id)
        if host.deleted_recipes:
            customer_service.schedule_summary_refresh(host.customer_id)
        raise
    except Exception as exc:
        record_event("chat.error", "sheetmate.services.conversations.chat._run_chat.execute", error_data(exc))
        await emit({"type": "error", "content": f"处理出错：{exc}", "code": getattr(exc, "code", "execution_error")})

    answer = await guard_creation_delivery()
    if not answer:
        answer = candidate_final or "".join(candidate_parts).strip() or final_text or "".join(text_parts).strip()
    try:
        delivery_problem = await finalize_deliveries(answer)
    except asyncio.CancelledError:
        cancel_execution()
        await finalize_deliveries(answer)
        candidate_parts.clear()
        candidate_final = ""
        answer = "（已停止）"
        await emit({"type": "text_chunk", "content": answer})
        await emit({"type": "final_text", "content": answer})
        await finish_execution(answer)
        _save_assistant(conversation_id, answer, stored, recorder, run_id)
        raise
    except Exception as exc:
        record_event("chat.error", "sheetmate.services.conversations.chat._run_chat.finalize_deliveries", error_data(exc))
        delivery_problem = "最终交付文件未能确认，本轮未交付正式结果。"
        reason = str(exc).strip()[:200]
        if reason:
            delivery_problem += f"核对失败原因：{reason}"
        await emit({"type": "execution_stop", "status": "incomplete", "code": "delivery_unverified",
                    "summary": delivery_problem, "scope": "task", "verified": False})
        await emit({"type": "delivery_manifest", "candidate_file_ids": list(delivery_candidates),
                    "final_file_ids": [], "status": "unverified", "summary": delivery_problem})
        delivery_finished = True
    if delivery_problem:
        candidate_parts.clear()
        candidate_final = ""
        answer = delivery_problem
        await emit({"type": "text_chunk", "content": answer})
        await emit({"type": "final_text", "content": answer})
    else:
        await publish_candidate()
        answer = final_text or "".join(text_parts).strip()
    if not answer or recorder.error:
        summary = _recipe_fallback(stored, interrupted=bool(recorder.error))
        if summary:
            addition = ("\n\n" if answer else "") + summary
            await emit({"type": "text_chunk", "content": addition})
            answer += addition
            await emit({"type": "final_text", "content": answer})
    if not answer and recorder.error:
        answer = f"⚠️ {recorder.error}"
    await finish_reply()
    recorder.llm_usage_pending = True
    await finish_execution(answer)
    _save_assistant(conversation_id, answer, stored, recorder, run_id)
    execution = _persist_llm_usage(conversation_id, recorder.message_id, llm)
    if execution is not None:
        await job.emit({"type": "execution_status", **execution,
                        "request_message_id": recorder.request_message_id, "job_id": recorder.job_id})
    if host.saved_recipes or host.deleted_recipes:
        await job.emit({"type": "recipes_changed", "recipes": host.saved_recipes,
                        "deleted_recipe_ids": [item["recipe_id"] for item in host.deleted_recipes]})
        customer_service.schedule_summary_refresh(ctx.host.customer_id)  # 流程变了：后台更新客户的整体处理逻辑
    record_event("chat.answer_ready", "sheetmate.services.conversations.chat._run_chat",
                 {"elapsed_seconds": round(time.monotonic() - started_at, 3), "job_id": job.id,
                  "memory_candidate": intent.memory_candidate})
    # 标题和记忆共享一个后台生命周期，只有两者都结束才结清本轮用量。
    trace = current_trace()
    task = asyncio.create_task(_finish_background(
        llm, user_id, conversation_id, ctx.host.customer_id, message, answer,
        recipe_id=ctx.host.recipe_id, source_message_id=message_id,
        source_turn_id=trace.turn_id if trace is not None else None,
        assistant_message_id=recorder.message_id, memory_candidate=intent.memory_candidate,
        additional_memory_sources=deepcopy(control.runtime.state.get("steers", [])) if control and control.runtime else None,
    ))
    _background.add(task)
    task.add_done_callback(_background.discard)
    await job.emit({"type": "done"})


def _script_fallback(events: list[dict[str, Any]], *, mode: str, interrupted: bool = False) -> str:
    """脚本失败或仅返回辅助数据时，不把最后一轮模型话术当成已交付修改。"""
    calls = [(index, event) for index, event in enumerate(events)
             if event.get("type") == "tool_call" and event.get("tool") == "run_python_script"
             and event.get("error_kind") not in {"protocol", "schema"}]
    if not calls or any(event.get("type") == "clarify" for event in events):
        return ""
    index, call = calls[-1]
    following = events[index + 1:]
    result = next((event for event in following if event.get("type") == "tool_result" and event.get("id") == call.get("id")), None)
    files = [event for event in following if event.get("type") == "file" and event.get("file_id")]
    # 修改/建表恢复必须有文件；查看日志、读取表信息等成功不能抵消脚本失败。
    later_success = any(event.get("type") == "tool_result" and event.get("ok")
                        and event is not result and event.get("tool") == "run_sql" for event in following)
    successful_ids = {event.get("id") for event in following if event.get("type") == "tool_result"
                      and event.get("ok") and event is not result and event.get("id")}
    later_success = later_success or any(event.get("type") == "table" and event.get("tool_call_id") in successful_ids
                                         for event in following)
    if mode == "analyze" and later_success:
        return ""
    if result is not None and result.get("ok") and (mode == "analyze" or files):
        return ""
    if files:
        return ""
    if result is not None and result.get("ok"):
        # 已交付文件后的后台核对不需要再交付一个测试文件。
        if any(event.get("type") == "file" and event.get("file_id") for event in events[:index]):
            return ""
        return "脚本已执行并返回辅助结果，本轮尚未生成可下载的工作簿，文件交付仍需继续完成。"
    message = "最近一次脚本调整尚未完成，本次尝试没有交付文件。"
    if interrupted:
        return message + "本次处理已停止。"
    if result is not None:
        details = str(result.get("summary") or result.get("text") or "").strip().splitlines()
        reason = details[0][:200] if details else ""
        if reason:
            message += f"\n\n工具返回：{reason}"
    previous_files = [event for event in events[:index] if event.get("type") == "file" and event.get("file_id")]
    if previous_files:
        pending = any(event.get("delivery_status") == "candidate" for event in previous_files)
        message += ("\n\n较早生成的文件留在执行记录中；最近一次调整尚未得到新文件。" if pending
                    else "\n\n较早生成的文件仍可查看；最近一次调整尚未得到新文件。")
    return message


def _creation_fallback(events: list[dict[str, Any]], *, interrupted: bool = False) -> str:
    """完整建表任务的交付事实；计划中的设计步骤不单独触发。"""
    if any(event.get("type") == "file" and event.get("file_id") for event in events):
        return ""
    if any(event.get("type") == "clarify" for event in events):
        return ""
    blocks = ["本轮尚未生成可下载的 Excel 工作簿。"]
    if interrupted:
        blocks.append("本次处理已停止。")
    failure = next((event for event in reversed(events)
                    if event.get("type") == "tool_result" and event.get("ok") is False
                    and event.get("error_kind") not in {"protocol", "schema"}), None)
    if failure is not None:
        details = str(failure.get("summary") or failure.get("text") or "").strip().splitlines()
        # 布局校验返回 Pydantic 文本时，展示实际原因而非英文校验标题或字段路径。
        reason = next((line.split("Value error, ", 1)[1].split(" [type=", 1)[0].strip()
                       for line in details if "Value error, " in line), details[0].strip() if details else "")
        if reason:
            blocks.append(f"最近一次处理未完成：{reason[:200]}")
    elif not interrupted:
        error = next((str(event.get("content") or "").strip().splitlines()[0]
                      for event in reversed(events)
                      if event.get("type") == "error" and str(event.get("content") or "").strip()), "")
        blocks.append(f"处理未完成：{error[:200]}" if error else "建表和文件交付尚未完成。")
    return "\n\n".join(blocks)


def _recipe_fallback(events: list[dict[str, Any]], *, interrupted: bool = False) -> str:
    calls = [event for event in events if event.get("type") == "tool_call"
             and event.get("error_kind") not in {"protocol", "schema"}
             and event.get("tool") in {"build_recipe", "revise_recipe", "run_recipe"}]
    if not calls:
        return ""
    results = {event.get("id"): event for event in events if event.get("type") == "tool_result"}
    failed = sum(results.get(call.get("id"), {}).get("ok") is False for call in calls)
    latest = results.get(calls[-1].get("id"))
    selected_run = next((event["selected_run"] for event in reversed(events)
                         if event.get("type") in {"recipe_completed", "recipe_saved"} and event.get("tool_call_id") == calls[-1].get("id")
                         and isinstance(event.get("selected_run"), dict)), {})
    statuses: dict[str, bool] = {}
    for event in events:
        if event.get("type") == "delivery_manifest":
            finals = set(event.get("final_file_ids", [])) if event.get("status") == "confirmed" else set()
            statuses.update({file_id: file_id in finals for file_id in event.get("candidate_file_ids", [])})
    generated = {event["file_id"] for event in events if event.get("type") == "file" and event.get("file_id")}
    files = {event["file_id"] for event in events if event.get("type") == "file" and event.get("file_id")
             and (event.get("explicit") or statuses.get(event["file_id"],
                  not event.get("intermediate") and event.get("delivery_status") not in {"candidate", "intermediate"}))}
    waiting = any(event.get("type") == "clarify" for event in events)
    if interrupted:
        outcome = "本次处理已中断，尚未完成最终结果确认。"
    elif latest is None:
        outcome = "最近一次流程尚未完成，暂时不能确认修改结果。"
    elif latest.get("ok"):
        outcome = "最近一次流程已执行完成。"
    else:
        outcome = "最近一次修改没有完成。"
    blocks = [f"本轮共进行了 {len(calls)} 次流程处理，其中 {failed} 次返回失败。{outcome}"]
    if latest is not None and latest.get("ok"):
        report = selected_run.get("report") or {}
        verification = selected_run.get("verification") or {}
        if not verification.get("informational") and verification.get("status") in {"fail", "warn"}:
            blocks.append("所选结果与提供的人工样例仍有差异，需要核对后再使用。")
        elif report.get("status") == "warning" or (report.get("validation") or {}).get("status") in {"fail", "warn"}:
            blocks.append("所选结果仍有运行校验或输出提示需要核对，请查看处理流程中的具体说明。")
    if latest is not None and not latest.get("ok"):
        related = [event for event in events if event.get("tool_call_id") == calls[-1].get("id")]
        reason = next((str(event.get("error")) for event in reversed(related)
                       if event.get("type") == "recipe_failed" and event.get("error")), "")
        if not reason:
            reason = next((str(event.get("summary")) for event in reversed(related)
                           if event.get("type") == "recipe_run_done" and event.get("summary")), "")
        reason = reason or str(latest.get("summary") or "")
        if "TemplatePreservationError" in reason:
            reason = "写回工作簿时，原表格式、合并结构或公式的兼容处理没有通过。"
        elif "没有输入表" in reason or "没有找到" in reason or "找不到" in reason:
            reason = "没有找到本次处理所需的文件、表格或已保存的流程。"
        elif "JSON" in reason:
            reason = "生成的处理步骤或参数无法被正确解析。"
        elif reason:
            reason = reason.splitlines()[0][:200]
        if reason:
            blocks.append(f"未完成的原因：{reason}")
    blocks.append(f"本轮已确认 {len(files)} 个可下载文件，可查看并核对正式结果。" if files else
                  "生成的文件仅保留在执行记录中，尚未交付正式结果。" if generated else
                  "本轮尚未生成可下载的结果文件。")
    if interrupted:
        blocks.append("可以继续本次任务，先核对已完成的步骤，再处理剩余部分。")
    elif waiting:
        blocks.append("请先回答下方的具体问题，确认处理口径后再继续。")
    elif latest is None or not latest.get("ok"):
        blocks.append("下一步可针对失败原因调整处理步骤，再从原文件的新副本继续；具体错误保留在执行明细中。")
    elif not files:
        blocks.append("下一步需要检查输出步骤，生成可下载的结果文件后再确认完成。")
    return "\n\n".join(blocks)


def _restore_delivery_candidates(events: list[dict[str, Any]], previous: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Receipted file events may outrun the UI checkpoint before a restart."""
    candidates = deepcopy(previous)
    for index, event in enumerate(events):
        if event.get("type") != "file" or event.get("delivery_status") != "candidate" or not event.get("file_id"):
            continue
        if event["file_id"] in candidates:
            event = events[index] = {**event, "explicit": False}
        if not event.get("explicit"):
            candidates.setdefault(event["file_id"], dict(event))
    return candidates


def _checkpoint_ui_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """UI keeps complete flows/cards; loop checkpoints keep scoped locators."""
    result, seen = [], set()
    for event in events:
        reference = event.get("_tool_result_ref")
        if isinstance(reference, dict) and reference.get("result_id"):
            identity = (reference["result_id"], event.get("tool_call_id"), reference.get("branch_id"))
            if identity in seen:
                continue
            seen.add(identity)
            result.append({"type": "tool_result_events", "result_id": identity[0],
                           "tool_call_id": identity[1], "branch_id": identity[2]})
        else:
            result.append(deepcopy(event))
    return result


def _restore_ui_events(events: list[dict[str, Any]], user_id: int, conversation_id: str) -> list[dict[str, Any]]:
    from sheetmate.services.execution.tool_results import resolve_tool_result_events

    result = []
    for event in events:
        if event.get("type") != "tool_result_events":
            result.append(deepcopy(event))
            continue
        reference = {"result_id": event.get("result_id"), "branch_id": event.get("branch_id")}
        with session_scope() as db:
            recovered = resolve_tool_result_events(db, user_id=user_id, conversation_id=conversation_id,
                result_id=reference["result_id"], branch_id=reference["branch_id"])
        result.extend({**item, "tool_call_id": event.get("tool_call_id"), "_tool_result_ref": reference}
                      for item in recovered)
    return result


def _slim(event: dict[str, Any]) -> dict[str, Any]:
    """存库前精简：表格预览只留前 20 行，去掉大段文本。"""
    item = externalize_script_references(event)
    if item.get("type") == "table" and isinstance(item.get("table"), dict):
        table = dict(item["table"])
        table["preview"] = (table.get("preview") or [])[:20]
        item["table"] = table
    if item.get("type") == "recipe_step_done" and isinstance(item.get("preview"), dict):
        item["preview"] = {**item["preview"], "rows": (item["preview"].get("rows") or [])[:20]}
    if item.get("type") == "tool_result":
        item["text"] = str(item.get("text", ""))[:1500]
    if item.get("type") == "recipe_run_done" and isinstance(item.get("report"), dict):
        report = dict(item["report"])
        report["steps"] = [{k: v for k, v in s.items() if k not in {"preview", "sql"}} for s in report.get("steps", [])]
        item["report"] = report
    if item.get("type") in {"recipe_completed", "recipe_saved"} and isinstance(item.get("selected_run"), dict):
        selected_run = dict(item["selected_run"])
        if isinstance(selected_run.get("report"), dict):
            report = dict(selected_run["report"])
            report["steps"] = [{k: v for k, v in s.items() if k not in {"preview", "sql"}} for s in report.get("steps", [])]
            selected_run["report"] = report
        item["selected_run"] = selected_run
    return item


def _save_assistant(conversation_id: str, content: str, events: list[dict[str, Any]], recorder: WorkflowRecorder,
                    run_id: str | None, *, finalize: bool = True) -> None:
    if finalize:
        recorder.finish(content)
    record = recorder.to_dict(final=finalize)
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            return
        meta = {"mode": conv.mode, "digest": recorder.digest(), "tool_sequence": record["tool_sequence"], "run_id": run_id,
                "execution": record["execution"], "request_message_id": recorder.request_message_id, "job_id": recorder.job_id}
        if getattr(recorder, "execution_id", None):
            meta["execution_id"] = recorder.execution_id
        replies = [recorder.request_message_id] if recorder.request_message_id else []
        if getattr(recorder, "execution_id", None):
            replies.extend(item.id for item in db.scalars(select(Message).where(
                Message.conversation_id == conversation_id, Message.role == "user").order_by(Message.id))
                if ((item.meta or {}).get("steering") or {}).get("execution_id") == recorder.execution_id
                and ((item.meta or {}).get("steering") or {}).get("status") == "committed")
        meta["reply_to_message_ids"] = replies
        trace = current_trace()
        if trace is not None:
            meta["log_turn_id"] = trace.turn_id
        assistant = db.get(Message, recorder.message_id) if recorder.message_id else None
        if assistant is None:
            assistant = conv_service.add_message(db, conv, "assistant", content, events=events, meta=meta)
            recorder.message_id = assistant.id
        else:
            assistant.content = content
            assistant.events = list(events)
            assistant.meta = meta
        state = dict(conv.task_state or {})
        if state and state.get("current_message_id") in replies:
            execution = record["execution"]
            state["status"] = execution.get("status", "running")
            state["last_assistant_message_id"] = assistant.id
            state["evidence_ids"] = list(dict.fromkeys([
                *(state.get("evidence_ids") or []),
                *((event.get("evidence") or {}).get("id") or event.get("evidence_id") or event.get("id") for event in events
                  if event.get("type") == "tool_result" and event.get("ok")
                  and (event.get("evidence_id") or event.get("id"))),
            ]))[-200:]
            if execution.get("status") == "completed":
                state["unresolved"] = []
            elif execution.get("status") != "running":
                state["unresolved"] = [execution.get("summary") or execution.get("code") or "上次任务未完成"]
            conv.task_state = state
        if run_id and finalize:
            if record["steps"] or record["execution"].get("status") != "completed":
                run_service.finish_analysis_run(db, run_id, record)
            else:
                run = db.get(run_service.Run, run_id)
                if run is not None:
                    db.delete(run)
        elif run_id:
            run = db.get(run_service.Run, run_id)
            if run is not None:
                run.steps = record["steps"]
                run.tool_sequence = record["tool_sequence"]
                run.validation = {**(run.validation or {}), "execution": record["execution"]}
        if finalize:
            record_event("chat.output", "sheetmate.services.conversations.chat._save_assistant",
                         {"message_id": assistant.id, "content": content,
                          "execution": {key: record["execution"][key] for key in (
                              "status", "code", "summary", "source") if key in record["execution"]},
                          "artifacts": [event for event in events if event.get("type") in {
                              "file", "table", "delivery_manifest", "clarify", "recipe_deleted"}]})


def _contains_unconfigured_llm_error(text: str) -> bool:
    return contains_llm_not_configured_text(text)


def _persist_llm_usage(conversation_id: str, message_id: int | None, llm: Any,
                       *, pending: bool | None = None) -> dict[str, Any] | None:
    """Include late title/memory calls in their originating turn's cost record."""
    snapshot = getattr(llm, "usage_snapshot", None)
    if message_id is None or not callable(snapshot):
        return
    usage = snapshot()
    with session_scope() as db:
        message = db.get(Message, message_id)
        if message is None or message.conversation_id != conversation_id or message.role != "assistant":
            return
        meta = dict(message.meta or {})
        execution = {**(meta.get("execution") or {}), "llm_usage": usage}
        if pending is not None:
            execution["llm_usage_pending"] = pending
        message.meta = {**meta, "execution": execution}
        # History replays the status event as well as meta; both must expose the
        # same snapshot after late title or background-memory calls settle.
        events = list(message.events or [])
        for index in range(len(events) - 1, -1, -1):
            if events[index].get("type") == "execution_status":
                events[index] = {"type": "execution_status", **execution,
                                 "request_message_id": meta.get("request_message_id"), "job_id": meta.get("job_id")}
                message.events = events
                break
        run = db.get(run_service.Run, meta["run_id"]) if meta.get("run_id") else None
        if run is not None:
            run.validation = {**(run.validation or {}), "execution": execution}
        return execution


def recover_interrupted_executions() -> None:
    """A persisted running checkpoint survived its process; the exact interruption cause is unknown."""
    reason = "服务已重启，上次执行未完成；停止的具体原因未知。"
    with session_scope() as db:
        for execution in execution_state.discover_recoverable(db, limit=1000):
            if execution.status == "running":
                execution_state.save_checkpoint(db, execution.id, execution.checkpoint,
                    user_id=execution.user_id, expected_version=execution.version, status="interrupted")
        # A finished response may still have been waiting for background memory
        # usage when the process stopped. There is no remaining work to poll.
        for assistant in db.scalars(select(Message).where(
            Message.role == "assistant", Message.meta["execution"]["llm_usage_pending"].as_boolean() == True,
        )):
            meta = dict(assistant.meta or {})
            execution = {**(meta.get("execution") or {}), "llm_usage_pending": False}
            assistant.meta = {**meta, "execution": execution}
            assistant.events = [{**event, "llm_usage_pending": False} if event.get("type") == "execution_status"
                                else event for event in assistant.events or []]
        for assistant in db.scalars(select(Message).where(
            Message.role == "assistant", Message.meta["execution"]["status"].as_string() == "running",
        )):
            meta = dict(assistant.meta or {})
            execution = dict(meta.get("execution") or {})
            if execution.get("status") != "running":
                continue
            execution.update(status="interrupted", code="process_restart", summary=reason, source="unknown")
            status = {"type": "execution_status", **execution,
                      "request_message_id": meta.get("request_message_id"), "job_id": meta.get("job_id")}
            assistant.meta = {**meta, "execution": execution}
            assistant.events = [*(assistant.events or []), status]
            conv = db.get(Conversation, assistant.conversation_id)
            if conv is not None and (conv.task_state or {}).get("current_message_id") == meta.get("request_message_id"):
                conv.task_state = {**conv.task_state, "status": "interrupted", "unresolved": [reason]}
            if not assistant.content:
                assistant.content = reason
            if meta.get("log_turn_id"):
                trace = new_turn(assistant.conversation_id, turn_id=meta["log_turn_id"])
                trace.record("chat.output", "sheetmate.services.conversations.chat.recover_interrupted_executions",
                             {"message_id": assistant.id, "content": assistant.content})
                trace.record("turn.recovered", "sheetmate.services.conversations.chat.recover_interrupted_executions",
                             {"code": "process_restart", "source": "unknown"})
                trace.record("turn.end", "sheetmate.services.conversations.chat.recover_interrupted_executions",
                             {"status": "interrupted", "code": "process_restart", "summary": reason})
        # Include scripts/recipes whose process stopped between writing a run and linking its message.
        for run in db.scalars(select(run_service.Run).where(run_service.Run.status == "running")):
            execution = dict((run.validation or {}).get("execution") or {})
            execution.update(status="interrupted", code="process_restart", summary=reason, source="unknown")
            run.status = "interrupted"
            run.error = reason
            run.validation = {**(run.validation or {}), "execution": execution}
            run.finished_at = run_service.now()


async def resume_startup_executions() -> None:
    """Continue only the latest request's safe interrupted checkpoint at startup."""
    from sheetmate.core.config.settings import get_llm_settings
    if not get_llm_settings().configured:
        return
    with session_scope() as db:
        candidates = [(execution.id, execution.conversation_id, execution.user_id)
                      for execution in execution_state.discover_recoverable(db, limit=1000)
                      if execution.status == "interrupted" and execution.checkpoint.get("runtime")]
    for execution_id, conversation_id, user_id in candidates:
        try:
            with session_scope() as db:
                user = db.get(User, user_id)
                if user is None:
                    continue
                conv = conv_service.get_conversation(db, user, conversation_id)
                current = latest_execution(db, conversation_id)
                if (current is None or current.id != execution_id or current.status != "interrupted"
                        or not current.checkpoint.get("runtime")):
                    continue
                from sheetmate.services.execution.control import execution_view
                view = execution_view(db, conv)
                if not view or not view.get("can_resume"):
                    continue
            resume_chat(user, conversation_id)
        except ServiceError as exc:
            record_event("execution.recovery_skipped", "sheetmate.services.conversations.chat.resume_startup_executions",
                         {"conversation_id": conversation_id, "reason": str(exc)})
        await asyncio.sleep(0)


def persist_unfinished_job(job: Job, *, status: str, code: str, summary: str) -> dict[str, Any] | None:
    """Last-resort persistence when orchestration fails outside its normal finalization path."""
    with session_scope() as db:
        if job.execution_id:
            durable = db.get(AgentExecution, job.execution_id)
            if durable is not None and durable.status in execution_state.RECOVERABLE_STATUSES:
                execution_state.save_checkpoint(db, durable.id, durable.checkpoint, user_id=durable.user_id,
                    expected_version=durable.version, status=status)
        conv = db.get(Conversation, job.key)
        if conv is None:
            return
        assistant = next((item for item in db.scalars(select(Message).where(
            Message.conversation_id == job.key, Message.role == "assistant").order_by(Message.id.desc()))
            if (item.meta or {}).get("job_id") == job.id), None)
        if assistant is not None and ((assistant.meta or {}).get("execution") or {}).get("status") != "running":
            return
        meta = dict(assistant.meta or {}) if assistant is not None else {"job_id": job.id, "request_message_id": job.message_id}
        execution = {**(meta.get("execution") or {}), "status": status, "code": code, "summary": summary,
                     "source": job.cancel_source or "system"}
        if (conv.task_state or {}).get("current_message_id") == job.message_id:
            conv.task_state = {**conv.task_state, "status": status, "unresolved": [summary]}
        event = {"type": "execution_status", **execution,
                 "request_message_id": meta.get("request_message_id"), "job_id": meta.get("job_id")}
        if assistant is None:
            assistant = conv_service.add_message(db, conv, "assistant", summary, events=[event], meta={**meta, "execution": execution})
        else:
            assistant.meta = {**meta, "execution": execution}
            assistant.events = [*(assistant.events or []), event]
            if not assistant.content:
                assistant.content = summary
            run_id = meta.get("run_id")
            run = db.get(run_service.Run, run_id) if run_id else None
            if run is not None and run.status == "running":
                run.status, run.error, run.finished_at = status, summary, run_service.now()
                run.validation = {**(run.validation or {}), "execution": execution}
        record_event("chat.output", "sheetmate.services.conversations.chat.persist_unfinished_job",
                     {"message_id": assistant.id, "content": assistant.content,
                      "execution": {"status": status, "code": code, "summary": summary,
                                    "source": job.cancel_source or "system"}})
        return event


def _limit_history_images(history: list[dict[str, Any]], current: list[dict[str, Any]]) -> None:
    """Prioritize current attachments and recent turns within the combined request budget."""
    remaining = _MAX_IMAGE_REQUEST_BYTES - sum(
        len(part["image_url"]["url"]) for part in current if part["type"] == "image_url"
    )
    for item in reversed(history):
        parts = item.get("image_parts", [])
        size = sum(len(part["image_url"]["url"]) for part in parts if part["type"] == "image_url")
        if size <= remaining:
            remaining -= size
        else:
            item.pop("image_parts", None)


def _history_snapshot(conversation_id: str, current_message_id: int | None) -> tuple[
    str, int, list[dict[str, Any]], dict[str, Any] | None,
]:
    """Read one consistent summary/cursor pair and the source messages after it."""
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        assert conv is not None
        if _contains_unconfigured_llm_error(conv.context_summary):
            # Old summaries may have absorbed a setup failure. Rebuild them from messages,
            # where the obsolete assistant error can be identified without dropping user text.
            db.execute(update(Conversation).where(
                Conversation.id == conversation_id, Conversation.summary_until == conv.summary_until,
                Conversation.context_summary == conv.context_summary,
            ).values(context_summary="", summary_until=0))
            db.refresh(conv)
        boundary = current_message_id
        if boundary is None:
            boundary = db.scalar(select(Message.id).where(
                Message.conversation_id == conversation_id,
            ).order_by(Message.id.desc()).limit(1))
        query = select(Message).where(Message.conversation_id == conversation_id, Message.id > conv.summary_until)
        primer_query = select(Message).where(
            Message.conversation_id == conversation_id, Message.role == "user", Message.content != "",
        )
        if boundary is not None:
            query = query.where(Message.id < boundary)
            primer_query = primer_query.where(Message.id < boundary)
        rows = list(db.scalars(query.order_by(Message.id)))
        first_user = db.scalar(primer_query.order_by(Message.id).limit(1))
        primer = {"id": first_user.id, "content": first_user.content} if first_user else None
        items = [{"id": m.id, "role": m.role, "content": _history_content(m)}
                 for m in rows if not is_llm_not_configured_message(m.role, m.content, m.events)
                 and ((m.meta or {}).get("steering") or {}).get("status") not in {"queued", "withdrawn"}]
        return conv.context_summary, conv.summary_until, items, primer


def _history_projection(summary: str, items: list[dict[str, Any]],
                        primer: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Pinned opening goal, derived summary, then recent authoritative messages."""
    history: list[dict[str, Any]] = []
    if summary or history_tokens(items) > HISTORY_TOKEN_BUDGET:
        opening = primer_block(primer)
        if opening:
            history.append({"role": "assistant", "content": opening, "context_primer": True,
                            "message_id": primer["id"]})
    body = summary_without_primer(summary)
    if body:
        history.append({"role": "assistant", "content": body, "context_summary": True})
    history += [{"role": item["role"], "content": item["content"], "message_id": item["id"]}
                for item in items if item["content"].strip()]
    return history


async def _history(conversation_id: str, llm: Any, emit: Any, *, current_message_id: int | None = None) -> list[dict[str, Any]]:
    """Build primer + summary + recent history, committing complete source batches only."""
    summary, summary_until, items, primer = _history_snapshot(conversation_id, current_message_id)
    conflicts = 0
    record_event("history.selection", "sheetmate.services.conversations.chat._history", {
        "summary_until": summary_until, "source_count": len(items),
        "estimated_tokens": history_tokens(_history_projection(summary, items, primer)),
        "history_token_budget": HISTORY_TOKEN_BUDGET,
        "primer_message_id": primer["id"] if primer else None,
    })
    while history_tokens(_history_projection(summary, items, primer)) > HISTORY_TOKEN_BUDGET:
        # Keep recent dialogue under its own token budget, rather than treating
        # a very long spreadsheet discussion as equivalent to a short greeting.
        cut = len(items)
        recent_tokens = 0
        while cut > 0:
            next_tokens = history_tokens([items[cut - 1]])
            if cut < len(items) and recent_tokens + next_tokens > RECENT_HISTORY_TOKENS:
                break
            recent_tokens += next_tokens
            cut -= 1
        batch = compaction_batch(items[:cut])
        if not batch:
            break
        record_event("history.compaction.batch", "sheetmate.services.conversations.chat._history", {
            "summary_until": summary_until, "source_first_id": batch[0]["id"],
            "source_last_id": batch[-1]["id"], "source_count": len(batch),
            "source_tokens": history_tokens(batch), "pending_count": len(items),
        })
        with session_scope() as db:
            # Original user wording remains authoritative across every cursor.
            anchor_rows = list(db.scalars(select(Message).where(
                Message.conversation_id == conversation_id, Message.role == "user",
                Message.id <= batch[-1]["id"], Message.content != "",
            ).order_by(Message.id.desc()).limit(RECENT_USER_ANCHORS)))
            user_anchors = [{"id": m.id, "content": m.content} for m in reversed(anchor_rows)]
        summary_item = [{"role": "assistant", "content": summary, "context_summary": True}] if summary else []
        text = await compact_history(llm, [*summary_item, *batch], user_anchors=user_anchors, primer=primer)
        if not text:
            record_event("history.compaction.failed", "sheetmate.services.conversations.chat._history", {
                "summary_until": summary_until, "source_last_id": batch[-1]["id"],
                "source_count": len(batch),
            })
            break
        with session_scope() as db:
            result = db.execute(update(Conversation).where(
                Conversation.id == conversation_id, Conversation.summary_until == summary_until,
                Conversation.context_summary == summary,
            ).values(context_summary=text, summary_until=batch[-1]["id"]))
            committed = result.rowcount == 1
        if not committed:
            # Reload the winning projection, including newly uncovered rows.
            # Returning the losing text here would hide history despite the CAS.
            summary, summary_until, items, primer = _history_snapshot(conversation_id, current_message_id)
            conflicts += 1
            record_event("history.compaction.conflict", "sheetmate.services.conversations.chat._history", {
                "summary_until": summary_until, "source_count": len(items), "conflicts": conflicts,
            })
            if conflicts >= 2:
                break
            continue
        summary, summary_until = text, batch[-1]["id"]
        items = items[len(batch):]
        record_event("history.compaction.committed", "sheetmate.services.conversations.chat._history", {
            "summary_until": summary_until, "source_count": len(batch), "remaining_count": len(items),
            "summary_tokens": history_tokens([{"content": summary}]),
        })
        await emit({"type": "compacted", "old_count": len(batch), "summary": text[:120]})
    history = _history_projection(summary, items, primer)
    if history_tokens(history) > HISTORY_TOKEN_BUDGET:
        history = bounded_history(history, HISTORY_TOKEN_BUDGET)
        await emit({"type": "compacted", "fallback": True, "summary": "历史超出上下文预算，暂时保留原始目标和近期消息；完整原文仍已保存。"})
    record_event("history.selected", "sheetmate.services.conversations.chat._history", {
        "summary_until": summary_until, "selected_count": len(history),
        "estimated_tokens": history_tokens(history),
        "limited": any(item.get("context_limited") for item in history),
    })
    # Reattach only recent, still-accessible images after text compaction. Never embed
    # base64 in summaries, persistence prompts, database messages, or diagnostic text.
    remaining = _MAX_HISTORY_IMAGES
    encoded_size = 0
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        assert conv is not None
        for item in reversed(history):
            message_id = item.get("message_id")
            if item["role"] != "user" or message_id is None or remaining <= 0:
                continue
            stored = db.get(Message, message_id)
            records = []
            for file_id in dict.fromkeys((stored.meta or {}).get("attachments", []) if stored else []):
                record = db.get(FileRecord, file_id)
                if record is None or not ((record.profile or {}).get("media_type") or "").startswith("image/"):
                    continue
                if (not Path(record.stored_path).is_file() or not file_service.can_access_in_conversation(
                    db, record, conversation_id=conv.id, customer_id=conv.customer_id,
                )):
                    continue
                records.append(record)
            if records:
                item["content"] += "\n（图片附件：" + "、".join(record.filename for record in records) + "）"
                chosen = records[-remaining:]
                try:
                    parts = _image_parts(chosen)
                except (OSError, ReadError, ServiceError):
                    # A missing/corrupted older attachment must not prevent a text reply.
                    item["content"] += "（原图片暂时无法读取）"
                else:
                    image_bytes = sum(len(part["image_url"]["url"]) for part in parts if part["type"] == "image_url")
                    if encoded_size + image_bytes <= _MAX_IMAGE_REQUEST_BYTES:
                        item["image_parts"] = parts
                        encoded_size += image_bytes
                        remaining -= len(chosen)
    return history


def _history_content(message: Message) -> str:
    content = message.content or ""
    if message.role != "assistant":
        return content
    digest = (message.meta or {}).get("digest")
    if digest:
        content += "\n" + digest + "（仅记录已执行操作，不代表用户目标已全部完成）"
    execution = (message.meta or {}).get("execution") or {}
    if execution.get("status") in {"incomplete", "failed", "cancelled", "interrupted", "waiting"}:
        content += f"\n（本轮状态：{execution['status']}；原因：{execution.get('summary') or execution.get('code') or '未知'}）"
    # 澄清卡的选项不在正文或工具摘要中；保留它们才能理解下一轮的数字回答。
    for event in message.events or []:
        if event.get("type") != "clarify":
            continue
        options = "\n".join(f"{index}. {option}" for index, option in enumerate(event.get("options") or [], 1))
        content += f"\n助手提问：{event.get('question', '')}" + (f"\n{options}" if options else "")
    return content


async def shutdown_background() -> None:
    """Let quick metadata work settle; cancellation still finalizes its usage."""
    tasks = list(_background)
    if not tasks:
        return
    _, pending = await asyncio.wait(tasks, timeout=5)
    for task in pending:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


async def _finish_background(llm: Any, user_id: int, conversation_id: str, customer_id: str | None,
                             message: str, answer: str, *, recipe_id: str | None = None,
                             source_message_id: int | None = None, source_turn_id: str | None = None,
                             assistant_message_id: int | None = None,
                             memory_candidate: bool | None = None,
                             additional_memory_sources: list[dict[str, Any]] | None = None) -> None:
    started_at = time.monotonic()
    work = [_maybe_title(conversation_id, llm, message, answer)]
    memory_sources = []
    if memory_candidate is not False:
        memory_sources.append((source_message_id, message))
    else:
        record_event("memory.skipped", "sheetmate.services.conversations.chat._finish_background",
                     {"reason": "intent_has_no_lasting_information", "source_message_id": source_message_id})
    # A steering message may add lasting rules after the initial intent call.
    # Keep its own provenance; that earlier false hint cannot suppress it.
    seen = {source_message_id}
    for source in additional_memory_sources or []:
        identifier, content = source.get("message_id"), source.get("content")
        if isinstance(identifier, int) and identifier not in seen and isinstance(content, str) and content.strip():
            memory_sources.append((identifier, content))
            seen.add(identifier)
    for identifier, content in memory_sources:
        work.append(_learn_memories(llm, user_id, conversation_id, customer_id, content, answer,
                    recipe_id=recipe_id, source_message_id=identifier, source_turn_id=source_turn_id,
                    assistant_message_id=assistant_message_id, persist_usage=False))
    try:
        results = await asyncio.gather(*work, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                record_event("chat.background.error", "sheetmate.services.conversations.chat._finish_background", error_data(result))
    finally:
        _persist_llm_usage(conversation_id, assistant_message_id, llm, pending=False)
        record_event("chat.background.done", "sheetmate.services.conversations.chat._finish_background",
                     {"elapsed_seconds": round(time.monotonic() - started_at, 3), "source_message_id": source_message_id})


async def _maybe_title(conversation_id: str, llm: Any, message: str, answer: str) -> str | None:
    if conversation_id in _titles_inflight:
        return None
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is None or conv.title not in {"新对话", ""}:
            return None
    _titles_inflight.add(conversation_id)
    started_at = time.monotonic()
    try:
        with trace_phase("background_title"):
            try:
                title = await asyncio.wait_for(make_title(llm, message, answer), timeout=60)
            except Exception:
                title = fallback_title(message)
            with session_scope() as db:
                result = db.execute(update(Conversation).where(
                    Conversation.id == conversation_id, Conversation.title.in_(["新对话", ""]),
                ).values(title=title))
                return title if result.rowcount == 1 else None
    finally:
        _titles_inflight.discard(conversation_id)
        record_event("chat.title.done", "sheetmate.services.conversations.chat._maybe_title",
                     {"elapsed_seconds": round(time.monotonic() - started_at, 3)})


async def _learn_memories(llm: Any, user_id: int, conversation_id: str, customer_id: str | None, message: str, answer: str,
                          *, recipe_id: str | None = None, source_message_id: int | None = None,
                          source_turn_id: str | None = None, assistant_message_id: int | None = None,
                          persist_usage: bool = True) -> None:
    with trace_phase("background_memory"):
        try:
            with session_scope() as db:
                user = db.get(User, user_id)
                if user is None:
                    return
                conversation = conv_service.get_conversation(db, user, conversation_id)
                if (conversation.customer_id != customer_id or conversation.recipe_id != recipe_id):
                    # A temporary chat may have just been saved to a customer.
                    # A previously bound task must not keep learning into its
                    # cached customer or workflow after a rebind or deletion.
                    if customer_id is None and recipe_id is None and conversation.customer_id:
                        customer_id, recipe_id = conversation.customer_id, conversation.recipe_id
                    else:
                        return
                existing = memory_service.memories_for(db, user_id=user_id, customer_id=customer_id,
                                                       conversation_id=conversation_id, recipe_id=recipe_id)
            # Candidate context is only a deduplication aid, never evidence for
            # creating new rules. Prioritize keys mentioned in this user turn.
            existing.sort(key=lambda item: not any(str(item.get("key") or "")[i:i + 2] in message
                                                   for i in range(max(0, len(str(item.get("key") or "")) - 1))))
            target_scope = {
                "scope": "customer" if customer_id else "conversation",
                "user_id": None,
                "customer_id": customer_id,
                "conversation_id": None if customer_id else conversation_id,
                "recipe_id": recipe_id,
            }
            items = await asyncio.wait_for(extract_memories(
                llm, message, answer, existing=existing[:12], target_scope=target_scope,
            ), timeout=120)
        except Exception as exc:
            record_event("memory.error", "sheetmate.services.conversations.chat._learn_memories", error_data(exc))
            return
        finally:
            if persist_usage:
                _persist_llm_usage(conversation_id, assistant_message_id, llm, pending=False)
    if not items:
        return
    with session_scope() as db:
        user = db.get(User, user_id)
        if user is None:
            return
        try:
            conversation = conv_service.get_conversation(db, user, conversation_id)
        except ServiceError:
            # A late model response must not restore deleted/revoked context.
            return
        # 临时对话可能在抽取期间已保存到客户；补上绑定时尚未来得及迁移的记忆。
        # 原本已绑定的任务在改绑或流程删除后停止学习，不写回过期范围。
        if customer_id is None and conversation.customer_id:
            customer_id, recipe_id = conversation.customer_id, conversation.recipe_id
        elif conversation.customer_id != customer_id or conversation.recipe_id != recipe_id:
            return
        if customer_id and db.get(Customer, customer_id) is None:
            return
        if recipe_id and db.scalar(select(RecipeRecord.id).where(
            RecipeRecord.recipe_id == recipe_id, RecipeRecord.customer_id == customer_id,
        ).limit(1)) is None:
            return
        receipts = []
        for item in items:
            memory = memory_service.set_memory(
                db, category=item["category"], key=item["key"], value=item["value"], user_id=user_id,
                customer_id=customer_id, conversation_id=conversation_id, recipe_id=recipe_id,
                source_message_id=source_message_id, source_turn_id=source_turn_id,
                evidence_span=item.get("evidence_span", ""),
                replaces_memory_id=item.get("replaces_memory_id"),
                replaces_value=item.get("replaces_value"),
            )
            receipts.append(memory_service.memory_write_result(memory))
        record_event("memory.saved", "sheetmate.services.memory.long_term.set_memory",
                     {"items": receipts, "customer_id": customer_id, "recipe_id": recipe_id,
                      "source_message_id": source_message_id, "source_turn_id": source_turn_id})


# ---------------------------------------------------------------------------
# 澄清
# ---------------------------------------------------------------------------


async def clarify(user: User, conversation_id: str, message: str, *, http_request: dict[str, Any] | None = None) -> dict[str, Any]:
    with session_scope() as db:
        conv_service.get_conversation(db, user, conversation_id)
    with bind_trace(new_turn(conversation_id)), trace_phase("clarification"):
        record_event("turn.start", "sheetmate.services.conversations.chat.clarify", {"user_id": user.id, "source": "clarification"})
        record_event("chat.input", "sheetmate.services.conversations.chat.clarify",
                     http_request if http_request is not None else {"body": {"message": message}})
        status = "failed"
        try:
            llm = get_llm()
            ctx, _ = build_context(user.id, conversation_id, llm)
            result = await check_clarification(llm, ctx, message)
            record_event("chat.output", "sheetmate.services.conversations.chat.clarify", result)
            status = "completed"
            return result
        except BaseException as exc:
            record_event("turn.error", "sheetmate.services.conversations.chat.clarify", error_data(exc))
            raise
        finally:
            record_event("turn.end", "sheetmate.services.conversations.chat.clarify", {"status": status})
