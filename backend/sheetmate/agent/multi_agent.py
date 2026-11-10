"""多表并行分析（移植自 TabClaw agent/multi_agent.py）。

触发条件：分析模式 + 至少两张上传的表 + 需求里有「对比 / 分别 / 差异 …」之类的词。
每张表一个专员 Agent 并行分析（只看得到自己的表，避免张冠李戴），事件通过 asyncio.Queue 交错返回；
全部完成后由汇总 LLM 做跨表对比，并用 [一致] / [待确认] 标注结论的可信度。

相对 TabClaw 的修正：专员调用 ReAct 时传入的是它自己的表视图（TabClaw 源码里误传了结果表字典）。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from sheetmate.agent.content import model_history, user_content
from sheetmate.agent.context import stage_history
from sheetmate.agent.executor import AgentExecutor, build_system_prompt
from sheetmate.agent.prompts import AGGREGATOR_PROMPT, MULTI_AGENT_KEYWORDS, SPECIALIST_MESSAGE
from sheetmate.agent.runtime import durable_value
from sheetmate.agent.tools import AgentContext
from sheetmate.engine.field_mapping import normalize_name
from sheetmate.recipe.schema import file_pattern_from_name


def should_activate(ctx: AgentContext, message: str) -> bool:
    """多表 + 对比类问题才并行；但结构相同的表（例如两个月的同一张明细）要跨表 JOIN/UNION 才能对比，
    每个专员只看一张表反而比不了，这种情况交给单个 Agent。"""
    if ctx.mode != "analyze" or ctx.delete_recipe_ids:
        return False
    # 流程管理是一次会话级操作；拆到每张表会重复提问并覆盖待确认清单。
    text = message.lower()
    if re.search(r"流程|配方|\brecipes?\b", text) and re.search(r"删|清空|移除|查看|列出|delete|remove|list", text):
        return False
    uploaded = [t for t in ctx.ws.list_tables() if t.source == "uploaded"]
    if len(uploaded) < 2:
        return False
    if re.search(r"关联|匹配|对账|合并|拼接|勾稽|联表|跨表|\b(?:join|lookup|reconcil\w*|merge|union)\b", text):
        return False
    if not re.search(r"分别|各自|独立分析|逐表分析|\b(?:independently|separately|each table)\b", text):
        return False
    return not _same_structure(uploaded)


def _same_structure(tables: list) -> bool:
    """是不是同一种表的不同期间 / 切片：文件名去掉日期后相同，或者两两之间列名（模糊）重合 ≥ 60%。"""
    patterns = {file_pattern_from_name(t.filename) for t in tables if t.filename}
    if len(patterns) == 1 and len(tables) == sum(1 for t in tables if t.filename):
        return True
    for i in range(len(tables)):
        for j in range(i + 1, len(tables)):
            if _column_overlap(tables[i].column_names, tables[j].column_names) < 0.6:
                return False
    return True


def _column_overlap(a: list[str], b: list[str]) -> float:
    """列名重合度：完全相同、去符号后相同或互相包含（如「店铺」和「店铺名称」、「单号」和「运单号」）都算对上。"""
    if not a or not b:
        return 0.0
    nb = [normalize_name(c) for c in b]
    hits = 0
    for col in a:
        na = normalize_name(col)
        if any(na == x or (min(len(na), len(x)) >= 2 and (na in x or x in na)) for x in nb):
            hits += 1
    return hits / max(len(a), len(b))


async def execute_multi(executor: AgentExecutor, ctx: AgentContext, message: str,
                        history: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    executor.configure_tools(ctx, message)
    tables = [t for t in ctx.ws.list_tables() if t.source == "uploaded"]
    yield {"type": "agent_pool_start", "agents": [{"id": t.name, "table_name": t.label or t.name} for t in tables]}
    if getattr(ctx, "runtime", None) is not None:
        async for event in _execute_durable_multi(executor, ctx, message, history, tables):
            yield event
        return
    done_marker = object()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    conclusions: dict[str, str] = {}
    budget = executor.new_budget()
    # Reserve enough operations for the coordinator's cross-table checks and
    # final delivery. Per-branch caps prevent a fast specialist from consuming
    # the whole task budget while the others are still waiting for the model.
    coordinator_reserve = min(8, max(1, executor.max_tool_calls // 4))
    specialist_total = max(0, executor.max_tool_calls - coordinator_reserve)
    per_branch, remainder = divmod(specialist_total, max(1, len(tables)))
    budget.branch_limits = {table.name: per_branch + (index < remainder) for index, table in enumerate(tables)}
    concurrency = asyncio.Semaphore(3)

    async def run_agent(table_name: str) -> None:
        scoped = replace(ctx, table_filter={table_name}, branch_id=table_name, tool_group="core")
        acquired = False
        try:
            await concurrency.acquire()
            acquired = True
            await queue.put({"type": "agent_start", "agent_id": table_name, "table_name": table_name,
                             "tool_calls_limit": budget.branch_limits[table_name]})
            messages = [{"role": "system", "content": build_system_prompt(scoped, message)}, *model_history(stage_history(history, "specialist")),
                        {"role": "user", "content": user_content(SPECIALIST_MESSAGE.format(table=table_name, message=message), scoped.image_parts)}]
            tools = executor.registry.for_context(scoped)
            conclusion = ""
            status, reason = "completed", ""
            async for event in executor.agent_stream(messages, tools, scoped, budget=budget, stop_scope="agent"):
                if event["type"] == "final_text":
                    conclusion = event["content"]
                    continue
                if event["type"] == "text_chunk":
                    continue  # 专员的过程文本不直接显示在回答里，只在执行明细里看结论
                if event["type"] == "execution_stop":
                    status, reason = event.get("status", "incomplete"), event.get("summary", "")
                await queue.put({**event, "agent_id": table_name})
            if status != "completed":
                conclusion = conclusion or reason or "本项尚未完成。"
            conclusions[table_name] = conclusion
            await queue.put({"type": "agent_done", "agent_id": table_name, "conclusion": conclusion,
                             "status": status, "error": status == "failed"})
        except Exception as exc:
            conclusions[table_name] = f"（分析出错：{exc}）"
            await queue.put({"type": "execution_stop", "scope": "agent", "agent_id": table_name,
                             "status": "failed", "code": getattr(exc, "code", "agent_error"),
                             "summary": conclusions[table_name], "verified": False})
            await queue.put({"type": "agent_done", "agent_id": table_name, "conclusion": conclusions[table_name], "error": True})
        finally:
            if acquired:
                concurrency.release()
            await queue.put(done_marker)

    tasks = [asyncio.create_task(run_agent(t.name)) for t in tables]
    finished = 0
    try:
        while finished < len(tasks):
            event = await queue.get()
            if event is done_marker:
                finished += 1
            else:
                yield event
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    yield {"type": "aggregate_start"}
    labels = {t.name: t.label or t.name for t in tables}
    parts = [f"**「{name}」（{labels.get(name, name)}）专员的结论：**\n{text}" for name, text in conclusions.items() if text]
    prompt = AGGREGATOR_PROMPT.format(count=len(conclusions), message=message, conclusions="\n\n---\n\n".join(parts) or "（无）")
    aggregate_history = stage_history(history, "execution")
    image_parts = [part for item in aggregate_history for part in item.get("image_parts", [])] + ctx.image_parts
    aggregate_messages = [{"role": "system", "content": build_system_prompt(ctx, message)},
                          {"role": "user", "content": user_content(prompt, image_parts)}]
    async for event in executor.agent_stream(aggregate_messages, executor.registry.for_context(ctx), ctx,
                                             budget=budget, scope=f"原始任务：{message}\n汇总并完成所有目标。"):
        yield event


async def _execute_durable_multi(executor: AgentExecutor, ctx: AgentContext, message: str,
                                 history: list[dict[str, Any]], tables: list[Any]) -> AsyncIterator[dict[str, Any]]:
    """Serial specialists retain isolation and one durable checkpoint writer."""
    runtime = ctx.runtime
    if runtime.state.get("whole_task_continuation"):
        async for event in executor.resume_runtime(ctx, message, history):
            yield event
        return
    state = runtime.state.setdefault("multi_state", {"conclusions": {}, "completed": {}, "current": None,
                                                      "revision": runtime.revision})
    conclusions, completed = state["conclusions"], state["completed"]
    budget = executor.new_budget()
    if runtime.state.get("budget"):
        budget.restore(runtime.state["budget"])
    else:
        reserve = min(8, max(1, executor.max_tool_calls // 4))
        per_branch, remainder = divmod(max(0, executor.max_tool_calls - reserve), max(1, len(tables)))
        budget.branch_limits = {table.name: per_branch + (index < remainder) for index, table in enumerate(tables)}

    async def save() -> None:
        runtime.state["budget"] = budget.to_state()
        runtime.state["multi_state"] = durable_value(state)
        await runtime.checkpoint()

    async def steered() -> bool:
        await runtime.consume(ctx)
        return runtime.revision != state["revision"]

    for table in tables:
        name = table.name
        if name in completed:
            yield {"type": "agent_done", "agent_id": name, "conclusion": conclusions.get(name, ""),
                   "status": completed[name], "error": completed[name] == "failed", "restored": True}
            continue
        if await steered():
            async for event in executor.continue_runtime(ctx, message, history):
                yield event
            return
        if state["current"] != name:
            runtime.state.pop("loop", None)
        state["current"] = name
        await save()
        scoped = replace(ctx, table_filter={name}, branch_id=name, tool_group="core")
        scoped.runtime = runtime
        yield {"type": "agent_start", "agent_id": name, "table_name": name,
               "tool_calls_limit": budget.branch_limits.get(name, executor.max_tool_calls)}
        messages = [{"role": "system", "content": build_system_prompt(scoped, message)},
                    *model_history(stage_history(history, "specialist")),
                    {"role": "user", "content": user_content(SPECIALIST_MESSAGE.format(table=name, message=message),
                                                               scoped.image_parts)}]
        conclusion, status, reason = "", "incomplete", ""
        async for event in executor.agent_stream(messages, executor.registry.for_context(scoped), scoped,
                                                 budget=budget, stop_scope="agent"):
            kind = event["type"]
            if kind == "execution_steered":
                async for continued in executor.continue_runtime(ctx, message, history):
                    yield continued
                return
            if kind == "final_text":
                conclusion = event["content"]
                continue
            if kind == "text_chunk":
                continue
            if kind == "execution_stop":
                status, reason = event.get("status", "incomplete"), event.get("summary", "")
            yield {**event, "agent_id": name}
            if kind in {"error", "clarify"}:
                return
        conclusions[name] = conclusion or reason or "本项尚未完成。"
        completed[name] = status
        await save()
        yield {"type": "agent_done", "agent_id": name, "conclusion": conclusions[name],
               "status": status, "error": status == "failed"}

    if await steered():
        async for event in executor.continue_runtime(ctx, message, history):
            yield event
        return
    if state["current"] != "aggregate":
        runtime.state.pop("loop", None)
    state["current"] = "aggregate"
    await save()
    yield {"type": "aggregate_start"}
    labels = {table.name: table.label or table.name for table in tables}
    parts = [f"**「{name}」（{labels.get(name, name)}）专员的结论：**\n{text}" for name, text in conclusions.items() if text]
    prompt = AGGREGATOR_PROMPT.format(count=len(conclusions), message=message,
                                     conclusions="\n\n---\n\n".join(parts) or "（无）")
    messages = [{"role": "system", "content": build_system_prompt(ctx, message)},
                {"role": "user", "content": user_content(prompt, ctx.image_parts)}]
    async for event in executor.agent_stream(messages, executor.registry.for_context(ctx), ctx,
                                             budget=budget, scope=f"原始任务：{message}\n汇总并完成所有目标。"):
        yield event
