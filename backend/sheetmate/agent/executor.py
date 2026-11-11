"""ReAct 工具循环（移植自 TabClaw agent/executor.py）。

一轮循环：流式调用 LLM → 收集文本增量和 tool_call 分片 → 有工具调用就执行工具、把结果作为 role=tool 回传 → 再调 LLM；
没有工具调用时，先核对实际完成依据，再决定继续还是收尾。默认最多 max_iterations 轮；使用脚本时增加有界的修正轮数。

保留了 TabClaw 的两个细节：
- 工具调用参数是分片流式返回的，按 index 拼接完整后再 json.loads
- DeepSeek 偶尔把内部工具标记 <｜tool▁call▁begin｜> 泄漏到正文，检测到后不再显示，并在最终文本里清掉
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from sheetmate.agent.completion import MAX_COMPLETION_RETRIES, check_completion, continuation, text_content
from sheetmate.agent.content import model_history, user_content
from sheetmate.agent.context import ContextBlock, assemble_context, stage_history
from sheetmate.agent.evidence import snapshot_inputs, tool_evidence
from sheetmate.agent.task_state import task_projection
from sheetmate.agent.llm import LLMError, parse_json, translate_error
from sheetmate.agent.parallel_tools import ParallelOutcome, independent_batch, run_parallel_tools
from sheetmate.agent.prompts import (
    AGENT_SYSTEM,
    AGENT_TABLE_LINE,
    APPROACH_SWITCH_MESSAGE,
    AUTONOMOUS_RECOVERY_MESSAGE,
    CHAT_SYSTEM,
    FINAL_ROUND_MESSAGE,
    MODE_RULES,
    MODIFY_NUDGE,
    PLAN_STEP_MESSAGE,
    RECOVERY_BUDGET_MESSAGE,
    REPEATED_FAILURE_MESSAGE,
    REPEATED_SCRIPT_MESSAGE,
    REFLECT_MESSAGE,
    SCRIPT_RETRY_MESSAGE,
    THINKING_RULES,
    TOOL_FALLBACK_MESSAGE,
)
from sheetmate.agent.recovery import MAX_METHODS, METHOD_FAILURE_LIMIT, RECONSIDER_TOOL, RecoveryState, _Method, _ObservedResult
from sheetmate.agent.reflection import classify_failure, guidance as failure_guidance
from sheetmate.agent.runtime import durable_value, restore_messages, runtime_images
from sheetmate.agent.scripting import ScriptRequest
from sheetmate.agent.summarizer import estimate_tokens
from sheetmate.agent.table_probe import table_probe_context
from sheetmate.agent.structured import StructuredOutputError
from sheetmate.agent.tool_results import ProjectionBudget, project_tool_result, result_reference
from sheetmate.agent.tools import (
    AgentContext, Tool, ToolRegistry, ToolResult, ToolSchemaError, execute_tool, tool_argument_error, validate_tool_args,
)
from sheetmate.conversation_log import record_event
from sheetmate.engine.script_references import externalize_script_references, externalize_script_text
from sheetmate.engine.script_store import script_params
from sheetmate.services.tool_results import sanitize_tool_payload

_TOOL_MARKER = "<｜tool▁call▁begin｜>"
_TOOL_RE = re.compile(r"<｜tool[\s\S]*?(?:<｜tool▁calls?▁end｜>|$)")
_ROLE_TAGS = {"input": "", "expected": "，⭐期望结果（人工做好的样例）", "derived": "，分析结果", "result": "，流程产出"}
_EXPLORE_TOOLS = {"table_info", "value_counts", "column_stats", "preview_table", "run_sql", "list_files", "get_recipe"}
_READ_OBSERVATION_TOOLS = {"table_info", "value_counts", "column_stats", "preview_table", "run_sql"}
_MAX_IDENTICAL_OBSERVATIONS = 20
_RECOVERABLE_MODEL_ERRORS = frozenset({
    "stream_incomplete", "empty_response", "tool_protocol", "connection_error", "timeout", "stream_idle_timeout",
})
_RECIPE_TOOLS = {"build_recipe", "revise_recipe", "run_recipe"}
_SCRIPT_TOOL = "run_python_script"
_SCRIPT_EXTRA_ITERATIONS = 60
_DIAGNOSTIC_TOOLS = (_EXPLORE_TOOLS - {"run_sql"}) | {
    "inspect_python_script", "inspect_workbook_creation", "list_recipes", "load_file", "show_files",
    "remember", "save_field_mapping", "save_as_recipe", "delete_recipe", "delete_recipes", "ask_user",
    "load_tool_group", "inspect_execution_evidence", "inspect_task_context", "inspect_saved_memories",
    "inspect_conversation_history",
    "read_tool_result",
}
_NUDGE_AFTER = 4  # 修改模式下查看数据超过这么多次还没生成流程，就提醒一次


def _checkpoint_tool_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep rich UI data in its authorized durable output, outside checkpoints."""
    projected, seen = [], set()
    for event in events:
        reference = event.get("_tool_result_ref")
        if isinstance(reference, dict) and reference.get("result_id"):
            key = (reference["result_id"], event.get("tool_call_id"), reference.get("branch_id"))
            if key not in seen:
                seen.add(key)
                projected.append({"type": "tool_result_events", "result_id": key[0],
                                  "tool_call_id": key[1], "branch_id": key[2]})
        else:
            projected.append(event)
    return projected


