"""对话编排：一条用户消息从进来到回答完成的全过程（对应 TabClaw app.py 里 /api/chat 的逻辑）。

1. 保存用户消息；历史太长先压缩（auto-compact）
2. 组装上下文：模式（分析/修改）、客户（记忆、流程、整体处理逻辑）、工作区里的表、公式库
3. 选择执行方式：按计划逐步执行 / 多表并行 / 直接 ReAct
4. 事件实时推给前端（文本、工具调用、结果表、Recipe 步骤、文件…），同时精简后存进助手消息，刷新页面能还原执行明细
5. 结束后：分析模式记一条运行记录（工作流）、首轮生成标题、后台抽取记忆
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, aclosing
from typing import Any

from sqlalchemy import select

from sheetmate.agent.executor import AgentExecutor
from sheetmate.agent.intent import infer_mode
from sheetmate.agent.llm import LLMError, get_llm
from sheetmate.agent.memory import extract_memories
from sheetmate.agent.multi_agent import execute_multi, should_activate
from sheetmate.agent.planner import check_clarification, generate_plan
from sheetmate.agent.recorder import WorkflowRecorder
from sheetmate.agent.summarizer import COMPACT_THRESHOLD, compact_history, fallback_title, make_title
from sheetmate.agent.tools import AgentContext, ToolRegistry
from sheetmate.config import settings
from sheetmate.db.database import session_scope
from sheetmate.db.models import Conversation, Customer, Message, User
from sheetmate.services import conversations as conv_service
from sheetmate.services import customers as customer_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services import runs as run_service
from sheetmate.services.analysis import AnalysisWorkspace, analysis_workspace
from sheetmate.services.errors import Conflict
from sheetmate.services.host import ConversationHost
from sheetmate.services.jobs import Job, jobs

_registry = ToolRegistry()
_background: set[asyncio.Task] = set()  # 持有后台任务的引用，防止被垃圾回收
_KEEP_EVENTS = {
    "note", "tool_call", "tool_result", "table", "file", "clarify", "memory_saved", "field_mapping_saved", "plan_start", "step_start", "step_done",
    "reflect_start", "reflect_done", "agent_pool_start", "agent_start", "agent_done", "aggregate_start", "error", "compacted",
    "recipe_learn_start", "recipe_evidence", "recipe_generated", "recipe_run_start", "recipe_input", "recipe_step_start",
    "recipe_step_done", "recipe_step_error", "recipe_repair", "recipe_output", "recipe_validation", "recipe_run_done",
    "recipe_verify", "recipe_refine", "recipe_diff", "recipe_saved", "recipe_failed",
}


def build_context(user_id: int, conversation_id: str, llm: Any) -> tuple[AgentContext, ConversationHost]:
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        assert conv is not None
        customer = db.get(Customer, conv.customer_id) if conv.customer_id else None
        library = recipe_service.library_for(db, conv.customer_id)
        mode = conv.mode
        customer_info = {"id": customer.id, "name": customer.name, "summary": customer.summary} if customer else None
    ws = conv_service.workspace(conversation_id)
    host = ConversationHost(user_id=user_id, conversation_id=conversation_id,
                            customer_id=customer_info["id"] if customer_info else None, ws=ws)
    ctx = AgentContext(mode=mode, ws=ws, library=library, host=host, llm=llm, conversation_id=conversation_id,
                       customer=customer_info, max_repair=settings.repair_max_attempts, verify_rounds=settings.verify_max_rounds)
    return ctx, host


def start_chat(user: User, conversation_id: str, message: str, *,
               plan_steps: list[dict[str, Any]] | None = None, attachments: list[str] | None = None) -> Job:
    text = (message or "").strip()
    if not text:
        from sheetmate.services.errors import ServiceError

        raise ServiceError("消息不能为空")
    with session_scope() as db:
        conv = conv_service.get_conversation(db, user, conversation_id)
        if jobs.running(conversation_id):
            raise Conflict("上一个任务还在处理中，请稍等它完成")
        meta = {"attachments": attachments or []}
        if plan_steps:
            meta["plan"] = plan_steps
        message_id = conv_service.add_message(db, conv, "user", text, meta=meta).id
    user_id = user.id

    async def work(job: Job) -> None:
        await _run_chat(job, user_id, conversation_id, text, plan_steps, message_id)

    return jobs.start(conversation_id, work)


async def _run_chat(job: Job, user_id: int, conversation_id: str, message: str, plan_steps: list[dict[str, Any]] | None,
                    message_id: int) -> None:
    recorder = WorkflowRecorder(message)
    stored: list[dict[str, Any]] = []
    text_parts: list[str] = []
    final_text = ""
    analysis: AnalysisWorkspace | None = None

    async def emit(event: dict[str, Any]) -> None:
        nonlocal final_text
        recorder.observe(event)
        if event["type"] == "text_chunk":
            text_parts.append(event["content"])
        elif event["type"] == "note":
            text_parts.clear()
            final_text = ""
            stored.append(_slim(event))
        elif event["type"] == "final_text":
            final_text = event.get("content", "")
            return  # 文本已经以 text_chunk 推送过
        elif event["type"] in _KEEP_EVENTS:
            stored.append(_slim(event))
        if analysis is not None and event["type"] == "table":
            name = event.get("table", {}).get("name")
            if name and analysis.ws.exists(name):
                analysis.publish(name)
        await job.emit(event)

    try:
        llm = get_llm()
        ctx, host = build_context(user_id, conversation_id, llm)
        history = await _history(conversation_id, llm, emit)
        ctx.mode = await infer_mode(llm, ctx, message, history, plan_steps)
    except asyncio.CancelledError:
        recorder.error = "用户停止了生成"
        _save_assistant(conversation_id, "（已停止，尚未处理文件）", stored, recorder, None)
        raise
    except Exception as exc:
        await emit({"type": "error", "content": str(exc)})
        _save_assistant(conversation_id, f"⚠️ {exc}", stored, recorder, None)
        await job.emit({"type": "done"})
        return

    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is not None:
            conv.mode = ctx.mode
        request = db.get(Message, message_id)
        if request is not None:
            request.meta = {**(request.meta or {}), "mode": ctx.mode}
    executor = AgentExecutor(llm, _registry, max_iterations=settings.agent_max_iterations)
    run_id = None
    try:
        with ExitStack() as resources:
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
            if plan_steps:
                stream = executor.execute_plan(ctx, message, plan_steps, history)
            elif should_activate(ctx, message):
                stream = execute_multi(executor, ctx, message, history)
            else:
                stream = executor.execute(ctx, message, history)
            async with aclosing(stream):
                async for event in stream:
                    await emit(event)
    except asyncio.CancelledError:
        recorder.error = "用户停止了生成"
        _save_assistant(conversation_id, ("".join(text_parts) + "\n\n（已停止）").strip(), stored, recorder, run_id)
        raise
    except Exception as exc:
        await emit({"type": "error", "content": f"处理出错：{exc}"})

    answer = final_text or "".join(text_parts).strip()
    if not answer and recorder.error:
        answer = f"⚠️ {recorder.error}"
    _save_assistant(conversation_id, answer, stored, recorder, run_id)
    title = await _maybe_title(conversation_id, llm, message, answer)
    if title:
        await job.emit({"type": "title", "title": title})
    if host.saved_recipes:
        await job.emit({"type": "recipes_changed", "recipes": host.saved_recipes})
        customer_service.schedule_summary_refresh(ctx.host.customer_id)  # 流程变了：后台更新客户的整体处理逻辑
    await job.emit({"type": "done"})
    # 记忆抽取放到独立的后台任务里，不占用这次请求的连接
    task = asyncio.create_task(_learn_memories(llm, user_id, conversation_id, ctx.host.customer_id, message, answer))
    _background.add(task)
    task.add_done_callback(_background.discard)


def _slim(event: dict[str, Any]) -> dict[str, Any]:
    """存库前精简：表格预览只留前 20 行，去掉大段文本。"""
    item = dict(event)
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
    return item


def _save_assistant(conversation_id: str, content: str, events: list[dict[str, Any]], recorder: WorkflowRecorder,
                    run_id: str | None) -> None:
    record = recorder.to_dict()
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        if conv is None:
            return
        conv_service.add_message(db, conv, "assistant", content, events=events,
                                 meta={"mode": conv.mode, "digest": recorder.digest(), "tool_sequence": record["tool_sequence"], "run_id": run_id})
        if run_id:
            if record["steps"]:
                run_service.finish_analysis_run(db, run_id, record)
            else:
                run = db.get(run_service.Run, run_id)
                if run is not None:
                    db.delete(run)


async def _history(conversation_id: str, llm: Any, emit: Any) -> list[dict[str, Any]]:
    """组装历史：压缩摘要 + 之后的消息（不含刚保存的这条用户消息）。助手消息附上本轮执行摘要，方便追问。"""
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        assert conv is not None
        rows = list(db.scalars(select(Message).where(Message.conversation_id == conversation_id, Message.id > conv.summary_until)
                               .order_by(Message.id)))[:-1]
        summary = conv.context_summary
    items = [{"id": m.id, "role": m.role, "content": (m.content or "") + (
        "\n" + (m.meta or {}).get("digest", "") if m.role == "assistant" and (m.meta or {}).get("digest") else "")} for m in rows]
    if len(items) > COMPACT_THRESHOLD:
        older, recent = items[:-8], items[-8:]
        text = await compact_history(llm, ([{"role": "assistant", "content": summary}] if summary else []) + older)
        if text:
            with session_scope() as db:
                conv = db.get(Conversation, conversation_id)
                conv.context_summary = text
                conv.summary_until = older[-1]["id"]
            summary = text
            items = recent
            await emit({"type": "compacted", "old_count": len(older), "summary": text[:120]})
    history = [{"role": "assistant", "content": summary}] if summary else []
    history += [{"role": m["role"], "content": m["content"]} for m in items if m["content"].strip()]
    return history


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


async def _learn_memories(llm: Any, user_id: int, conversation_id: str, customer_id: str | None, message: str, answer: str) -> None:
    try:
        items = await asyncio.wait_for(extract_memories(llm, message, answer), timeout=30)
    except (asyncio.TimeoutError, Exception):
        return
    if not items:
        return
    with session_scope() as db:
        for item in items:
            memory_service.set_memory(db, category=item["category"], key=item["key"], value=item["value"], user_id=user_id,
                                      customer_id=customer_id, conversation_id=conversation_id)


# ---------------------------------------------------------------------------
# 计划 / 澄清
# ---------------------------------------------------------------------------


async def plan(user: User, conversation_id: str, message: str) -> dict[str, Any]:
    with session_scope() as db:
        conv_service.get_conversation(db, user, conversation_id)
    llm = get_llm()
    ctx, _ = build_context(user.id, conversation_id, llm)
    return await generate_plan(llm, ctx, message)


async def clarify(user: User, conversation_id: str, message: str) -> dict[str, Any]:
    with session_scope() as db:
        conv_service.get_conversation(db, user, conversation_id)
    llm = get_llm()
    ctx, _ = build_context(user.id, conversation_id, llm)
    return await check_clarification(llm, ctx, message)
