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
from sheetmate.agent.intent import infer_request
from sheetmate.agent.llm import LLMError, get_llm
from sheetmate.agent.memory import extract_memories
from sheetmate.agent.multi_agent import execute_multi, should_activate
from sheetmate.agent.planner import check_clarification, generate_plan
from sheetmate.agent.persistence import infer_recipe_persistence
from sheetmate.agent.recorder import WorkflowRecorder
from sheetmate.agent.summarizer import COMPACT_THRESHOLD, compact_history, fallback_title, make_title
from sheetmate.agent.tools import AgentContext, ToolRegistry
from sheetmate.config import settings
from sheetmate.db.database import session_scope
from sheetmate.db.models import Conversation, Customer, Message, User
from sheetmate.engine.script_references import bind_script_references, externalize_script_references
from sheetmate.services import conversations as conv_service
from sheetmate.services import customers as customer_service
from sheetmate.services import files as file_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services import recipe_deletion
from sheetmate.services import runs as run_service
from sheetmate.services.file_view import file_references, file_view_answer, select_files
from sheetmate.services.analysis import AnalysisWorkspace, analysis_workspace
from sheetmate.services.errors import Conflict
from sheetmate.services.host import ConversationHost
from sheetmate.services.jobs import Job, jobs

_registry = ToolRegistry()
_background: set[asyncio.Task] = set()  # 持有后台任务的引用，防止被垃圾回收
_KEEP_EVENTS = {
    "note", "tool_call", "tool_result", "table", "file", "script", "clarify", "memory_saved", "field_mapping_saved", "plan_start", "step_start", "step_done",
    "reflect_start", "reflect_done", "agent_pool_start", "agent_start", "agent_done", "aggregate_start", "error", "compacted",
    "recipe_learn_start", "recipe_evidence", "recipe_generated", "recipe_run_start", "recipe_input", "recipe_step_start",
    "recipe_step_done", "recipe_step_error", "recipe_repair", "recipe_output", "recipe_validation", "recipe_run_done",
    "recipe_verify", "recipe_refine", "recipe_diff", "recipe_completed", "recipe_saved", "recipe_deleted", "recipe_failed", "recipe_view",
}


