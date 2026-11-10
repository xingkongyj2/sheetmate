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
from contextlib import ExitStack, aclosing
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from sheetmate.agent.executor import AgentExecutor
from sheetmate.agent.intent import infer_request
from sheetmate.agent.llm import (
    LLMError,
    LLMNotConfigured,
    contains_llm_not_configured_text,
    get_llm,
    is_llm_not_configured_message,
)
from sheetmate.agent.memory import extract_memories
from sheetmate.agent.multi_agent import execute_multi, should_activate
from sheetmate.agent.planner import check_clarification
from sheetmate.agent.persistence import infer_recipe_persistence
from sheetmate.agent.recorder import WorkflowRecorder
from sheetmate.agent.summarizer import (
    HISTORY_TOKEN_BUDGET, RECENT_HISTORY_TOKENS, bounded_history, compact_history,
    fallback_title, history_tokens, make_title,
)
from sheetmate.agent.thinking import THINKING_TOOL_BUDGET, execute_thinking
from sheetmate.agent.tools import AgentContext, ToolRegistry
from sheetmate.agent.task_state import advance_task
from sheetmate.config import settings
from sheetmate.conversation_log import (
    bind_trace, current_trace, error_data, new_turn, record_event, trace_phase,
)
from sheetmate.db.database import session_scope
from sheetmate.db.models import Conversation, Customer, FileRecord, Message, RecipeProfile, RecipeRecord, User
from sheetmate.engine.images import model_image_data_url
from sheetmate.engine.reader import ReadError
from sheetmate.engine.script_references import bind_script_references, externalize_script_references
from sheetmate.services import conversations as conv_service
from sheetmate.services import customers as customer_service
from sheetmate.services import files as file_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services import recipe_deletion
from sheetmate.services import runs as run_service
from sheetmate.services.delivery import select_delivery
from sheetmate.services.file_view import file_references, file_view_answer, select_files
from sheetmate.services.analysis import AnalysisWorkspace, analysis_workspace
from sheetmate.services.errors import Conflict, ServiceError
from sheetmate.services.host import ConversationHost
from sheetmate.services.table_memory import file_memories_text, restore_workspace_memories
from sheetmate.services.jobs import Job, jobs

_registry = ToolRegistry()
_background: set[asyncio.Task] = set()  # 持有后台任务的引用，防止被垃圾回收
_MAX_MESSAGE_IMAGES = 10
_MAX_HISTORY_IMAGES = 8
_MAX_IMAGE_REQUEST_BYTES = 20 * 1024 * 1024
_KEEP_EVENTS = {
    "model_call_start", "model_call_done", "completion_check_start", "completion_check", "execution_stop", "execution_status",
    "note", "reply", "tool_call", "tool_result", "table", "file", "delivery_manifest", "script", "clarify", "memory_saved", "field_mapping_saved", "plan_start", "step_start", "step_done",
    "reflect_start", "reflect_done", "think_start", "plan_review", "step_check", "audit_start", "audit_done", "agent_pool_start", "agent_start", "agent_done", "aggregate_start", "error", "compacted",
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
        conv = db.get(Conversation, conversation_id)
        assert conv is not None
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
               thinking: bool = False, http_request: dict[str, Any] | None = None) -> Job:
    # Authorise before creating a log; untrusted ids must not create arbitrary files.
    with session_scope() as db:
        conv_service.get_conversation(db, user, conversation_id)
    trace = new_turn(conversation_id)
    with bind_trace(trace):
        record_event("turn.start", "sheetmate.services.chat.start_chat", {"user_id": user.id, "source": "chat"})
        record_event("chat.input", "sheetmate.services.chat.start_chat",
                     http_request if http_request is not None else {
                         "body": {"message": message, "plan_steps": plan_steps, "attachments": attachments, "thinking": thinking}})
        try:
            return _start_chat(user, conversation_id, message, plan_steps=plan_steps,
                               attachments=attachments, thinking=thinking)
        except BaseException as exc:
            record_event("turn.error", "sheetmate.services.chat.start_chat", error_data(exc))
            record_event("turn.end", "sheetmate.services.chat.start_chat", {"status": "rejected"})
            raise