def _hydrate_tool_events(ctx: AgentContext, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hydrated = []
    for event in events:
        if event.get("type") != "tool_result_events":
            hydrated.append(event)
            continue
        reference = {"result_id": event["result_id"], "branch_id": event.get("branch_id")}
        hydrated.extend({**item, "tool_call_id": event.get("tool_call_id"), "_tool_result_ref": reference}
                        for item in ctx.host.resolve_tool_result_events(**reference))
    return hydrated


@dataclass
class _ScriptAttempt:
    result: ToolResult
    executions: int = 1
    evidence_id: str | None = None


@dataclass
class _ReadProgress:
    key: str | None = None
    repetitions: int = 0

    def observe(self, key: str | None) -> bool:
        self.repetitions = self.repetitions + 1 if key and key == self.key else int(key is not None)
        self.key = key
        return self.repetitions >= _MAX_IDENTICAL_OBSERVATIONS


@dataclass
class _ToolBudget:
    used: int = 0
    protocol_retries: int = 0
    stream_recoveries: int = 0
    results: dict[str, dict[str, Any]] = field(default_factory=dict)
    branch_used: dict[str, int] = field(default_factory=dict)
    branch_limits: dict[str, int] = field(default_factory=dict)
    script_attempts: dict[tuple[str | None, str], _ScriptAttempt] = field(default_factory=dict)
    recoveries: dict[tuple[str | None, str], RecoveryState] = field(default_factory=dict)
    read_progress: dict[tuple[str | None, str], _ReadProgress] = field(default_factory=dict)
    scope_dependencies: dict[tuple[str | None, str], set[str]] = field(default_factory=dict)

    def to_state(self) -> dict[str, Any]:
        def rows(items, encode):
            return [{"branch": key[0], "scope": key[1], "state": encode(value)} for key, value in items.items()]

        def recovery(value):
            return {"methods": [asdict(method) for method in value.methods], "pending": sorted(value.pending),
                    "results": {key: asdict(item) for key, item in value.results.items()},
                    "invalid_results": sorted(value.invalid_results)}

        return durable_value({
            "used": self.used, "protocol_retries": self.protocol_retries,
            "stream_recoveries": self.stream_recoveries, "results": self.results,
            "branch_used": self.branch_used, "branch_limits": self.branch_limits,
            "script_attempts": rows(self.script_attempts, asdict), "recoveries": rows(self.recoveries, recovery),
            "read_progress": rows(self.read_progress, asdict),
            "scope_dependencies": rows(self.scope_dependencies, sorted),
        })

    @classmethod
    def from_state(cls, state: dict[str, Any]) -> _ToolBudget:
        budget = cls(used=int(state.get("used", 0)), protocol_retries=int(state.get("protocol_retries", 0)),
                     stream_recoveries=int(state.get("stream_recoveries", 0)),
                     results=state.get("results", {}), branch_used=state.get("branch_used", {}),
                     branch_limits=state.get("branch_limits", {}))
        for row in state.get("script_attempts", []):
            value = row["state"]
            budget.script_attempts[(row["branch"], row["scope"])] = _ScriptAttempt(
                ToolResult(**value["result"]), int(value["executions"]), value.get("evidence_id"))
        for row in state.get("recoveries", []):
            value = row["state"]
            recovery = RecoveryState(
                methods=[_Method(**method) for method in value["methods"]], pending=set(value["pending"]),
                results={key: _ObservedResult(**item) for key, item in value["results"].items()},
                invalid_results=set(value["invalid_results"]))
            # Older checkpoints indexed only call IDs, although model feedback
            # exposed evidence IDs. Recover their known identity without text guesses.
            for evidence_id, receipt in budget.results.items():
                observed = recovery.results.get(receipt.get("call_id"))
                if (observed is None or receipt.get("branch_id") != row["branch"]
                        or receipt.get("scope") != row["scope"]):
                    continue
                observed.aliases = sorted(set(observed.aliases) | {evidence_id})
            budget.recoveries[(row["branch"], row["scope"])] = recovery
        for row in state.get("read_progress", []):
            budget.read_progress[(row["branch"], row["scope"])] = _ReadProgress(**row["state"])
        for row in state.get("scope_dependencies", []):
            budget.scope_dependencies[(row["branch"], row["scope"])] = set(row["state"])
        return budget

    def restore(self, state: dict[str, Any]) -> None:
        self.__dict__.update(self.from_state(state).__dict__)

    def exhausted(self, maximum: int, branch_id: str | None = None) -> bool:
        return (self.used >= maximum or bool(branch_id and branch_id in self.branch_limits
                and self.branch_used.get(branch_id, 0) >= self.branch_limits[branch_id]))

    def evidence(self, branch_id: str | None = None) -> dict[str, dict[str, Any]]:
        """A specialist can only use its own evidence; the coordinator sees all."""
        return ({key: item for key, item in self.results.items() if item.get("branch_id") == branch_id}
                if branch_id else self.results)

    def allow_dependencies(self, branch_id: str | None, scope: str, dependency_scopes: set[str]) -> None:
        allowed = set(dependency_scopes)
        for dependency in dependency_scopes:
            allowed.update(self.scope_dependencies.get((branch_id, dependency), set()))
        self.scope_dependencies[(branch_id, scope)] = allowed

    def eligible_evidence(self, branch_id: str | None, scope: str) -> set[str]:
        """A step may cite its own work, declared prerequisites and explicit reuse."""
        scopes = {scope} | self.scope_dependencies.get((branch_id, scope), set())
        results = {key: item for key, item in self.results.items() if item.get("branch_id") == branch_id}
        allowed = {key for key, item in results.items() if item.get("scope") in scopes}
        for key in list(allowed):
            original = (results[key].get("evidence") or {}).get("reused_from")
            if original in results:
                allowed.add(original)
        return allowed

    def snapshot(self, maximum: int, branch_id: str | None = None) -> dict[str, Any]:
        return {"tool_calls_used": self.used, "tool_calls_limit": maximum,
                "tool_calls_remaining": max(0, maximum - self.used),
                **({"branch_id": branch_id, "branch_tool_calls_used": self.branch_used.get(branch_id, 0),
                    "branch_tool_calls_limit": self.branch_limits.get(branch_id, maximum)} if branch_id else {})}


class AgentExecutor:
    def __init__(self, llm: Any, registry: ToolRegistry | None = None, max_iterations: int = 200,
                 max_tool_calls: int = 800, max_parallel_tools: int = 3):
        self.llm = llm
        self.registry = registry or ToolRegistry()
        self.max_iterations = max(1, max_iterations)
        self.max_tool_calls = max(1, max_tool_calls)
        self.max_parallel_tools = max(1, min(3, max_parallel_tools))

    def new_budget(self) -> _ToolBudget:
        """一次任务共用的工具调用预算：计划的各个步骤、补做和自检共享同一份。"""
        return _ToolBudget()

    async def continue_runtime(self, ctx: AgentContext, message: str,
                               history: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """Continue a late accepted requirement using all prior work and budget."""
        runtime = getattr(ctx, "runtime", None)
        if runtime is None:
            async for event in self.execute(ctx, message, history):
                yield event
            return
        self.configure_tools(ctx, runtime.effective_message(message))
        budget = _ToolBudget.from_state(runtime.state.get("budget", {}))
        messages = restore_messages(runtime.state.get("conversation", []), runtime_images(ctx))
        if not messages:
            messages = [{"role": "system", "content": build_system_prompt(ctx, message)},
                        *model_history(stage_history(history, "execution"))]
        else:
            messages[0] = {"role": "system", "content": build_system_prompt(ctx, runtime.effective_message(message))}
        runtime.state.pop("loop", None)
        runtime.state["whole_task_continuation"] = True
        messages.append({"role": "user", "content": runtime.effective_message(message)})
        tools = [] if getattr(ctx, "execution_action", "process") == "chat" else self.registry.for_context(ctx)
        async for event in self.agent_stream(messages, tools, ctx, budget=budget,
                                             scope=message):
            yield event

    async def resume_runtime(self, ctx: AgentContext, message: str,
                             history: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """Resume a whole-task continuation without replacing its pending calls."""
        runtime = getattr(ctx, "runtime", None)
        saved = runtime.state.get("loop") if runtime else None
        if not saved:
            async for event in self.continue_runtime(ctx, message, history):
                yield event
            return
        budget = _ToolBudget.from_state(runtime.state.get("budget", {}))
        messages = restore_messages(saved["messages"], runtime_images(ctx))
        tools = [] if getattr(ctx, "execution_action", "process") == "chat" else self.registry.for_context(ctx)
        async for event in self.agent_stream(messages, tools, ctx, budget=budget,
                                             scope=saved.get("original_scope", saved["scope"]),
                                             stop_scope=saved.get("stop_scope", "task")):
            yield event

    def budget_exhausted(self, budget: _ToolBudget) -> bool:
        return budget.used >= self.max_tool_calls

    def configure_tools(self, ctx: AgentContext, message: str) -> None:
        """Keep a stable compact menu, expanding only explicit capabilities."""
        if ctx.table_filter is not None or ctx.tool_group not in {None, "core"}:
            return
        ctx.tool_group = "core"
        if ctx.mode == "create" or re.search(r"新建|创建|排版|格式|建表", message):
            ctx.enabled_tool_groups.add("workbook")
        if ctx.mode == "modify" or ctx.save_recipe_requested or re.search(r"流程|配方|recipe", message, re.I):
            ctx.enabled_tool_groups.add("workflow")
        if re.search(r"脚本|python|pandas", message, re.I):
            ctx.enabled_tool_groups.add("python")
        if re.search(r"匹配|关联|合并|拼接|去重|清洗|透视|分组|汇总|lookup|join|pivot", message, re.I):
            ctx.enabled_tool_groups.add("formulas")

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    async def converse(self, ctx: AgentContext, message: str, history: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """普通对话和意图询问，不提供任务执行工具。"""
        ctx.execution_action = "chat"
        if getattr(ctx, "runtime", None) and ctx.runtime.state.get("whole_task_continuation"):
            async for event in self.resume_runtime(ctx, message, history):
                yield event
            return
        system = assemble_context([
            ContextBlock("system_policy", CHAT_SYSTEM, required=True),
            ContextBlock("customer", "## 当前客户资料\n" + build_customer_block(ctx), required=True),
            ContextBlock("business_memory", "## 已保存的长期记忆与当前流程规则\n" + (ctx.host.memory_text(message) or "（当前可用范围尚未保存记忆条目）"), required=True),
            ContextBlock("table_memory", table_probe_context(ctx.ws, ctx.visible_tables(),
                         ctx.file_memory if ctx.table_filter is None else "", query=message), priority=20),
        ], stage="chat")
        messages = [{"role": "system", "content": system}, *model_history(stage_history(history, "chat")),
                    {"role": "user", "content": user_content(message, ctx.image_parts)}]
        async for event in self.agent_stream(messages, [], ctx):
            yield event

    async def execute(self, ctx: AgentContext, message: str, history: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """直接执行（不带计划）。"""
        if getattr(ctx, "runtime", None) and ctx.runtime.state.get("whole_task_continuation"):
            async for event in self.resume_runtime(ctx, message, history):
                yield event
            return
        self.configure_tools(ctx, message)
        messages = [{"role": "system", "content": build_system_prompt(ctx, message)}, *model_history(stage_history(history, "execution")),
                    {"role": "user", "content": user_content(message, ctx.image_parts)}]
        async for event in self.agent_stream(messages, self.registry.for_context(ctx), ctx, scope=message):
            yield event

    async def execute_plan(self, ctx: AgentContext, message: str, steps: list[dict[str, Any]],
                           history: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """按用户确认的计划逐步执行，每步一次 ReAct，最后自检（TabClaw execute_plan）。"""
        from sheetmate.agent.planner import plan_from_steps

        runtime = getattr(ctx, "runtime", None)
        if runtime and runtime.state.get("whole_task_continuation"):
            async for event in self.resume_runtime(ctx, message, history):
                yield event
            return
        if runtime and runtime.state.get("plan"):
            saved_loop = runtime.state.get("loop") or {}
            if saved_loop:
                budget = _ToolBudget.from_state(runtime.state.get("budget", {}))
                messages = restore_messages(saved_loop["messages"], runtime_images(ctx))
                async for event in self.agent_stream(
                    messages, self.registry.for_context(ctx), ctx, budget=budget,
                    scope=saved_loop.get("original_scope", saved_loop["scope"]),
                    stop_scope=saved_loop.get("stop_scope", "step"),
                ):
                    if event["type"] not in {"text_chunk", "final_text", "execution_stop"}:
                        yield event
                    if event["type"] in {"error", "clarify"}:
                        return
            async for event in self.continue_runtime(ctx, message, history):
                yield event
            return
        plan = plan_from_steps(steps)
        steps = plan.steps
        self.configure_tools(ctx, message)
        tools = self.registry.for_context(ctx)
        conversation = [{"role": "system", "content": build_system_prompt(ctx, message)}, *model_history(stage_history(history, "execution")),
                        {"role": "user", "content": user_content(f"原始需求：{message}\n下面按计划逐步执行。", ctx.image_parts)}]
        budget = _ToolBudget()
        executed_steps = 0
        step_states: dict[int, str] = {}
        pending: list[str] = []
        step_scopes: dict[int, str] = {}

        async def checkpoint_plan() -> None:
            if runtime:
                runtime.state["plan"] = durable_value({"steps": steps, "step_states": {str(key): value for key, value in step_states.items()},
                                                        "conversation": conversation, "pending": pending})
                runtime.state["budget"] = budget.to_state()
                await runtime.checkpoint()

        await checkpoint_plan()
        yield {"type": "plan_start", "steps": [{"id": index, "description": plan.display_description(index)}
                                                for index, step in enumerate(steps, start=1)]}
        for i, step in enumerate(steps, start=1):
            if runtime and await runtime.consume(ctx):
                await checkpoint_plan()
                async for event in self.continue_runtime(ctx, message, history):
                    yield event
                return
            executed_steps = i
            description = str(step.get("description") or f"第 {i} 步")
            dependencies = step.get("depends_on", [i - 1] if i > 1 else [])
            if any(step_states.get(dep) != "done" for dep in dependencies):
                step_states[i] = "blocked"
                pending.append(description)
                yield {"type": "step_done", "step_num": i, "status": "warning",
                       "summary": "依赖步骤尚未通过验收，本项未执行。",
                       "detail": "依赖步骤尚未通过验收，本项未执行。", "blocked": True, "depends_on": dependencies}
                continue
            yield {"type": "step_start", "step_num": i, "total": len(steps), "description": plan.display_description(i)}
            step_message = (PLAN_STEP_MESSAGE.format(num=i, total=len(steps), description=description)
                            + f"\n本项验收标准：{step['check']}")
            step_scopes[i] = step_message
            budget.allow_dependencies(ctx.branch_id, step_message, {step_scopes[dep] for dep in dependencies})
            # 每一步都刷新系统提示里的表清单：前面步骤生成的表要让模型看得到
            conversation[0] = {"role": "system", "content": build_system_prompt(ctx, message)}
            conversation.append({"role": "user", "content": step_message})
            # 把这一步真实的工具调用和结果都记进对话（TabClaw 原来只保留文字结论，模型会以为说了就等于做了）
            transcript: list[dict[str, Any]] = []
            step_text = ""
            waiting = False
            question = ""
            unresolved_tools: set[str] = set()
            incomplete = False
            verified_complete = False
            async for event in self.agent_stream(conversation, tools, ctx, transcript=transcript, budget=budget,
                                                 scope=step_message, stop_scope="step"):
                if event["type"] == "execution_steered":
                    async for continued in self.continue_runtime(ctx, message, history):
                        yield continued
                    return
                if event["type"] == "text_chunk":
                    step_text += event["content"]  # 每一步的文字小结是过程，不是给用户的最终回答
                    continue
                if event["type"] == "note":
                    step_text = ""
                    yield {**event, "step_num": i}
                    continue
                if event["type"] == "final_text":
                    step_text = event.get("content", "")
                    continue
                yield event
                if event["type"] == "error":
                    error = event.get("content", "执行失败")
                    detail = "\n\n".join(part for part in (step_text.strip(), error) if part)
                    yield {"type": "step_done", "step_num": i, "status": "error", "summary": error, "detail": detail}
                    return
                if event["type"] == "clarify":
                    waiting = True
                    question = event.get("question", "需要确认后才能继续")
                if event["type"] == "execution_stop" and event.get("status") == "incomplete":
                    incomplete = True
                if event["type"] == "execution_stop" and event.get("status") == "completed":
                    verified_complete = event.get("verified") is True
                if event["type"] == "tool_result" and event.get("error_kind") not in {"protocol", "schema"}:
                    if event["ok"]:
                        unresolved_tools.discard(event["tool"])
                    else:
                        unresolved_tools.add(event["tool"])
            conversation.extend(transcript)
            budget_exhausted = budget.used >= self.max_tool_calls
            status = "waiting" if waiting else "warning" if (not verified_complete or unresolved_tools or incomplete
                or (budget_exhausted and not verified_complete)) else "done"
            step_states[i] = status
            if status != "done":
                pending.append(description)
            await checkpoint_plan()
            summary = question if waiting else step_text.strip()
            detail = "\n\n".join(part for part in (step_text.strip(), question if waiting else "") if part)
            yield {"type": "step_done", "step_num": i, "status": status, "summary": summary, "detail": detail}
            if waiting:
                return
            if budget_exhausted or incomplete:
                if budget_exhausted:
                    pending.extend(str(item.get("description") or "后续步骤") for item in steps[i:])
                    break
        if pending:
            answer = "本轮尚未完成，部分步骤未通过验收或依赖未就绪。\n\n仍需完成：" + "；".join(pending)
            yield {"type": "execution_stop", "scope": "task", "status": "incomplete", "code": "plan_incomplete",
                   "summary": answer, "verified": False}
            yield {"type": "text_chunk", "content": answer}
            yield {"type": "final_text", "content": answer}
            return
        yield {"type": "reflect_start"}
        conversation[0] = {"role": "system", "content": build_system_prompt(ctx, message)}
        reflect = REFLECT_MESSAGE.format(message=message, total=executed_steps)
        if executed_steps < len(steps):
            reflect += f"\n计划中还有 {len(steps) - executed_steps} 个步骤未执行，不得称已完成。"
        async for event in self.agent_stream(conversation + [{"role": "user", "content": reflect}], tools, ctx,
                                             budget=budget, scope=f"原始任务：{message}\n请根据实际执行结果整理最终回答。"):
            yield event
        yield {"type": "reflect_done"}

    # ------------------------------------------------------------------
    # 核心循环
    # ------------------------------------------------------------------

    async def agent_stream(self, messages: list[dict[str, Any]], tools: list[Tool], ctx: AgentContext,
                           transcript: list[dict[str, Any]] | None = None,
                           budget: _ToolBudget | None = None, scope: str | None = None,
                           stop_scope: str = "task") -> AsyncIterator[dict[str, Any]]:
        """transcript 不为空时，把这一轮新增的消息（工具调用、工具结果、最终回答）追加进去，供计划模式接续上下文。"""
        msgs = list(messages)
        start = len(msgs)
        if tools and ctx.tool_group is not None:
            tools = self.registry.for_context(ctx)
        budget = budget or _ToolBudget()
        scope = scope or next((text_content(item.get("content")) for item in reversed(messages)
                               if item.get("role") == "user"), "当前任务")
        runtime = getattr(ctx, "runtime", None)
        original_scope = scope
        scope_key = (ctx.branch_id, original_scope)
        tool_map = {t.name: t for t in tools}
        recovery = budget.recoveries.setdefault(scope_key, RecoveryState())
        read_progress = budget.read_progress.setdefault(scope_key, _ReadProgress())
        if tools:
            tool_map[RECONSIDER_TOOL] = recovery.tool()
        explored, built, nudged = 0, False, False
        iteration, iteration_limit = 0, max(1, self.max_iterations)
        script_budget_added, fallback_suggested = False, False
        failures: dict[tuple[str, str], int] = {}
        reflected_failures: set[tuple[str, str]] = set()
        pending_reflections: list[str] = []
        policy = getattr(getattr(self.llm, "cfg", None), "runtime_policy", None)
        protocol_retry_limit = getattr(policy, "tool_protocol_retries", 12)
        stream_retry_limit = getattr(policy, "stream_recovery_retries", 5)
        protocol_pending = False
        completion_retries = 0
        recovery_finish_prompts = 0
        finish_reason: str | None = None
        stream_id = uuid4().hex
        no_progress = False
        pending_response: dict[str, Any] | None = None
        saved = runtime.state.get("loop", {}) if runtime else {}
        restored = saved.get("original_scope", saved.get("scope")) == original_scope and saved.get("stop_scope") == stop_scope
        applied_revision = int(saved.get("revision", 0)) if restored else 0
        reserved_call_ids = set(saved.get("reserved_call_ids", [])) if restored else set()
        parallel_batch = saved.get("parallel_batch") if restored else None
        projection_budget = ProjectionBudget(used_chars=int(saved.get("projection_used_chars", 0)) if restored else 0)
        if runtime:
            # An earlier phase may already have marked messages committed.
            # Their presence in runtime is not proof this loop has seen them.
            if not parallel_batch:
                await runtime.consume(ctx)
            scope = saved.get("scope", original_scope) if parallel_batch else runtime.effective_message(original_scope)
            self.configure_tools(ctx, scope)
            if tools and ctx.tool_group is not None:
                tool_map.update({item.name: item for item in self.registry.for_context(ctx)})
        if restored:
            msgs = restore_messages(saved["messages"], runtime_images(ctx))
            start = int(saved.get("start", start))
            budget.restore(saved.get("budget", {}))
            recovery = budget.recoveries.setdefault(scope_key, RecoveryState())
            read_progress = budget.read_progress.setdefault(scope_key, _ReadProgress())
            if tools:
                tool_map[RECONSIDER_TOOL] = recovery.tool()
            pending_response = saved.get("pending_response")
            iteration = int(saved.get("iteration", 0)) - int(bool(pending_response))
            iteration_limit = int(saved.get("iteration_limit", iteration_limit))
            explored, built, nudged = saved.get("explored", 0), saved.get("built", False), saved.get("nudged", False)
            script_budget_added = saved.get("script_budget_added", False)
            # Existing checkpoints keep spent rounds, but can use a newly increased limit.
            iteration_limit = max(iteration_limit, self.max_iterations +
                                  (_SCRIPT_EXTRA_ITERATIONS if script_budget_added else 0))
            fallback_suggested, protocol_pending = saved.get("fallback_suggested", False), saved.get("protocol_pending", False)
            completion_retries, no_progress = saved.get("completion_retries", 0), saved.get("no_progress", False)
            recovery_finish_prompts = int(saved.get("recovery_finish_prompts", 0))
            failures = {(row[0], row[1]): row[2] for row in saved.get("failures", [])}
            reflected_failures = {tuple(row) for row in saved.get("reflected_failures", [])}
            pending_reflections = list(saved.get("pending_reflections", []))
            if saved.get("terminal_events") and applied_revision == runtime.revision:
                if transcript is not None:
                    transcript.extend(msgs[start:])
                for event in saved["terminal_events"]:
                    yield event
                return

        def snapshot_loop(*, response: dict[str, Any] | None = None,
                          snapshot_messages: list[dict[str, Any]] | None = None,
                          terminal_events: list[dict[str, Any]] | None = None) -> None:
            if runtime is None:
                return
            values = durable_value(msgs if snapshot_messages is None else snapshot_messages)
            budget_state = budget.to_state()
            runtime.state["budget"] = budget_state
            runtime.state["conversation"] = values
            runtime.state["loop"] = {
                "scope": scope, "original_scope": original_scope, "revision": applied_revision,
                "stop_scope": stop_scope, "messages": values, "start": start,
                "budget": budget_state, "iteration": iteration, "iteration_limit": iteration_limit,
                "explored": explored, "built": built, "nudged": nudged, "script_budget_added": script_budget_added,
                "fallback_suggested": fallback_suggested, "protocol_pending": protocol_pending,
                "completion_retries": completion_retries, "recovery_finish_prompts": recovery_finish_prompts,
                "no_progress": no_progress,
                "failures": [[*key, count] for key, count in failures.items()],
                "reflected_failures": [list(key) for key in sorted(reflected_failures)],
                "pending_reflections": list(pending_reflections),
                "pending_response": durable_value(response), "terminal_events": terminal_events or [],
                "reserved_call_ids": sorted(reserved_call_ids), "parallel_batch": durable_value(parallel_batch),
                "projection_used_chars": projection_budget.used_chars,
            }

        async def consume(abandon: dict[str, Any] | None = None, completed: int = 0) -> bool:
            nonlocal scope, pending_response, applied_revision, no_progress, iteration_limit, protocol_pending
            if runtime is None:
                return False
            if parallel_batch:
                # Finish the already authorized read batch before applying a
                # newer requirement or abandoning any of its durable results.
                return False
            await runtime.consume(ctx)
            if applied_revision == runtime.revision:
                return False
            fresh = runtime.state.get("steers", [])[applied_revision:]
            if abandon is not None:
                abandon["tool_calls"] = abandon["tool_calls"][:completed]
            pending_response = None
            scope = runtime.effective_message(original_scope)
            # Whole-task continuation already carries one composed request.
            # Other entries need the still-unseen inbound messages themselves.
            if not any(item.get("role") == "user" and text_content(item.get("content")) == scope for item in msgs):
                msgs.extend({"role": "user", "content": item["content"]} for item in fresh)
            applied_revision = runtime.revision
            no_progress = False
            read_progress.key, read_progress.repetitions = None, 0
            protocol_pending = False
            # New inbound work has its own bounded decision window. The
            # shared tool/model/token/time budgets remain spent across it.
            iteration_limit = max(iteration_limit, iteration + max(1, self.max_iterations))
            self.configure_tools(ctx, scope)
            if tools and ctx.tool_group is not None:
                tool_map.update({item.name: item for item in self.registry.for_context(ctx)})
            snapshot_loop()
            await runtime.checkpoint()
            return True

        def stop(status: str, code: str, summary: str, *, verified: bool = False) -> dict[str, Any]:
            event = {"type": "execution_stop", "status": status, "code": code, "summary": summary,
                     "finish_reason": finish_reason, "iteration": iteration, "iteration_limit": iteration_limit,
                     **budget.snapshot(self.max_tool_calls, ctx.branch_id),
                     "scope": stop_scope, "verified": verified}
            usage_snapshot = getattr(self.llm, "usage_snapshot", None)
            if callable(usage_snapshot):
                event["usage"] = usage_snapshot()
            record_event("agent.stop", "sheetmate.agent.executor.AgentExecutor.agent_stream", event, stream_id=stream_id)
            return event

        if await consume() and stop_scope in {"step", "agent"}:
            if transcript is not None:
                transcript.extend(msgs[start:])
            yield {"type": "execution_steered", "scope": stop_scope, "revision": runtime.revision}
            return
        while iteration < iteration_limit:
            if pending_reflections and pending_response is None and not parallel_batch:
                # A process may have stopped after the durable tool receipt
                # but before its hint was appended to the next decision.
                msgs.extend({"role": "user", "content": message} for message in pending_reflections)
                pending_reflections.clear()
            if await consume() and stop_scope in {"step", "agent"}:
                if transcript is not None:
                    transcript.extend(msgs[start:])
                yield {"type": "execution_steered", "scope": stop_scope, "revision": runtime.revision}
                return
            budget_exhausted = recovery.exhausted or budget.exhausted(self.max_tool_calls, ctx.branch_id)
            available = [tool for name, tool in tool_map.items()
                         if (name != RECONSIDER_TOOL or recovery.pending or recovery.results)
                         and (not recovery.must_switch or name == RECONSIDER_TOOL or name in _DIAGNOSTIC_TOOLS)]
            try:
                definitions = [tool.definition() for tool in available] or None
            except ToolSchemaError as exc:
                if transcript is not None:
                    transcript.extend(msgs[start:])
                yield stop("failed", "tool_schema", str(exc))
                yield {"type": "error", "content": str(exc), "code": "tool_schema"}
                return
            # 最后一轮只允许依据已有结果回答；服务商违规返回工具时会如实终止。
            final_round = not pending_response and not protocol_pending and (
                no_progress or budget_exhausted or (iteration == iteration_limit - 1 and definitions is not None)
            )
            iteration += 1
            limit_code = ("no_progress" if no_progress else "recovery_exhausted" if recovery.exhausted else
                          "tool_budget_exhausted" if budget.exhausted(self.max_tool_calls, ctx.branch_id) else "max_iterations") if final_round else ""
            limit_summary = {"no_progress": "对相同版本输入的重复读取未带来新进展，任务尚未确认全部完成。",
                             "recovery_exhausted": "本轮恢复次数已用尽，任务尚未确认全部完成。",
                             "tool_budget_exhausted": "本轮操作次数已用尽，任务尚未确认全部完成。",
                             "max_iterations": "本轮执行轮数已达到上限，任务尚未确认全部完成。"}.get(limit_code, "")
            if final_round:
                if no_progress:
                    msgs.append({"role": "user", "content": f"对相同版本输入已连续读取{_MAX_IDENTICAL_OBSERVATIONS}次并得到相同结果，本轮停止重复操作。请依据真实结果如实说明已完成与仍未完成的目标，不把重复读取当作计算或交付完成。"})
                if budget_exhausted:
                    msgs.append({"role": "user", "content": RECOVERY_BUDGET_MESSAGE})
                msgs.append({"role": "user", "content": FINAL_ROUND_MESSAGE})
            content, reasoning = "", ""
            calls_acc: dict[int, dict[str, str]] = {}
            # 文件任务要先确认没有违规工具调用再发布正文；普通闲聊保留逐字流式输出。
            # 即使 tool_choice=none，也不能提前把尚未执行的声明当作最终回答。
            live = not tools and not protocol_pending
            streamed_text = False
            suppress = False
            finish_reason = None
            saw_choice = False
            model_started = time.perf_counter()
            model_call = {"call_id": uuid4().hex, "iteration": iteration,
                          "model": getattr(self.llm, "model", None) or getattr(getattr(self.llm, "cfg", None), "model", ""),
                          "budget_calls_before": getattr(getattr(self.llm, "budget", None), "calls", None),
                          "iteration_limit": iteration_limit, **budget.snapshot(self.max_tool_calls, ctx.branch_id)}
            resumed_response, pending_response = pending_response, None
            if not resumed_response:
                projection_budget = ProjectionBudget()
            if runtime is not None and not resumed_response:
                runtime.state["inflight_model"] = model_call
                runtime.state["model_calls_started"] = int(runtime.state.get("model_calls_started", 0)) + 1
                snapshot_loop()
                await runtime.checkpoint()
            if not resumed_response:
                yield {"type": "model_call_start", **model_call}
            try:
                async def response_stream():
                    if resumed_response:
                        return
                    async for item in self.llm.stream_chat(msgs, tools=definitions, tool_choice="none" if final_round else None):
                        yield item

                if resumed_response:
                    content = resumed_response.get("display", "")
                    calls_acc = {index: {"id": call["id"], "name": call["function"]["name"],
                                        "arguments": call["function"]["arguments"]}
                                 for index, call in enumerate(resumed_response["calls"])}
                    finish_reason, saw_choice = "tool_calls", True
                async for chunk in response_stream():
                    choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
                    if choice is None:
                        continue
                    saw_choice = True
                    finish_reason = getattr(choice, "finish_reason", None) or finish_reason
                    delta = choice.delta
                    piece = getattr(delta, "reasoning_content", None)
                    if piece:
                        reasoning += piece
                    if delta.content:
                        content += delta.content
                        suppress = suppress or _TOOL_MARKER in content
                        if live and not suppress:
                            streamed_text = True
                            yield {"type": "text_chunk", "content": delta.content}
                    for tc in delta.tool_calls or []:
                        slot = calls_acc.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["name"] += tc.function.name
                            if tc.function.arguments:
                                slot["arguments"] += tc.function.arguments
                # Also validate substituted clients; the production client validates the transport itself.
                if not saw_choice:
                    raise LLMError("模型服务返回了空响应，本轮没有执行结果。", code="empty_response")
                if finish_reason is None:
                    raise LLMError("模型响应流提前结束，未收到结束原因，本轮响应不完整。", code="stream_incomplete")
                if finish_reason not in {"stop", "tool_calls"}:
                    code = {"length": "output_limit", "content_filter": "content_filter"}.get(finish_reason, "unexpected_finish_reason")
                    raise LLMError("模型响应未正常完成，任务尚未完成。", code=code, finish_reason=finish_reason)
                if finish_reason == "tool_calls" and not calls_acc:
                    raise LLMError("模型声明了工具调用但没有返回实际操作，任务尚未完成。", code="tool_protocol", finish_reason=finish_reason)
                if not content.strip() and not calls_acc:
                    raise LLMError("模型没有返回正文或可执行操作，任务尚未完成。", code="empty_response", finish_reason=finish_reason)
            except asyncio.CancelledError:
                if runtime is not None:
                    runtime.state.pop("inflight_model", None)
                yield {"type": "model_call_done", **model_call, "finish_reason": finish_reason,
                       "status": "cancelled", "code": "cancelled", "summary": "模型调用已取消。",
                       "duration_ms": int((time.perf_counter() - model_started) * 1000)}
                raise
            except Exception as raw:
                exc = translate_error(raw)
                finish_reason = getattr(exc, "finish_reason", None) or finish_reason
                code = getattr(exc, "code", "llm_error")
                # Only the current, unexecuted response is discarded. Earlier
                # tool receipts remain paired in msgs and are never replayed.
                # Live chat text cannot be silently replaced after display.
                retry = (code in _RECOVERABLE_MODEL_ERRORS and not streamed_text
                         and budget.stream_recoveries < stream_retry_limit)
                if runtime is not None:
                    runtime.state.pop("inflight_model", None)
                if retry:
                    budget.stream_recoveries += 1
                    # A failed transport attempt spends the shared model/time
                    # budget, but does not consume a successful decision turn.
                    iteration -= 1
                    snapshot_loop()
                    if runtime is not None:
                        await runtime.checkpoint()
                yield {"type": "model_call_done", **model_call, "finish_reason": finish_reason,
                       "status": "error", "code": code, "summary": str(exc),
                       "will_retry": retry,
                       "duration_ms": int((time.perf_counter() - model_started) * 1000)}
                if retry:
                    yield {"type": "activity", "text": f"模型回复中断，正在自动恢复（{budget.stream_recoveries}/{stream_retry_limit}）"}
                    continue
                if transcript is not None:
                    transcript.extend(msgs[start:])
                yield stop("failed", code, str(exc))
                yield {"type": "error", "content": str(exc), "code": code, "finish_reason": finish_reason}
                return
            if runtime is not None:
                runtime.state.pop("inflight_model", None)
            if not resumed_response:
                yield {"type": "model_call_done", **model_call, "finish_reason": finish_reason, "status": "ok",
                       "code": "model_response", "summary": "模型已返回响应。",
                       "duration_ms": int((time.perf_counter() - model_started) * 1000)}

            # A request accepted during remote inference supersedes its old
            # decision before even the first selected tool can have an effect.
            if await consume():
                if stop_scope in {"step", "agent"}:
                    if transcript is not None:
                        transcript.extend(msgs[start:])
                    yield {"type": "execution_steered", "scope": stop_scope, "revision": runtime.revision}
                    return
                continue

            display = _TOOL_RE.sub("", content).strip()
            reply: dict[str, Any] = {"role": "assistant", "content": display}
            if reasoning:
                reply["reasoning_content"] = reasoning
            calls = [
                {"id": slot["id"] or f"call_{model_call['call_id']}_{idx}", "type": "function",
                 "function": {"name": slot["name"], "arguments": slot["arguments"]}}
                for idx, slot in sorted(calls_acc.items())
            ]
            if not tools:
                calls = []  # 普通闲聊从未提供执行工具，保留正文并忽略服务商多余的调用。
            if runtime is not None:
                snapshot_loop(response={"display": display, "calls": calls} if calls else None)
                await runtime.checkpoint()
            id_errors = _unique_call_ids(calls, msgs)
            forbidden_calls = bool(calls) and (not definitions or final_round)
            if not calls:
                if protocol_pending:
                    # 参数纠错之后仍没有真实调用，不能把自由文本当作完成依据。
                    if display:
                        msgs.append(reply)
                    if budget.protocol_retries >= protocol_retry_limit:
                        if transcript is not None:
                            transcript.extend(msgs[start:])
                        summary = "工具调用参数多次未通过校验，相关操作尚未执行，任务未完成。"
                        yield stop("failed", "tool_protocol", summary)
                        yield {"type": "error", "content": summary, "code": "tool_protocol"}
                        return
                    budget.protocol_retries += 1
                    iteration_limit += 1
                    msgs.append({"role": "user", "content": _PROTOCOL_CORRECTION_MESSAGE})
                    continue
                if not final_round and definitions and not recovery.may_finish and recovery_finish_prompts < 3:
                    # 单一路径失败时先自主恢复；不同方法确实尝试后由 AI 决定是否收尾。
                    # 候选文字尚未展示，可以直接继续恢复，不改变已有内容。
                    if display:
                        msgs.append(reply)
                    msgs.append({"role": "user", "content": AUTONOMOUS_RECOVERY_MESSAGE})
                    recovery_finish_prompts += 1
                    continue
                if tools:
                    yield {"type": "activity", "text": "正在确认任务完成情况"}
                    check_id = uuid4().hex
                    yield {"type": "completion_check_start", **model_call, "call_id": check_id,
                           "purpose": "completion_check"}
                    check = await check_completion(
                        self.llm, msgs, display, scope=scope, results=budget.evidence(ctx.branch_id),
                        eligible_evidence_ids=budget.eligible_evidence(ctx.branch_id, original_scope) if stop_scope == "step" else None,
                    )
                    yield {**check.event(), "call_id": check_id}
                    if not check.completed:
                        if check.verified and check.can_continue and not final_round and completion_retries < MAX_COMPLETION_RETRIES:
                            completion_retries += 1
                            if display:
                                msgs.append(reply)
                                yield {"type": "note", "content": display}
                            msgs.append({"role": "user", "content": continuation(check)})
                            continue
                        code = limit_code or ("premature_stop" if check.verified else "completion_unverified")
                        summary = limit_summary or check.reason
                        if display:
                            msgs.append(reply)
                            yield {"type": "note", "content": display, "retracted": True}
                        if transcript is not None:
                            transcript.extend(msgs[start:])
                        yield stop("incomplete", code, summary)
                        answer = f"本轮尚未确认全部完成。{summary}"
                        if check.remaining:
                            answer += "\n\n仍需完成：" + "；".join(check.remaining)
                        yield {"type": "text_chunk", "content": answer}
                        yield {"type": "final_text", "content": answer}
                        return
                terminal = stop("completed", limit_code or "model_stop", "任务完成情况已核对。" if tools else "本轮回答已结束。",
                                verified=bool(tools))
                if runtime is not None:
                    snapshot_loop(snapshot_messages=msgs + ([reply] if display else []), terminal_events=[
                        terminal, {"type": "final_text", "content": display}])
                    await runtime.checkpoint()
                if display and not live:
                    yield {"type": "text_chunk", "content": display}
                if transcript is not None:
                    transcript.extend(m for m in msgs[start:] if m.get("content") != FINAL_ROUND_MESSAGE)
                    if display:
                        transcript.append(reply)
                yield terminal
                yield {"type": "final_text", "content": display}
                return
            try:
                if not forbidden_calls and ctx.table_filter is None and ctx.tool_group == "core":
                    for call in calls:
                        name = call["function"]["name"]
                        if name not in tool_map:
                            loaded = self.registry.load_named_tool(name, ctx)
                            if loaded is not None:
                                tool_map.update({item.name: item for item in self.registry.for_context(ctx)})
                errors = ([tool_argument_error(call["function"]["name"], "protocol", [
                    {"path": "/", "message": "本轮不允许工具调用，此操作尚未执行，不能作为完成依据。"},
                ]) for call in calls] if forbidden_calls else [
                    id_error or _preflight_call(call, tool_map) for call, id_error in zip(calls, id_errors)
                ])
            except ToolSchemaError as exc:
                # 配置故障无法通过模型纠错解决；整批未执行，仍保留完整成对的对话。
                assistant = {"role": "assistant", "content": display or None, "tool_calls": calls}
                if reasoning:
                    assistant["reasoning_content"] = reasoning
                msgs.append(assistant)
                msgs.extend({"role": "tool", "tool_call_id": call["id"], "content": str(exc)} for call in calls)
                for call in calls:
                    _record_unexecuted_tool(call, ToolResult(str(exc), ok=False, error_kind="schema"), status="rejected")
                if transcript is not None:
                    transcript.extend(msgs[start:])
                yield stop("failed", "tool_schema", str(exc))
                yield {"type": "error", "content": str(exc), "code": "tool_schema"}
                return
            if any(error is not None for error in errors):
                # 整批预检：即使只有一个调用结构错误，本响应也不能产生部分副作用。
                assistant = {"role": "assistant", "content": display or None, "tool_calls": calls}
                if reasoning:
                    assistant["reasoning_content"] = reasoning
                msgs.append(assistant)
                for call, error in zip(calls, errors):
                    name = call["function"]["name"]
                    result = error or tool_argument_error(name, "protocol", [
                        {"path": "/", "message": "同一响应中的其他调用未通过校验，本调用尚未执行，请一起重新发出。"},
                    ])
                    _record_unexecuted_tool(call, result, status="rejected")
                    # 不调用 display/public_params，也不保存脚本或计入业务失败。
                    yield {"type": "tool_call", "id": call["id"], "tool": name,
                           "label": "核对处理参数", "params": {}, "debug_params": {},
                           "error_kind": result.error_kind}
                    yield {"type": "tool_result", "id": call["id"], "tool": name, "ok": False,
                           "summary": result.public_summary, "text": result.public_text,
                           "debug_result": result.text, "duration_ms": 0,
                           "error_kind": result.error_kind, "error_details": result.error_details}
                    msgs.append({"role": "tool", "tool_call_id": call["id"], "content": result.text})
                if forbidden_calls:
                    if transcript is not None:
                        transcript.extend(msgs[start:])
                    summary = "模型在禁止工具调用的收尾阶段仍返回了操作请求，相关操作尚未执行，不能确认任务完成。"
                    yield stop("incomplete", limit_code or "tool_protocol", limit_summary or summary)
                    yield {"type": "error", "content": summary, "code": "tool_protocol"}
                    return
                if budget.protocol_retries >= protocol_retry_limit:
                    if transcript is not None:
                        transcript.extend(msgs[start:])
                    summary = "工具调用参数多次未通过校验，相关操作尚未执行，任务未完成。"
                    yield stop("failed", "tool_protocol", summary)
                    yield {"type": "error", "content": summary, "code": "tool_protocol"}
                    return
                budget.protocol_retries += 1
                protocol_pending = True
                iteration_limit += 1
                msgs.append({"role": "user", "content": _PROTOCOL_CORRECTION_MESSAGE})
                continue
            protocol_pending = False
            if display:
                # 先确定这段文字是过程说明，再按顺序展示一次。
                yield {"type": "note", "content": display}
            if "delete_recipes" in tool_map:
                calls = _coalesce_recipe_delete_calls(calls)
            assistant: dict[str, Any] = {"role": "assistant", "content": display or None, "tool_calls": calls}
            if reasoning:
                assistant["reasoning_content"] = reasoning
            msgs.append(assistant)
            assistant_position = len(msgs) - 1

            end_turn = False
            guidance: list[str] = []
            prepared_parallel: set[str] = set()
            for call_index, call in enumerate(calls):
                in_parallel = bool(parallel_batch and call["id"] in parallel_batch["call_ids"])
                if call_index and not in_parallel and await consume(assistant, call_index):
                    if stop_scope in {"step", "agent"}:
                        if transcript is not None:
                            transcript.extend(msgs[start:])
                        yield {"type": "execution_steered", "scope": stop_scope, "revision": runtime.revision}
                        return
                    break
                if not in_parallel and (no_progress or budget.exhausted(self.max_tool_calls, ctx.branch_id) or recovery.exhausted):
                    assistant["tool_calls"] = calls[:call_index]
                    break
                if not in_parallel:
                    remaining_budget = self.max_tool_calls - budget.used
                    if ctx.branch_id in budget.branch_limits:
                        remaining_budget = min(remaining_budget, budget.branch_limits[ctx.branch_id]
                                               - budget.branch_used.get(ctx.branch_id, 0))
                    candidates = independent_batch(calls, call_index, tool_map, ctx,
                                                   min(self.max_parallel_tools, remaining_budget))
                    if len(candidates) > 1:
                        parallel_batch = {"call_ids": [item["id"] for item in candidates], "outcomes": {},
                                          "sources": {item["id"]: snapshot_inputs(
                                              ctx, item["function"]["name"], _parse_args(item["function"]["arguments"]))
                                              for item in candidates}, "visible_events": []}
                        reserved_call_ids.update(parallel_batch["call_ids"])
                        budget.used += len(candidates)
                        if ctx.branch_id:
                            budget.branch_used[ctx.branch_id] = budget.branch_used.get(ctx.branch_id, 0) + len(candidates)
                        in_parallel = True
                if in_parallel and call["id"] not in prepared_parallel:
                    batch_calls = [item for item in calls[call_index:] if item["id"] in parallel_batch["call_ids"]]

                    def snapshot_batch() -> None:
                        # Keep only fully paired messages in the checkpoint.
                        # The pending response owns every not-yet-paired call.
                        prefix = durable_value(msgs)
                        if call_index:
                            prefix[assistant_position]["tool_calls"] = calls[:call_index]
                        else:
                            prefix.pop(assistant_position)
                        snapshot_loop(response={"display": "", "calls": calls[call_index:]}, snapshot_messages=prefix)

                    snapshot_batch()
                    if runtime:
                        await runtime.checkpoint()
                    pending_calls = []
                    for item in batch_calls:
                        item_args = _parse_args(item["function"]["arguments"])
                        item_tool = tool_map[item["function"]["name"]]
                        debug = externalize_script_references(item_args)
                        yield {"type": "tool_call", "id": item["id"], "tool": item_tool.name,
                               "label": item_tool.display(item_args),
                               "params": item_tool.public_params(item_args) if item_tool.public_params else debug,
                               "debug_params": debug}
                        if item["id"] in parallel_batch["outcomes"]:
                            if runtime:
                                # A sibling checkpoint can retain a real result
                                # even when its receipt transaction failed.
                                # Finishing the same receipt is idempotent.
                                snapshot_batch()
                                await runtime.finished(item["id"], parallel_batch["outcomes"][item["id"]]["result"])
                            continue
                        cached = await runtime.started(item["id"], item_tool.name, item_args) if runtime else None
                        if cached is not None:
                            parallel_batch["outcomes"][item["id"]] = {"result": cached, "events": [], "duration_ms": 0}
                            snapshot_batch()
                            if runtime:
                                await runtime.checkpoint()
                        else:
                            pending_calls.append(item)

                    names = {item["id"]: item["function"]["name"] for item in batch_calls}

                    async def save_outcome(outcome: ParallelOutcome) -> None:
                        payload = asdict(outcome.result)
                        payload["events"] = [*outcome.events, *payload["events"]]
                        projected = project_tool_result(ctx, names[outcome.call_id], outcome.call_id,
                                                        payload, budget=projection_budget)
                        parallel_batch["outcomes"][outcome.call_id] = {
                            "result": projected, "events": [], "duration_ms": outcome.duration_ms}
                        snapshot_batch()
                        if runtime:
                            await runtime.finished(outcome.call_id, projected)

                    await run_parallel_tools(pending_calls, tool_map, ctx, save_outcome)
                    prepared_parallel.update(item["id"] for item in batch_calls)
                if call["id"] not in reserved_call_ids:
                    budget.used += 1
                    if ctx.branch_id:
                        budget.branch_used[ctx.branch_id] = budget.branch_used.get(ctx.branch_id, 0) + 1
                prefetched = parallel_batch["outcomes"][call["id"]] if in_parallel else None
                result: ToolResult | None = None
                executed = False
                name = call["function"]["name"]
                args = _parse_args(call["function"]["arguments"])
                tool = tool_map.get(name)
                sources = parallel_batch["sources"][call["id"]] if in_parallel else snapshot_inputs(ctx, name, args)
                cached_result = await runtime.started(call["id"], name, args or {}) if runtime and not in_parallel else None
                visible_events: list[dict[str, Any]] = list(prefetched["events"]) if prefetched else []
                debug_params: Any = args if args is not None else call["function"]["arguments"]
                script_ref: dict[str, Any] = {}
                if name == _SCRIPT_TOOL and args is not None:
                    try:
                        request = ScriptRequest.model_validate(args)
                        prepared = script_params(args, title=getattr(request, "title", "") or request.description)
                        script_ref = {key: prepared[key] for key in ("script_id", "script_title", "script_filename")}
                        args = {**args, "script_id": script_ref["script_id"]}
                        debug_params = prepared
                    except (ValueError, OSError):
                        debug_params = externalize_script_references(args, python=True)
                elif name == _SCRIPT_TOOL:
                    debug_params = "工具参数不是合法的 JSON，未保存源码"
                else:
                    debug_params = externalize_script_references(debug_params)
                if not in_parallel:
                    yield {"type": "tool_call", "id": call["id"], "tool": name,
                           "label": tool.display(args or {}) if tool else name,
                           "params": {**(tool.public_params(args or {}) if tool and tool.public_params else debug_params if isinstance(debug_params, dict) else {}), **script_ref},
                           "debug_params": debug_params, **script_ref}
                started = time.perf_counter()
                script_key = None
                attempt = None
                repeated_script = False
                method_blocked = False
                if prefetched is not None:
                    result = ToolResult(**prefetched["result"])
                    executed = True
                elif cached_result is not None:
                    result = ToolResult(**cached_result)
                    executed = True
                elif tool is None:
                    result = ToolResult(f"没有工具 `{name}`。可用工具：{', '.join(tool_map)}", ok=False)
                elif args is None:
                    result = ToolResult("工具参数不是合法的 JSON 对象，请重新调用。", ok=False)
                elif recovery.must_switch and name != RECONSIDER_TOOL and name not in _DIAGNOSTIC_TOOLS:
                    method_blocked = True
                    result = ToolResult(
                        _approach_switch_message(recovery), ok=False, summary="当前方法已达到失败阈值，须先调整方法",
                        public_summary="正在调整处理方法", public_text="当前方法多次未通过，将重新选择处理方法。",
                    )
                else:
                    if name == _SCRIPT_TOOL:
                        script_key = await asyncio.to_thread(_script_key, args, ctx)
                        attempt = budget.script_attempts.get((ctx.branch_id, script_key)) if script_key else None
                        if attempt is None and script_key and args.get("input_tables") is None and not args.get("input_files"):
                            attempt = budget.script_attempts.get((ctx.branch_id, "default:" + script_key))
                    # 成功结果直接复用；失败允许一次原样重试以容纳暂时性故障。
                    repeated_script = attempt is not None and (attempt.result.ok or attempt.executions >= 2)
                    if repeated_script:
                        result = ToolResult(
                            REPEATED_SCRIPT_MESSAGE
                            + (f"\n原始执行依据 evidence_id={attempt.evidence_id}；请引用该依据核对完成情况。" if attempt.evidence_id else "")
                            + "\n上次实际执行的反馈：\n" + attempt.result.text,
                            ok=attempt.result.ok, summary="相同代码与输入已执行，复用上次结果",
                            public_summary="已读取已有处理结果", public_text="正在根据已有结果核对处理方法。",
                            public_details=attempt.result.public_details,
                        )
                    else:
                        executed = True
                        async for item in execute_tool(tool, args, ctx, call_id=call["id"]):
                            if isinstance(item, ToolResult):
                                result = item
                            else:
                                if runtime:
                                    visible_events.append({**item, "tool_call_id": call["id"]})
                                else:
                                    yield {**item, "tool_call_id": call["id"]}
                assert result is not None
                payload = asdict(result)
                payload["events"] = [*visible_events, *payload["events"]]
                safe_payload, _ = sanitize_tool_payload(payload)
                original_events = safe_payload["events"]
                original_tables = safe_payload["tables"]
                if prefetched is None and cached_result is None:
                    result = ToolResult(**project_tool_result(ctx, name, call["id"], payload, budget=projection_budget))
                output_reference = result_reference(result.text)
                if output_reference and (prefetched is not None or cached_result is not None):
                    original_events = ctx.host.resolve_tool_result_events(output_reference, branch_id=ctx.branch_id)
                    original_tables = []  # The durable resolver also restores native table previews.
                visible_events = []
                if name == "load_tool_group" and result.ok:
                    tool_map.update({item.name: item for item in self.registry.for_context(ctx)})
                if not executed:
                    _record_unexecuted_tool(
                        call, result, status="reused" if repeated_script else "blocked" if method_blocked else "rejected",
                        arguments=args, duration_ms=int((time.perf_counter() - started) * 1000),
                    )
                if script_key and not repeated_script:
                    if attempt is None:
                        attempt = _ScriptAttempt(result)
                    else:
                        attempt.result = result
                        attempt.executions += 1
                    budget.script_attempts[(ctx.branch_id, script_key)] = attempt
                    if args.get("input_tables") is None and not args.get("input_files"):
                        # 默认输入会自动包含刚产生的中间表；它们不能使原脚本绕过去重。
                        # 显式选择这些表、或之后由其他工具改变输入，仍会得到新的指纹。
                        after_key = await asyncio.to_thread(_script_key, args, ctx)
                        if after_key:
                            budget.script_attempts[(ctx.branch_id, "default:" + after_key)] = attempt
                for table in original_tables:
                    visible_events.append({"type": "table", "tool_call_id": call["id"], "table": table,
                                           **({"_tool_result_ref": {"result_id": output_reference, "branch_id": ctx.branch_id}}
                                              if output_reference else {})})
                for extra in original_events:
                    visible_events.append({**extra, "tool_call_id": call["id"],
                                           **({"_tool_result_ref": {"result_id": output_reference, "branch_id": ctx.branch_id}}
                                              if output_reference else {})})
                public_text = externalize_script_text(
                    result.public_text if result.public_text is not None else result.text,
                    python=name in {_SCRIPT_TOOL, "inspect_python_script"},
                )
                evidence = tool_evidence(ctx, name, args, result, call_id=call["id"], sources=sources,
                                         executed=executed, reused=repeated_script)
                if repeated_script and attempt is not None and attempt.evidence_id:
                    evidence["reused_from"] = attempt.evidence_id
                elif executed and script_key and attempt is not None:
                    attempt.evidence_id = evidence["id"]
                evidence_record = {"text": externalize_script_text(result.text, python=name == _SCRIPT_TOOL),
                                   "summary": result.summary, "args": externalize_script_references(args), "facts": evidence}
                ctx.evidence_store[evidence["id"]] = evidence_record
                visible_events.append({"type": "tool_result", "id": call["id"], "tool": name, "ok": result.ok,
                       "evidence": evidence,
                       "evidence_record": evidence_record,
                       "summary": result.public_summary if result.public_summary is not None else result.summary or result.text[:200],
                       "text": public_text[:3000],
                       **({"details": result.public_details} if result.public_details else {}),
                       "debug_result": externalize_script_text(result.text, python=name in {_SCRIPT_TOOL, "inspect_python_script"}),
                       "duration_ms": prefetched["duration_ms"] if prefetched else int((time.perf_counter() - started) * 1000)})
                msgs.append({"role": "tool", "tool_call_id": call["id"],
                             "content": (f"[evidence_id={evidence['id']}]\n"
                                         f"[execution_receipt status={evidence['status']} "
                                         f"executed={str(evidence['executed']).lower()} "
                                         f"reused={str(evidence['reused']).lower()}]\n{result.text}")})
                budget.results[evidence["id"]] = {"tool": name, "ok": result.ok, "branch_id": ctx.branch_id, "scope": original_scope,
                                              "call_id": call["id"], "evidence": evidence,
                                              "result": externalize_script_text(result.text, python=name == _SCRIPT_TOOL)}
                for invalid_id in recovery.invalid_results:
                    for evidence_id, item in budget.results.items():
                        if item.get("branch_id") == ctx.branch_id and invalid_id in {evidence_id, item.get("call_id")}:
                            item["invalid"] = True
                end_turn = end_turn or result.end_turn
                if name != RECONSIDER_TOOL and not method_blocked:
                    recovery.observe(name, result, call_id=call["id"], diagnostic=name in _DIAGNOSTIC_TOOLS,
                                     reused=repeated_script, evidence_id=evidence["id"], result_events=original_events)
                if executed:
                    if name != RECONSIDER_TOOL:
                        recovery_finish_prompts = 0
                    observation = _read_observation_key(name, args, sources, result)
                    no_progress = read_progress.observe(observation)
                explored += name in _EXPLORE_TOOLS
                built = built or name in _RECIPE_TOOLS
                if name == _SCRIPT_TOOL and tool is not None:
                    if not script_budget_added:
                        iteration_limit += _SCRIPT_EXTRA_ITERATIONS
                        script_budget_added = True
                    # 脚本中间表或打印完成不算修改交付，实际文件可替代 Recipe 完成交付。
                    built = built or (result.ok and any(event.get("type") == "file" and event.get("file_id")
                                                       for event in result.events))
                if not result.ok and name != RECONSIDER_TOOL and not method_blocked:
                    failure_text = re.sub(r"\brun_id=[A-Za-z0-9_]+", "run_id=<本次运行>", result.summary or result.text)
                    key = (name, failure_text[:1000])
                    failures[key] = failures.get(key, 0) + 1
                    if failures[key] == 2:
                        guidance.append(REPEATED_FAILURE_MESSAGE)
                    elif name == _SCRIPT_TOOL:
                        guidance.append(SCRIPT_RETRY_MESSAGE)
                    elif _SCRIPT_TOOL in tool_map and not fallback_suggested:
                        guidance.append(TOOL_FALLBACK_MESSAGE)
                        fallback_suggested = True
                if (executed and not result.ok and not result.end_turn and cached_result is None
                        and not repeated_script and not method_blocked and name != RECONSIDER_TOOL
                        and result.error_kind not in {"schema", "protocol"}):
                    technical_error = "\n".join(str(safe_payload.get(key) or "") for key in ("summary", "text"))
                    kind = classify_failure(name, technical_error)
                    reflection_key = (name, kind) if kind else None
                    if reflection_key is not None and reflection_key not in reflected_failures:
                        experience_text = ""
                        retrieve_experience = getattr(ctx.host, "repair_experience_text", None)
                        if callable(retrieve_experience):
                            try:
                                experience_text = retrieve_experience(f"{name} {kind}")
                                if inspect.isawaitable(experience_text):
                                    experience_text = await experience_text
                                if not isinstance(experience_text, str):
                                    experience_text = ""
                            except Exception:
                                # Experience is optional; its availability
                                # must never interrupt the user's work.
                                experience_text = ""
                        pending_reflections.append(failure_guidance(name, technical_error, experience_text))
                        reflected_failures.add(reflection_key)
                if recovery.must_switch and not recovery.exhausted:
                    guidance.append(_approach_switch_message(recovery))
                ready_events = visible_events
                if in_parallel:
                    parallel_batch["visible_events"].extend(_checkpoint_tool_events(visible_events))
                    reserved_call_ids.discard(call["id"])
                    if any(identifier in reserved_call_ids for identifier in parallel_batch["call_ids"]):
                        ready_events = []
                    else:
                        ready_events = _hydrate_tool_events(ctx, parallel_batch["visible_events"])
                        parallel_batch = None
                if runtime is not None:
                    saved_messages = durable_value(msgs)
                    saved_messages[assistant_position]["tool_calls"] = calls[:call_index + 1]
                    remaining = [] if end_turn and not parallel_batch else calls[call_index + 1:]
                    snapshot_loop(response={"display": "", "calls": remaining} if remaining else None,
                                  snapshot_messages=saved_messages)
                    if ready_events:
                        runtime.state["last_tool_events"] = durable_value(_checkpoint_tool_events(ready_events))
                    if in_parallel:
                        await runtime.checkpoint()
                    else:
                        await runtime.finished(call["id"], asdict(result))
                for event in ready_events:
                    yield event
                if end_turn and not parallel_batch:
                    # 等待用户回答时立即停止，后续调用不能覆盖确认或产生额外副作用。
                    # 只保留实际执行过的调用，保证计划模式接续时工具消息成对。
                    assistant["tool_calls"] = calls[:call_index + 1]
                    break
            msgs.extend({"role": "user", "content": message} for message in guidance)
            msgs.extend({"role": "user", "content": message} for message in pending_reflections)
            pending_reflections.clear()
            if end_turn:
                if transcript is not None:
                    transcript.extend(msgs[start:])
                # 工具前的说明已经作为 note 交付；确认问题由 clarify 卡片展示。
                yield stop("waiting", "awaiting_user", "需要用户回答后继续处理。")
                yield {"type": "final_text", "content": ""}
                return
            if ctx.mode == "modify" and not built and not nudged and explored >= _NUDGE_AFTER:
                # 收束没有产出修改文件的探索；由模型选择 Recipe 或 Python 完成交付。
                msgs.append({"role": "user", "content": MODIFY_NUDGE.format(count=explored)})
                nudged = True
            if runtime is not None:
                snapshot_loop()
                await runtime.checkpoint()

        if transcript is not None:
            transcript.extend(msgs[start:])
        yield stop("incomplete", "max_iterations", "本轮执行轮数已达到上限，任务尚未完成。")


def _unique_call_ids(calls: list[dict[str, Any]], messages: list[dict[str, Any]]) -> list[ToolResult | None]:
    """Reject duplicate provider IDs before side effects and retain valid pairs.

    A replacement ID belongs only to the rejected protocol receipt. It does
    not turn the invalid request into an executable call.
    """
    seen = {call.get("id") for item in messages for call in item.get("tool_calls") or []}
    errors = []
    for call in calls:
        identifier = call["id"]
        if identifier in seen:
            call["id"] = "call_" + uuid4().hex
            errors.append(tool_argument_error(call["function"]["name"], "protocol", [
                {"path": "/id", "message": "工具调用 ID 重复，本批调用尚未执行；请使用新的唯一 ID 重新发出。"},
            ]))
        else:
            errors.append(None)
        seen.add(identifier)
        seen.add(call["id"])
    return errors


def _read_observation_key(name: str, args: dict[str, Any] | None, sources: list[dict[str, Any]],
                          result: ToolResult) -> str | None:
    """Convergence is limited to successful, versioned reads, never writes."""
    if name not in _READ_OBSERVATION_TOOLS or not result.ok or not sources:
        return None
    if name == "run_sql" and (args or {}).get("result_name"):
        return None  # A named query materializes a new table, so it is a write.
    value = {"tool": name, "args": args, "sources": sources, "result": result.text}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def _script_key(args: dict[str, Any], ctx: AgentContext) -> str | None:
    """只比较代码和实际声明的输入版本，描述变化不构成新尝试。"""
    try:
        request = ScriptRequest.model_validate(args)
    except ValueError:
        return None  # 参数错误仍由工具按原流程反馈，不能复用此前的有效调用。
    visible = {table.name: table for table in ctx.visible_tables()}
    names = request.input_tables
    if names is None:
        names = [] if request.input_files else [table.name for table in visible.values() if table.role != "expected"]
    files = []
    for alias, file_id in request.input_files.items():
        try:
            resolve = getattr(ctx.host, "original_file_path", None)
            path = resolve(file_id) if callable(resolve) else None
            if path is None:
                return None  # Unknown input versions cannot safely share a cached execution.
            digest = hashlib.sha256()
            with Path(path).open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            files.append((alias, file_id, digest.hexdigest()))
        except Exception:
            return None  # Let the tool report its actual access/read failure.
    payload = {
        "code": request.code,
        "tables": [(name, visible[name].revision, visible[name].file_version) if name in visible else (name, None, None)
                   for name in names],
        "files": files,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _approach_switch_message(recovery: RecoveryState) -> str:
    return APPROACH_SWITCH_MESSAGE.format(
        method=recovery.current.name, failures=recovery.current.failures, limit=METHOD_FAILURE_LIMIT,
        remaining=MAX_METHODS - len(recovery.methods),
    )


def _record_unexecuted_tool(call: dict[str, Any], result: ToolResult, *, status: str,
                            arguments: dict[str, Any] | None = None, duration_ms: int = 0) -> None:
    """未进入 execute_tool 的调用仍保留参数和最终反馈，避免误判为实际执行。"""
    if arguments is None:
        arguments = _parse_args(call["function"]["arguments"])
    record_event(
        "tool.result", "sheetmate.agent.executor.AgentExecutor.agent_stream",
        {"arguments": arguments if arguments is not None else call["function"]["arguments"], "result": result},
        tool=call["function"]["name"], call_id=call["id"], executed=False,
        status=status, duration_ms=duration_ms,
    )


def _parse_args(raw: str) -> dict[str, Any] | None:
    try:
        value = parse_json(raw, allow_text=False)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


_PROTOCOL_CORRECTION_MESSAGE = (
    "上一轮工具调用未通过协议或参数结构校验，该响应中的全部调用均未执行。"
    "请根据各条工具反馈及工具 JSON Schema，仅修正工具名称、JSON 语法、字段、类型、枚举或必填结构，"
    "并重新发出全部仍需执行的调用（包括同批被暂缓的调用）。"
    "不要猜测或替换列名、金额、业务规则等语义，不要从自由文本中提取执行动作，"
    "也不要以文字回复声称这些操作已经完成。"
)


def _preflight_call(call: dict[str, Any], tool_map: dict[str, Tool]) -> ToolResult | None:
    """任何显示、脚本保存或执行副作用之前检查原始调用。"""
    name = call["function"]["name"]
    tool = tool_map.get(name)
    if tool is None:
        return tool_argument_error(name, "protocol", [
            {"path": "/name", "message": f"未知工具，请使用可用工具：{', '.join(tool_map)}"},
        ])
    args = _parse_args(call["function"]["arguments"])
    if args is None:
        return tool_argument_error(name, "protocol", [
            {"path": "/arguments", "message": "参数必须是完整 JSON 对象，不能包含说明文字或非对象值。"},
        ])
    try:
        validate_tool_args(tool, args)
    except StructuredOutputError as exc:
        return tool_argument_error(name, "schema", exc.errors)
    return None


def _coalesce_recipe_delete_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一次模型响应的删除目标共用一份待确认清单，避免后一次覆盖前一次。"""
    deletion_indexes: list[int] = []
    recipe_ids: list[str] = []
    delete_all = False
    for index, call in enumerate(calls):
        name = call["function"]["name"]
        if name not in {"delete_recipe", "delete_recipes"}:
            continue
        args = _parse_args(call["function"]["arguments"])
        if args is None:
            continue
        if name == "delete_recipe":
            recipe_id = str(args.get("recipe_id") or "")
            if not recipe_id:
                continue
            recipe_ids.append(recipe_id)
        elif args.get("all") is True:
            delete_all = True
        else:
            targets = args.get("recipe_ids")
            if not isinstance(targets, list) or not targets or not all(isinstance(item, str) and item for item in targets):
                continue
            recipe_ids.extend(targets)
        deletion_indexes.append(index)
    if not deletion_indexes:
        return calls

    first_index = deletion_indexes[0]
    deletion_set = set(deletion_indexes)
    merged = calls[first_index]
    if len(deletion_indexes) > 1:
        args = {"all": True} if delete_all else {"recipe_ids": list(dict.fromkeys(recipe_ids))}
        merged = {**merged, "function": {"name": "delete_recipes", "arguments": json.dumps(args, ensure_ascii=False)}}

    result = []
    for index, call in enumerate(calls):
        if index == first_index:
            result.append(merged)
        elif index in deletion_set:
            continue
        else:
            # 删除工具已经负责确认；仅移除模型同时提出的同一个删除确认问题。
            if call["function"]["name"] == "ask_user":
                args = _parse_args(call["function"]["arguments"]) or {}
                question = str(args.get("question") or "")
                options = args.get("options")
                if ("删除" in question and ("流程" in question or "recipe" in question.lower())
                        and isinstance(options, list) and "确认删除" in options and "取消" in options):
                    continue
            result.append(call)
    return result


# ---------------------------------------------------------------------------
# 系统提示
# ---------------------------------------------------------------------------


def build_customer_block(ctx: AgentContext) -> str:
    """客户身份独立于聊天历史和所选流程，每轮从宿主提供的最新资料构建。"""
    if not ctx.customer:
        return "未绑定客户：本会话的数据和会话记忆只属于这个会话。用户以后可以把会话保存到某个客户名下；不要根据文件名猜测客户身份。"
    customer = ctx.customer
    lines = [f"已绑定客户「{customer['name']}」：这是本轮的明确客户身份，不需要用户重复介绍。"]
    if customer.get("code"):
        lines.append(f"客户编号：{customer['code']}")
    if customer.get("description"):
        lines.append(f"客户说明：{customer['description']}")
    summary = (customer.get("summary") or "").strip()
    if summary:
        lines.append("已保存的客户处理说明（派生摘要，具体条件以原始记忆和当前用户要求为准）：\n" + summary)
    if customer.get("recipe_id"):
        lines.append(f"当前流程：{customer.get('recipe_name') or '已选择的流程'}（ID：{customer['recipe_id']}），使用这个流程的资料、字段映射和记忆。")
    recipe_summary = (customer.get("recipe_summary") or "").strip()
    if recipe_summary:
        lines.append("当前流程的处理逻辑：\n" + recipe_summary)
    lines.append("这些客户资料和下方已保存记忆由应用长期存储，每轮按当前客户绑定读取；同一客户的新旧对话都可以使用，与本轮是否执行表格任务无关。")
    return "\n".join(lines)


def _recipe_context_blocks(ctx: AgentContext, message: str, recipes: list[dict[str, Any]]) -> list[ContextBlock]:
    """Keep a small relevant directory ahead of large reference facts.

    This is a directory, not the recipe implementation. Exact IDs and versions
    remain intact; long descriptions and input/match lists advertise excerpts.
    Detailed steps are still fetched through ``get_recipe`` on demand.
    """
    if not recipes:
        return []

    def excerpt(value: Any, chars: int) -> str:
        text = " ".join(str(value or "").split())
        return text if len(text) <= chars else text[:chars] + "…（片段）"

    def line(recipe: dict[str, Any]) -> str:
        inputs = list(recipe.get("inputs") or [])
        inputs_text = "、".join(excerpt(value, 40) for value in inputs[:4])
        if len(inputs) > 4:
            inputs_text += f"（另 {len(inputs) - 4} 项，按 ID 读取）"
        return (f"- recipe_id={recipe['recipe_id']}「{excerpt(recipe.get('name'), 80)}」v{recipe['version']}："
                f"{excerpt(recipe.get('description'), 80)} 输入 {inputs_text or '（无）'}，{recipe['step_count']} 步"
                + (f"；与当前表匹配：{excerpt(recipe['match'], 180)}" if recipe.get("match") else ""))

    current = (ctx.customer or {}).get("recipe_id") or getattr(ctx.host, "recipe_id", None)
    query = message.casefold()

    def relevance(recipe: dict[str, Any]) -> int:
        if recipe["recipe_id"] == current:
            return 3
        name = str(recipe.get("name") or "").strip().casefold()
        if str(recipe["recipe_id"]).casefold() in query or (len(name) >= 2 and name in query):
            return 2
        return 1 if recipe.get("match") else 0

    ranked = sorted(recipes, key=relevance, reverse=True)
    preferred = [recipe for recipe in ranked if relevance(recipe)]
    selected_ids: set[str] = set()

    def directory(items: list[dict[str, Any]], *, source: str, heading: str,
                  budget: int, priority: int) -> ContextBlock:
        note = "目录仅作索引；具体步骤用 get_recipe 按 ID 读取，其他流程用 list_recipes 查看。"
        lines = [heading, note]
        used = estimate_tokens("\n".join(lines)) + 100  # Reserve the omission receipt.
        selected = []
        for recipe in items:
            rendered = line(recipe)
            size = estimate_tokens(rendered) + 1
            if used + size > budget:
                continue
            lines.append(rendered)
            selected.append(str(recipe["recipe_id"]))
            selected_ids.add(str(recipe["recipe_id"]))
            used += size
        omitted = len(items) - len(selected)
        if omitted:
            lines.append(f"（本段目录省略 {omitted} 项；流程原文仍保存，调用 list_recipes / get_recipe 按需读取。）")
        return ContextBlock(source, "\n".join(lines), priority=priority, references=tuple(selected),
                            retrieve="调用 list_recipes / get_recipe 按需读取流程。")

    blocks = []
    if preferred:
        blocks.append(directory(preferred, source="relevant_recipes", heading="## 当前或相关的处理流程（Recipe，优先复用）",
                                budget=2_000, priority=90))
    remaining = [recipe for recipe in ranked if str(recipe["recipe_id"]) not in selected_ids]
    if remaining:
        blocks.append(directory(remaining, source="recipes", heading="## 其他已有的处理流程（Recipe 目录片段）",
                                budget=1_000, priority=30))
    return blocks


def build_system_prompt(ctx: AgentContext, message: str, *, thinking: bool = False) -> str:
    tables = ctx.visible_tables()
    table_lines = []
    for t in tables:
        cols = ", ".join(t.column_names[:14]) + (f" …（共 {len(t.columns)} 列）" if len(t.columns) > 14 else "")
        tag = _ROLE_TAGS.get(t.role, "")
        if t.created_by == "create_workbook":
            tag += f"，建表生成，file_id={t.file_id}（可查看布局并继续调整）"
        elif t.created_by == _SCRIPT_TOOL:
            tag += f"，脚本生成，file_id={t.file_id}（结果表可继续用公式或 SQL；文件副本可供必要的脚本处理）"
            script_run_id = (t.header or {}).get("script_run_id")
            if not script_run_id:
                match = re.search(r"run_id=([A-Za-z0-9_]+)", t.description or "")
                script_run_id = match.group(1) if match else None
            if script_run_id:
                tag += f"，run_id={script_run_id}"
        table_lines.append(AGENT_TABLE_LINE.format(name=t.name, label=t.label, rows=t.rows, cols=len(t.columns),
                                                   tag=tag, columns=cols))
    available_files = ctx.host.list_files()
    if ctx.table_filter is not None:
        allowed_file_ids = {table.file_id for table in tables if table.file_id}
        available_files = [item for item in available_files if item.get("file_id") in allowed_file_ids]
    files = [f for f in available_files if not f.get("tables")]
    replacements = [{"file_id": file["file_id"], "filename": file["filename"]} for file in available_files
                    if file["file_id"] in ctx.replace_file_ids]
    saving_rule = (
        "\n本轮更新这些已有结果：" + json.dumps(replacements, ensure_ascii=False)
        + "。基于用户指定对象继续调整或重算，沿用结果名称；新结果核对成功后由系统替换当前交付，不自行删除文件。"
        if replacements else "\n本轮未选定替换目标；需要生成文件时作为独立结果交付，已有适用文件直接使用。"
    )
    file_lines = [f"- file_id={f['file_id']}「{f['filename']}」（{f.get('scope_label', '')}）"
                  + ("，图片附件（非工作簿，无工作表）" if (f.get("media_type") or "").startswith("image/") else "")
                  + (f"，脚本 run_id={f['script_run_id']}（可查看脚本后继续调整）" if f.get("script_run_id") else "")
                  for f in files]
    recipes = ctx.host.list_recipes() if ctx.table_filter is None else []
    recipe_blocks = _recipe_context_blocks(ctx, message, recipes)
    formula_menu = ""
    if ctx.table_filter is None and ctx.tool_group == "core" and not (ctx.enabled_tool_groups & {"formulas", "all"}):
        specs = [*ctx.library.customer_specs(), *ctx.library.builtin_specs()]
        formula_menu = "\n\n可按需加载的公式（load_tool_group group=formulas）：" + "、".join(
            f"{spec.id}（{spec.name}）" for spec in specs if spec.id != "custom_sql")[:1600]
    policy = AGENT_SYSTEM.partition("\n## 客户\n")[0].format(
        mode_label={"analyze": "分析", "modify": "修改", "create": "新建"}.get(ctx.mode, "分析"),
        mode_rules=MODE_RULES.get(ctx.mode, MODE_RULES["analyze"]) + (
            "\n本轮用户已明确要求保存或更新重复流程，可在处理完成后保存。"
            if ctx.save_recipe_requested else
            "\n本轮未授权保存重复流程：只完成本次处理，不调用 save_as_recipe，不保存或更新流程，也不主动询问是否保存。"
        ) + (
            "\n用户已经确认删除这些流程 ID：" + "、".join(sorted(ctx.delete_recipe_ids)) + "。直接执行，不重复询问。"
            if ctx.delete_recipe_ids else
            "\n用户要求删除流程时，直接调用 delete_recipe 或 delete_recipes，由工具列出目标并请求一次确认。删除全部流程用 delete_recipes(all=true)。不需要开启额外授权，确认后系统直接执行。"
        ) + saving_rule,
    )
    task = ctx.task_state_block or task_projection(ctx.task_state)
    scope = "table_specialist" if ctx.table_filter is not None else "conversation"
    stage = "specialist" if ctx.table_filter is not None else "planning" if thinking else "execution"
    blocks = [
        ContextBlock("system_policy", policy + (THINKING_RULES if thinking else ""), required=True),
        ContextBlock("customer", "## 客户\n" + build_customer_block(ctx), required=True),
        ContextBlock("business_memory", "## 客户与当前流程的长期记忆（用户维护的规则优先于 AI 参考）\n" + (ctx.host.memory_text(message) or "（没有）"), required=True),
        ContextBlock("task_requirements", "## 当前任务状态\n" + task if task else "", required=True,
                     references=tuple(str(value) for value in ctx.task_state.get("source_message_ids", []))),
        ContextBlock("tables", "## 工作区里的表（调用工具时用「表名」）\n" + ("\n".join(table_lines) or (
                     "（还没有表，可直接根据用户文字或资料新建工作簿）" if ctx.mode == "create" else
                     "（还没有表；可回答问题，需要处理现有数据时再加载 Excel / CSV）")), priority=80,
                     scope=scope, references=tuple(t.name for t in tables), retrieve="调用 table_info / preview_table 读取当前版本结构。"),
        ContextBlock("files", "## 可加载的文件\n" + ("\n".join(file_lines) or "（没有）"), priority=50,
                     scope=scope, references=tuple(str(f["file_id"]) for f in files), retrieve="调用 list_files 按范围查找可访问原文件。"),
        ContextBlock("table_memory", table_probe_context(ctx.ws, tables, ctx.file_memory if ctx.table_filter is None else "", query=message),
                     priority=70, scope=scope, references=tuple(t.name for t in tables), retrieve="调用 table_info 或具体统计工具补读当前版本依据。"),
        *recipe_blocks,
        ContextBlock("formula_catalog", formula_menu, priority=10, retrieve="调用 load_tool_group(group='formulas') 加载完整算子。"),
    ]
    return assemble_context(blocks, stage=stage)