def build_context(user_id: int, conversation_id: str, llm: Any) -> tuple[AgentContext, ConversationHost]:
    with session_scope() as db:
        conv = db.get(Conversation, conversation_id)
        assert conv is not None
        customer = db.get(Customer, conv.customer_id) if conv.customer_id else None
        library = recipe_service.library_for(db, conv.customer_id)
        mode = conv.mode
        customer_info = {"id": customer.id, "name": customer.name, "summary": customer.summary} if customer else None
        available = {record.id for record in file_service.list_files(
            db, customer_id=conv.customer_id, conversation_id=conv.id,
        )}
    ws = conv_service.workspace(conversation_id)
    # 旧版本删除了原件后可能留下输入表；不能再把它们当作可用原件交给流程。
    for table in ws.list_tables():
        if table.source == "uploaded" and table.file_id and table.file_id not in available:
            ws.drop(table.name)
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
        event = externalize_script_references(event)
        bind_script_references(event, user_id=user_id, conversation_id=conversation_id)
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
                deleted = host.delete_recipes_confirmed(sorted(confirmed_ids))
                for item in deleted:
                    await emit({"type": "recipe_deleted", **item, "tool_call_id": call_id})
                answer = f"已删除 {len(deleted)} 个流程。"
                await emit({"type": "tool_result", "id": call_id, "tool": "delete_recipes", "ok": True,
                            "summary": answer, "text": answer})
            else:
                answer = "本次没有删除流程。"
            await emit({"type": "text_chunk", "content": answer})
            await emit({"type": "final_text", "content": answer})
            _save_assistant(conversation_id, answer, stored, recorder, None)
            if deleted:
                await job.emit({"type": "recipes_changed", "recipes": [],
                                "deleted_recipe_ids": [item["recipe_id"] for item in deleted]})
                customer_service.schedule_summary_refresh(host.customer_id)
            await job.emit({"type": "done"})
            return
        llm = get_llm()
        ctx, host = build_context(user_id, conversation_id, llm)
        history = await _history(conversation_id, llm, emit)
        files = host.list_files()
        references = file_references(conversation_id, message_id, files)
        intent = await infer_request(llm, ctx, message, history, plan_steps, files=files, file_references=references)
        ctx.mode = intent.mode
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
                    "file_id", "filename", "kind", "kind_label", "size", "customer_id", "conversation_id", "run_id", "created_at", "sheets", "download_url",
                ) if key in file}})
            answer = await file_view_answer(llm, message, intent.files, selected_files)
            if answer:
                await emit({"type": "text_chunk", "content": answer})
            await emit({"type": "final_text", "content": answer})
            _save_assistant(conversation_id, answer, stored, recorder, None)
            await job.emit({"type": "done"})
            return
        ctx.save_recipe_requested = await infer_recipe_persistence(llm, message, history)
        host.save_recipe_requested = ctx.save_recipe_requested
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

    async def guard_creation_delivery(*, interrupted: bool = False) -> str:
        summary = (_creation_fallback(stored, interrupted=interrupted) if ctx.mode == "create" else "")
        summary = summary or _script_fallback(stored, mode=ctx.mode, interrupted=interrupted)
        if not summary:
            return ""
        previous = final_text or "".join(text_parts).strip()
        if previous:
            # 撤回模型尚未交付文件时的回答，避免界面和保存的最终结果宣称建表成功。
            await emit({"type": "note", "content": previous})
        await emit({"type": "text_chunk", "content": summary})
        await emit({"type": "final_text", "content": summary})
        return summary

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
        answer = await guard_creation_delivery(interrupted=True)
        if not answer:
            summary = _recipe_fallback(stored, interrupted=True) or "（已停止）"
            separator = "\n\n" if text_parts else ""
            await emit({"type": "text_chunk", "content": separator + summary})
            answer = "".join(text_parts).strip()
            await emit({"type": "final_text", "content": answer})
        _save_assistant(conversation_id, answer, stored, recorder, run_id)
        if host.deleted_recipes:
            customer_service.schedule_summary_refresh(host.customer_id)
        raise
    except Exception as exc:
        await emit({"type": "error", "content": f"处理出错：{exc}"})

    answer = await guard_creation_delivery() or final_text or "".join(text_parts).strip()
    if not answer or recorder.error:
        summary = _recipe_fallback(stored, interrupted=bool(recorder.error))
        if summary:
            addition = ("\n\n" if answer else "") + summary
            await emit({"type": "text_chunk", "content": addition})
            answer += addition
            await emit({"type": "final_text", "content": answer})
    if not answer and recorder.error:
        answer = f"⚠️ {recorder.error}"
    _save_assistant(conversation_id, answer, stored, recorder, run_id)
    title = await _maybe_title(conversation_id, llm, message, answer)
    if title:
        await job.emit({"type": "title", "title": title})
    if host.saved_recipes or host.deleted_recipes:
        await job.emit({"type": "recipes_changed", "recipes": host.saved_recipes,
                        "deleted_recipe_ids": [item["recipe_id"] for item in host.deleted_recipes]})
        customer_service.schedule_summary_refresh(ctx.host.customer_id)  # 流程变了：后台更新客户的整体处理逻辑
    await job.emit({"type": "done"})
    # 记忆抽取放到独立的后台任务里，不占用这次请求的连接
    task = asyncio.create_task(_learn_memories(llm, user_id, conversation_id, ctx.host.customer_id, message, answer))
    _background.add(task)
    task.add_done_callback(_background.discard)


def _script_fallback(events: list[dict[str, Any]], *, mode: str, interrupted: bool = False) -> str:
    """脚本失败或仅返回辅助数据时，不把最后一轮模型话术当成已交付修改。"""
    calls = [(index, event) for index, event in enumerate(events)
             if event.get("type") == "tool_call" and event.get("tool") == "run_python_script"]
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
    if any(event.get("type") == "file" and event.get("file_id") for event in events[:index]):
        message += "\n\n较早生成的文件仍可查看；最近一次调整尚未得到新文件。"
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
                    if event.get("type") == "tool_result" and event.get("ok") is False), None)
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
             and event.get("tool") in {"build_recipe", "revise_recipe", "run_recipe"}]
    if not calls:
        return ""
    results = {event.get("id"): event for event in events if event.get("type") == "tool_result"}
    failed = sum(results.get(call.get("id"), {}).get("ok") is False for call in calls)
    latest = results.get(calls[-1].get("id"))
    selected_run = next((event["selected_run"] for event in reversed(events)
                         if event.get("type") in {"recipe_completed", "recipe_saved"} and event.get("tool_call_id") == calls[-1].get("id")
                         and isinstance(event.get("selected_run"), dict)), {})
    files = {event.get("file_id") for event in events if event.get("type") == "file" and event.get("file_id")}
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
    blocks.append(f"本轮已生成 {len(files)} 个可下载文件，可先查看并核对已有结果。" if files
                  else "本轮尚未生成可下载的结果文件。")
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