def _start_chat(user: User, conversation_id: str, message: str, *,
                plan_steps: list[dict[str, Any]] | None = None, attachments: list[str] | None = None,
                thinking: bool = False) -> Job:
    text = (message or "").strip()
    if not text:
        raise ServiceError("消息不能为空")
    with session_scope() as db:
        conv = conv_service.get_conversation(db, user, conversation_id)
        if jobs.running(conversation_id):
            raise Conflict("上一个任务还在处理中，请稍等它完成")
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
        message_id = conv_service.add_message(db, conv, "user", text, meta=meta).id
    user_id = user.id

    async def work(job: Job) -> None:
        await _run_chat(job, user_id, conversation_id, text, plan_steps, message_id, thinking)

    return jobs.start(conversation_id, work, message_id=message_id)


async def _run_chat(job: Job, user_id: int, conversation_id: str, message: str, plan_steps: list[dict[str, Any]] | None,
                    message_id: int, thinking: bool = False) -> None:
    recorder = WorkflowRecorder(message)
    recorder.request_message_id = message_id
    recorder.job_id = job.id
    stored: list[dict[str, Any]] = []
    text_parts: list[str] = []
    reply_parts: list[str] = []
    candidate_parts: list[str] = []
    candidate_final = ""
    final_text = ""
    delivery_candidates: dict[str, dict[str, Any]] = {}
    delivery_finished = False
    replace_file_ids: list[str] = []
    analysis: AnalysisWorkspace | None = None
    run_id = None
    llm = None
    last_checkpoint = 0.0

    def checkpoint(*, force: bool = False) -> None:
        nonlocal last_checkpoint
        if force or time.monotonic() - last_checkpoint >= 1:
            _save_assistant(conversation_id, "".join(text_parts).strip() or final_text, stored, recorder, run_id, finalize=False)
            last_checkpoint = time.monotonic()

    async def finish_execution(content: str) -> None:
        recorder.finish(content)
        snapshot = getattr(llm, "usage_snapshot", None)
        if callable(snapshot):
            recorder.llm_usage = snapshot()
        await emit({"type": "execution_status", **recorder.execution()})

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
        nonlocal final_text
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
                selection = await select_delivery(llm, request=message, answer=answer,
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
        if status == "unverified":
            recorder.record_stop({"status": "incomplete", "code": "delivery_unverified", "summary": summary,
                                  "scope": "task", "verified": False})
        await emit({"type": "delivery_manifest", "candidate_file_ids": list(delivery_candidates),
                    "final_file_ids": finals if confirmed else [], "status": status, "summary": summary,
                    "replaced_file_ids": replaced, "files": final_files})
        delivery_finished = True
        if status == "unverified":
            return "已生成文件，但最终交付版本尚未确认，本轮未交付正式结果。" + summary
        if status == "incomplete":
            return summary
        return ""

    try:
        # 用户已看到具体目标并确认后直接执行，不再通过模型判断第二次授权。
        with session_scope() as db:
            user = db.get(User, user_id)
            assert user is not None
            confirmed_ids = recipe_deletion.resolve_confirmation(db, user, conversation_id, message)
            if confirmed_ids is not None:
                conv = db.get(Conversation, conversation_id)
                assert conv is not None
                conv.mode = "analyze"
        if confirmed_ids is not None:
            ctx, host = build_context(user_id, conversation_id, None)
            await job.emit({"type": "start", "mode": "analyze", "customer": ctx.customer["name"] if ctx.customer else None})
            deleted: list[dict[str, Any]] = []
            if confirmed_ids:
                host.delete_recipe_ids = set(confirmed_ids)
                call_id = "confirmed_recipe_deletion"
                await emit({"type": "tool_call", "id": call_id, "tool": "delete_recipes",
                            "label": "删除已确认的流程", "params": {"recipe_ids": sorted(confirmed_ids)}})
                record_event("tool.input", "sheetmate.services.host.ConversationHost.delete_recipes_confirmed",
                             {"recipe_ids": sorted(confirmed_ids)}, call_id=call_id, tool="delete_recipes")
                try:
                    deleted = host.delete_recipes_confirmed(sorted(confirmed_ids))
                except BaseException as exc:
                    record_event("tool.error", "sheetmate.services.host.ConversationHost.delete_recipes_confirmed",
                                 error_data(exc), call_id=call_id, tool="delete_recipes", status="error")
                    raise
                record_event("tool.result", "sheetmate.services.host.ConversationHost.delete_recipes_confirmed",
                             {"deleted": deleted}, call_id=call_id, tool="delete_recipes", status="ok")
                for item in deleted:
                    await emit({"type": "recipe_deleted", **item, "tool_call_id": call_id})
                answer = f"已删除 {len(deleted)} 个流程。"
                await emit({"type": "tool_result", "id": call_id, "tool": "delete_recipes", "ok": True,
                            "summary": answer, "text": answer})
            else:
                answer = "本次没有删除流程。"
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
        host.request_message_id = message_id
        trace = current_trace()
        host.request_turn_id = trace.turn_id if trace is not None else None
        with session_scope() as db:
            conv = db.get(Conversation, conversation_id)
            request = db.get(Message, message_id)
            assert conv is not None and request is not None
            records = _attachment_records(db, conv, (request.meta or {}).get("attachments", []))
            ctx.image_parts = _image_parts(records)
        history = await _history(conversation_id, llm, emit, current_message_id=message_id)
        _limit_history_images(history, ctx.image_parts)
        files = host.list_files()
        references = file_references(conversation_id, message_id, files)
        intent = await infer_request(llm, ctx, message, history, plan_steps, files=files, file_references=references)
        record_event("chat.intent.result", "sheetmate.agent.intent.infer_request", intent)
        ctx.mode = intent.mode
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
            answer = await file_view_answer(llm, message, intent.files, selected_files)
            if answer:
                await emit({"type": "text_chunk", "content": answer})
            await emit({"type": "final_text", "content": answer})
            await finish_execution(answer)
            _save_assistant(conversation_id, answer, stored, recorder, None)
            await job.emit({"type": "done"})
            return
        if intent.action == "process":
            ctx.save_recipe_requested = (intent.save_recipe if intent.save_recipe is not None
                                         else await infer_recipe_persistence(llm, message, history))
            host.save_recipe_requested = ctx.save_recipe_requested
            ctx.task_state = _advance_conversation_task(
                conversation_id, message_id, relation=intent.task_relation,
                save_recipe=ctx.save_recipe_requested,
            )
    except asyncio.CancelledError:
        cancel_execution()
        await finish_execution("")
        _save_assistant(conversation_id, "（已停止，尚未处理文件）", stored, recorder, None)
        raise
    except Exception as exc:
        record_event("chat.error", "sheetmate.services.chat._run_chat.context", error_data(exc))
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
        with ExitStack() as resources:
            if intent.action == "process":
                analysis = resources.enter_context(analysis_workspace(ctx.ws))
                ctx.ws = host.ws = analysis.ws
                host.analysis = analysis
                for table in ctx.ws.list_tables():
                    if table.file_id:
                        host.file_path(table.file_id)
                if ctx.mode == "analyze":
                    with session_scope() as db:
                        run_id = run_service.create_run(db, kind="analysis", user_id=user_id, customer_id=ctx.host.customer_id,
                                                        conversation_id=conversation_id, request_text=message).id
            await job.emit({"type": "start", "mode": ctx.mode, "customer": ctx.customer["name"] if ctx.customer else None})
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
        record_event("chat.error", "sheetmate.services.chat._run_chat.execute", error_data(exc))
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
        record_event("chat.error", "sheetmate.services.chat._run_chat.finalize_deliveries", error_data(exc))
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
    await finish_execution(answer)
    _save_assistant(conversation_id, answer, stored, recorder, run_id)
    title = await _maybe_title(conversation_id, llm, message, answer)
    _persist_llm_usage(conversation_id, recorder.message_id, llm)
    if title:
        await job.emit({"type": "title", "title": title})
    if host.saved_recipes or host.deleted_recipes:
        await job.emit({"type": "recipes_changed", "recipes": host.saved_recipes,
                        "deleted_recipe_ids": [item["recipe_id"] for item in host.deleted_recipes]})
        customer_service.schedule_summary_refresh(ctx.host.customer_id)  # 流程变了：后台更新客户的整体处理逻辑
    await job.emit({"type": "done"})
    # 普通聊天也可能提供客户背景；与处理任务一样提取用户明确提供的长期信息。
    # 抽取放到独立的后台任务里，不占用这次请求的连接。
    trace = current_trace()
    task = asyncio.create_task(_learn_memories(
        llm, user_id, conversation_id, ctx.host.customer_id, message, answer,
        recipe_id=ctx.host.recipe_id, source_message_id=message_id,
        source_turn_id=trace.turn_id if trace is not None else None,
        assistant_message_id=recorder.message_id,
    ))
    _background.add(task)
    task.add_done_callback(_background.discard)


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
    usage = getattr(recorder, "llm_usage", None)
    if usage:
        record["execution"]["llm_usage"] = usage
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            return
        meta = {"mode": conv.mode, "digest": recorder.digest(), "tool_sequence": record["tool_sequence"], "run_id": run_id,
                "execution": record["execution"], "request_message_id": recorder.request_message_id, "job_id": recorder.job_id}
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
        if state and state.get("current_message_id") == recorder.request_message_id:
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
            record_event("chat.output", "sheetmate.services.chat._save_assistant",
                         {"message_id": assistant.id, "content": content,
                          "execution": {key: record["execution"][key] for key in (
                              "status", "code", "summary", "source") if key in record["execution"]},
                          "artifacts": [event for event in events if event.get("type") in {
                              "file", "table", "delivery_manifest", "clarify", "recipe_deleted"}]})


def _contains_unconfigured_llm_error(text: str) -> bool:
    return contains_llm_not_configured_text(text)


def _persist_llm_usage(conversation_id: str, message_id: int | None, llm: Any) -> None:
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
        message.meta = {**meta, "execution": execution}
        run = db.get(run_service.Run, meta["run_id"]) if meta.get("run_id") else None
        if run is not None:
            run.validation = {**(run.validation or {}), "execution": execution}


def recover_interrupted_executions() -> None:
    """A persisted running checkpoint survived its process; the exact interruption cause is unknown."""
    reason = "服务已重启，上次执行未完成；停止的具体原因未知。"
    with session_scope() as db:
        for assistant in db.scalars(select(Message).where(
            Message.role == "assistant", Message.meta["execution"]["status"].as_string() == "running",
        )):
            meta = dict(assistant.meta or {})
            execution = dict(meta.get("execution") or {})
            if execution.get("status") != "running":
                continue
            execution.update(status="interrupted", code="process_restart", summary=reason, source="unknown")
            status = {"type": "execution_status", **execution}
            assistant.meta = {**meta, "execution": execution}
            assistant.events = [*(assistant.events or []), status]
            conv = db.get(Conversation, assistant.conversation_id)
            if conv is not None and (conv.task_state or {}).get("current_message_id") == meta.get("request_message_id"):
                conv.task_state = {**conv.task_state, "status": "interrupted", "unresolved": [reason]}
            if not assistant.content:
                assistant.content = reason
            if meta.get("log_turn_id"):
                trace = new_turn(assistant.conversation_id, turn_id=meta["log_turn_id"])
                trace.record("chat.output", "sheetmate.services.chat.recover_interrupted_executions",
                             {"message_id": assistant.id, "content": assistant.content})
                trace.record("turn.recovered", "sheetmate.services.chat.recover_interrupted_executions",
                             {"code": "process_restart", "source": "unknown"})
                trace.record("turn.end", "sheetmate.services.chat.recover_interrupted_executions",
                             {"status": "interrupted", "code": "process_restart", "summary": reason})
        # Include scripts/recipes whose process stopped between writing a run and linking its message.
        for run in db.scalars(select(run_service.Run).where(run_service.Run.status == "running")):
            execution = dict((run.validation or {}).get("execution") or {})
            execution.update(status="interrupted", code="process_restart", summary=reason, source="unknown")
            run.status = "interrupted"
            run.error = reason
            run.validation = {**(run.validation or {}), "execution": execution}
            run.finished_at = run_service.now()


def persist_unfinished_job(job: Job, *, status: str, code: str, summary: str) -> dict[str, Any] | None:
    """Last-resort persistence when orchestration fails outside its normal finalization path."""
    with session_scope() as db:
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
        event = {"type": "execution_status", **execution}
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
        record_event("chat.output", "sheetmate.services.chat.persist_unfinished_job",
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


async def _history(conversation_id: str, llm: Any, emit: Any, *, current_message_id: int | None = None) -> list[dict[str, Any]]:
    """组装历史：压缩摘要 + 之后的消息（不含刚保存的这条用户消息）。助手消息附上本轮执行摘要，方便追问。"""
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        assert conv is not None
        summary = conv.context_summary
        summary_until = conv.summary_until
        if _contains_unconfigured_llm_error(summary):
            # Old summaries may have absorbed a setup failure. Rebuild them from messages,
            # where the obsolete assistant error can be identified without dropping user text.
            summary = ""
            summary_until = 0
            conv.context_summary = ""
            conv.summary_until = 0
        query = select(Message).where(Message.conversation_id == conversation_id, Message.id > summary_until)
        if current_message_id is not None:
            query = query.where(Message.id < current_message_id)
        rows = list(db.scalars(query.order_by(Message.id)))
        if current_message_id is None:
            rows = rows[:-1]
    items = [{"id": m.id, "role": m.role, "content": _history_content(m)}
        for m in rows if not is_llm_not_configured_message(m.role, m.content, m.events)]
    summary_item = [{"role": "assistant", "content": summary, "context_summary": True}] if summary else []
    if history_tokens([*summary_item, *items]) > HISTORY_TOKEN_BUDGET:
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
        older, recent = items[:cut], items[cut:]
        text = await compact_history(llm, ([{"role": "assistant", "content": summary}] if summary else []) + older)
        if text and older:
            with session_scope() as db:
                # A slow summary from an older request must not move a newer
                # request's persisted cursor backwards or replace its summary.
                db.execute(update(Conversation).where(
                    Conversation.id == conversation_id, Conversation.summary_until == summary_until,
                ).values(context_summary=text, summary_until=older[-1]["id"]))
            summary = text
            items = recent
            await emit({"type": "compacted", "old_count": len(older), "summary": text[:120]})
    history = [{"role": "assistant", "content": summary, "context_summary": True}] if summary else []
    history += [{"role": m["role"], "content": m["content"], "message_id": m["id"]} for m in items if m["content"].strip()]
    if history_tokens(history) > HISTORY_TOKEN_BUDGET:
        history = bounded_history(history, HISTORY_TOKEN_BUDGET)
        await emit({"type": "compacted", "fallback": True, "summary": "历史超出上下文预算，暂时保留原始目标和近期消息；完整原文仍已保存。"})
    # Reattach only recent, still-accessible images after text compaction. Never embed
    # base64 in summaries, persistence prompts, database messages, or diagnostic text.
    remaining = _MAX_HISTORY_IMAGES
    encoded_size = 0
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        assert conv is not None
        for item in reversed(history):
            message_id = item.pop("message_id", None)
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


async def _maybe_title(conversation_id: str, llm: Any, message: str, answer: str) -> str | None:
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is None or conv.title not in {"新对话", ""}:
            return None
    try:
        title = await asyncio.wait_for(make_title(llm, message, answer), timeout=15)
    except (asyncio.TimeoutError, Exception):
        title = fallback_title(message)
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is not None:
            conv.title = title
    return title


async def _learn_memories(llm: Any, user_id: int, conversation_id: str, customer_id: str | None, message: str, answer: str,
                          *, recipe_id: str | None = None, source_message_id: int | None = None,
                          source_turn_id: str | None = None, assistant_message_id: int | None = None) -> None:
    with trace_phase("background_memory"):
        try:
            with session_scope() as db:
                existing = memory_service.memories_for(db, user_id=user_id, customer_id=customer_id,
                                                       conversation_id=conversation_id, recipe_id=recipe_id)
            # Candidate context is only a deduplication aid, never evidence for
            # creating new rules. Prioritize keys mentioned in this user turn.
            existing.sort(key=lambda item: not any(str(item.get("key") or "")[i:i + 2] in message
                                                   for i in range(max(0, len(str(item.get("key") or "")) - 1))))
            items = await asyncio.wait_for(extract_memories(llm, message, answer, existing=existing[:12]), timeout=30)
        except Exception as exc:
            record_event("memory.error", "sheetmate.services.chat._learn_memories", error_data(exc))
            return
        finally:
            _persist_llm_usage(conversation_id, assistant_message_id, llm)
    if not items:
        return
    with session_scope() as db:
        conversation = db.get(Conversation, conversation_id)
        if conversation is None:
            return
        # 临时对话可能在抽取期间已保存到客户；补上绑定时尚未来得及迁移的记忆。
        # 原本已绑定的任务仍归原客户和流程，避免改绑期间串入其他客户。
        if customer_id is None and conversation.customer_id:
            customer_id, recipe_id = conversation.customer_id, conversation.recipe_id
        if customer_id and db.get(Customer, customer_id) is None:
            return
        receipts = []
        for item in items:
            memory = memory_service.set_memory(
                db, category=item["category"], key=item["key"], value=item["value"], user_id=user_id,
                customer_id=customer_id, conversation_id=conversation_id, recipe_id=recipe_id,
                source_message_id=source_message_id, source_turn_id=source_turn_id,
                evidence_span=item.get("evidence_span", ""),
            )
            receipts.append(memory_service.memory_write_result(memory))
        record_event("memory.saved", "sheetmate.services.memory.set_memory",
                     {"items": receipts, "customer_id": customer_id, "recipe_id": recipe_id,
                      "source_message_id": source_message_id, "source_turn_id": source_turn_id})


# ---------------------------------------------------------------------------
# 澄清
# ---------------------------------------------------------------------------


async def clarify(user: User, conversation_id: str, message: str, *, http_request: dict[str, Any] | None = None) -> dict[str, Any]:
    with session_scope() as db:
        conv_service.get_conversation(db, user, conversation_id)
    with bind_trace(new_turn(conversation_id)), trace_phase("clarification"):
        record_event("turn.start", "sheetmate.services.chat.clarify", {"user_id": user.id, "source": "clarification"})
        record_event("chat.input", "sheetmate.services.chat.clarify",
                     http_request if http_request is not None else {"body": {"message": message}})
        status = "failed"
        try:
            llm = get_llm()
            ctx, _ = build_context(user.id, conversation_id, llm)
            result = await check_clarification(llm, ctx, message)
            record_event("chat.output", "sheetmate.services.chat.clarify", result)
            status = "completed"
            return result
        except BaseException as exc:
            record_event("turn.error", "sheetmate.services.chat.clarify", error_data(exc))
            raise
        finally:
            record_event("turn.end", "sheetmate.services.chat.clarify", {"status": status})
