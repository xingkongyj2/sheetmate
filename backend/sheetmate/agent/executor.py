"""ReAct 工具循环（移植自 TabClaw agent/executor.py）。

一轮循环：流式调用 LLM → 收集文本增量和 tool_call 分片 → 有工具调用就执行工具、把结果作为 role=tool 回传 → 再调 LLM；
没有工具调用时，当前文本就是最终回答。默认最多 max_iterations 轮；使用脚本时增加有界的修正轮数。

保留了 TabClaw 的两个细节：
- 工具调用参数是分片流式返回的，按 index 拼接完整后再 json.loads
- DeepSeek 偶尔把内部工具标记 <｜tool▁call▁begin｜> 泄漏到正文，检测到后不再显示，并在最终文本里清掉
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from sheetmate.agent.content import model_history, user_content
from sheetmate.agent.llm import LLMError
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
    TOOL_FALLBACK_MESSAGE,
)
from sheetmate.agent.recovery import MAX_METHODS, METHOD_FAILURE_LIMIT, RECONSIDER_TOOL, RecoveryState
from sheetmate.agent.scripting import ScriptRequest
from sheetmate.agent.tools import AgentContext, Tool, ToolRegistry, ToolResult, execute_tool
from sheetmate.engine.script_references import externalize_script_references, externalize_script_text
from sheetmate.engine.script_store import script_params

_TOOL_MARKER = "<｜tool▁call▁begin｜>"
_TOOL_RE = re.compile(r"<｜tool[\s\S]*?(?:<｜tool▁calls?▁end｜>|$)")
_ROLE_TAGS = {"input": "", "expected": "，⭐期望结果（人工做好的样例）", "derived": "，分析结果", "result": "，流程产出"}
_EXPLORE_TOOLS = {"table_info", "value_counts", "column_stats", "preview_table", "run_sql", "list_files", "get_recipe"}
_RECIPE_TOOLS = {"build_recipe", "revise_recipe", "run_recipe"}
_SCRIPT_TOOL = "run_python_script"
_SCRIPT_EXTRA_ITERATIONS = 12
_DIAGNOSTIC_TOOLS = (_EXPLORE_TOOLS - {"run_sql"}) | {
    "inspect_python_script", "inspect_workbook_creation", "list_recipes", "load_file", "show_files",
    "remember", "save_field_mapping", "save_as_recipe", "delete_recipe", "delete_recipes", "ask_user",
}
_NUDGE_AFTER = 4  # 修改模式下查看数据超过这么多次还没生成流程，就提醒一次


@dataclass
class _ScriptAttempt:
    result: ToolResult
    executions: int = 1


@dataclass
class _ToolBudget:
    used: int = 0


class AgentExecutor:
    def __init__(self, llm: Any, registry: ToolRegistry | None = None, max_iterations: int = 12,
                 max_tool_calls: int = 40):
        self.llm = llm
        self.registry = registry or ToolRegistry()
        self.max_iterations = max_iterations
        self.max_tool_calls = max(1, max_tool_calls)

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    async def converse(self, ctx: AgentContext, message: str, history: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """普通对话和意图询问，不提供任务执行工具。"""
        messages = [{"role": "system", "content": CHAT_SYSTEM}, *model_history(history[-12:]),
                    {"role": "user", "content": user_content(message, ctx.image_parts)}]
        async for event in self.agent_stream(messages, [], ctx):
            yield event

    async def execute(self, ctx: AgentContext, message: str, history: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """直接执行（不带计划）。"""
        messages = [{"role": "system", "content": build_system_prompt(ctx, message)}, *model_history(history[-12:]),
                    {"role": "user", "content": user_content(message, ctx.image_parts)}]
        async for event in self.agent_stream(messages, self.registry.for_context(ctx), ctx):
            yield event

    async def execute_plan(self, ctx: AgentContext, message: str, steps: list[dict[str, Any]],
                           history: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """按用户确认的计划逐步执行，每步一次 ReAct，最后自检（TabClaw execute_plan）。"""
        tools = self.registry.for_context(ctx)
        conversation = [{"role": "system", "content": build_system_prompt(ctx, message)}, *model_history(history[-12:]),
                        {"role": "user", "content": user_content(f"原始需求：{message}\n下面按计划逐步执行。", ctx.image_parts)}]
        budget = _ToolBudget()
        executed_steps = 0
        yield {"type": "plan_start", "steps": [{"id": index, "description": str(step.get("description") or f"第 {index} 步")}
                                                for index, step in enumerate(steps, start=1)]}
        for i, step in enumerate(steps, start=1):
            executed_steps = i
            description = str(step.get("description") or f"第 {i} 步")
            yield {"type": "step_start", "step_num": i, "total": len(steps), "description": description}
            step_message = PLAN_STEP_MESSAGE.format(num=i, total=len(steps), description=description)
            # 每一步都刷新系统提示里的表清单：前面步骤生成的表要让模型看得到
            conversation[0] = {"role": "system", "content": build_system_prompt(ctx, message)}
            conversation.append({"role": "user", "content": step_message})
            # 把这一步真实的工具调用和结果都记进对话（TabClaw 原来只保留文字结论，模型会以为说了就等于做了）
            transcript: list[dict[str, Any]] = []
            step_text = ""
            waiting = False
            question = ""
            unresolved_tools: set[str] = set()
            async for event in self.agent_stream(conversation, tools, ctx, transcript=transcript, budget=budget):
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
                    yield {"type": "step_done", "step_num": i, "status": "error", "summary": event.get("content", "执行失败")}
                    return
                if event["type"] == "clarify":
                    waiting = True
                    question = event.get("question", "需要确认后才能继续")
                if event["type"] == "tool_result":
                    if event["ok"]:
                        unresolved_tools.discard(event["tool"])
                    else:
                        unresolved_tools.add(event["tool"])
            conversation.extend(transcript)
            budget_exhausted = budget.used >= self.max_tool_calls
            status = "waiting" if waiting else "warning" if unresolved_tools or budget_exhausted else "done"
            yield {"type": "step_done", "step_num": i, "status": status, "summary": question if waiting else step_text.strip()}
            if waiting:
                return
            if budget_exhausted:
                break
        yield {"type": "reflect_start"}
        conversation[0] = {"role": "system", "content": build_system_prompt(ctx, message)}
        reflect = REFLECT_MESSAGE.format(message=message, total=executed_steps)
        if executed_steps < len(steps):
            reflect += f"\n工具调用预算已用尽；计划中还有 {len(steps) - executed_steps} 个步骤未执行，不得称已完成。"
        async for event in self.agent_stream(conversation + [{"role": "user", "content": reflect}], tools, ctx,
                                             budget=budget):
            yield event
        yield {"type": "reflect_done"}

    # ------------------------------------------------------------------
    # 核心循环
    # ------------------------------------------------------------------

    async def agent_stream(self, messages: list[dict[str, Any]], tools: list[Tool], ctx: AgentContext,
                           transcript: list[dict[str, Any]] | None = None,
                           budget: _ToolBudget | None = None) -> AsyncIterator[dict[str, Any]]:
        """transcript 不为空时，把这一轮新增的消息（工具调用、工具结果、最终回答）追加进去，供计划模式接续上下文。"""
        msgs = list(messages)
        start = len(msgs)
        tool_map = {t.name: t for t in tools}
        recovery = RecoveryState()
        if tools:
            tool_map[RECONSIDER_TOOL] = recovery.tool()
        explored, built, nudged = 0, False, False
        iteration, iteration_limit = 0, max(1, self.max_iterations)
        script_budget_added, fallback_suggested = False, False
        failures: dict[tuple[str, str], int] = {}
        script_attempts: dict[str, _ScriptAttempt] = {}
        budget = budget or _ToolBudget()
        while iteration < iteration_limit:
            budget_exhausted = recovery.exhausted or budget.used >= self.max_tool_calls
            available = [tool for name, tool in tool_map.items()
                         if (name != RECONSIDER_TOOL or recovery.pending or recovery.results)
                         and (not recovery.must_switch or name == RECONSIDER_TOOL or name in _DIAGNOSTIC_TOOLS)]
            definitions = [tool.definition() for tool in available] or None
            # 最后一轮不再允许调用工具，保证一定有回答（TabClaw 原来是直接报「超过最大轮数」）
            final_round = budget_exhausted or (iteration == iteration_limit - 1 and definitions is not None)
            iteration += 1
            if final_round:
                if budget_exhausted:
                    msgs.append({"role": "user", "content": RECOVERY_BUDGET_MESSAGE})
                msgs.append({"role": "user", "content": FINAL_ROUND_MESSAGE})
            content, reasoning = "", ""
            calls_acc: dict[int, dict[str, str]] = {}
            suppress = False
            # 允许工具的轮次，只有收齐本轮内容后才能确定文字属于过程还是回答。
            # 不能根据长度提前展示，否则后到的工具调用会迫使界面撤回已显示文字。
            live = not definitions or final_round
            try:
                async for chunk in self.llm.stream_chat(msgs, tools=definitions, tool_choice="none" if final_round else None):
                    choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
                    if choice is None:
                        continue
                    delta = choice.delta
                    piece = getattr(delta, "reasoning_content", None)
                    if piece:
                        reasoning += piece
                        yield {"type": "thinking", "content": piece}
                    if delta.content:
                        content += delta.content
                        if _TOOL_MARKER in delta.content:
                            suppress = True
                        if live and not suppress:
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
            except LLMError as exc:
                yield {"type": "error", "content": str(exc)}
                return

            display = _TOOL_RE.sub("", content).strip()
            reply: dict[str, Any] = {"role": "assistant", "content": display}
            if reasoning:
                reply["reasoning_content"] = reasoning
            if not definitions or not calls_acc or final_round:
                if not final_round and definitions and not recovery.may_finish:
                    # 单一路径失败时先自主恢复；不同方法确实尝试后由 AI 决定是否收尾。
                    # 候选文字尚未展示，可以直接继续恢复，不改变已有内容。
                    if display:
                        msgs.append(reply)
                    msgs.append({"role": "user", "content": AUTONOMOUS_RECOVERY_MESSAGE})
                    continue
                if display and not live:
                    yield {"type": "text_chunk", "content": display}
                if transcript is not None:
                    transcript.extend(m for m in msgs[start:] if m.get("content") != FINAL_ROUND_MESSAGE)
                    if display:
                        transcript.append(reply)
                yield {"type": "final_text", "content": display}
                return
            if display:
                # 先确定这段文字是过程说明，再按顺序展示一次。
                yield {"type": "note", "content": display}

            calls = [
                {"id": slot["id"] or f"call_{idx}", "type": "function",
                 "function": {"name": slot["name"], "arguments": slot["arguments"]}}
                for idx, slot in sorted(calls_acc.items())
            ]
            if "delete_recipes" in tool_map:
                calls = _coalesce_recipe_delete_calls(calls)
            assistant: dict[str, Any] = {"role": "assistant", "content": display or None, "tool_calls": calls}
            if reasoning:
                assistant["reasoning_content"] = reasoning
            msgs.append(assistant)

            end_turn = False
            guidance: list[str] = []
            for call_index, call in enumerate(calls):
                if budget.used >= self.max_tool_calls or recovery.exhausted:
                    assistant["tool_calls"] = calls[:call_index]
                    break
                budget.used += 1
                result: ToolResult | None = None
                name = call["function"]["name"]
                args = _parse_args(call["function"]["arguments"])
                tool = tool_map.get(name)
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
                yield {"type": "tool_call", "id": call["id"], "tool": name,
                       "label": tool.display(args or {}) if tool else name,
                       "params": {**(tool.public_params(args or {}) if tool and tool.public_params else debug_params if isinstance(debug_params, dict) else {}), **script_ref},
                       "debug_params": debug_params, **script_ref}
                started = time.perf_counter()
                script_key = None
                attempt = None
                repeated_script = False
                method_blocked = False
                if tool is None:
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
                        script_key = _script_key(args, ctx)
                        attempt = script_attempts.get(script_key) if script_key else None
                        if attempt is None and script_key and args.get("input_tables") is None:
                            attempt = script_attempts.get("default:" + script_key)
                    # 成功结果直接复用；失败允许一次原样重试以容纳暂时性故障。
                    repeated_script = attempt is not None and (attempt.result.ok or attempt.executions >= 2)
                    if repeated_script:
                        result = ToolResult(
                            REPEATED_SCRIPT_MESSAGE + "\n上次实际执行的反馈：\n" + attempt.result.text,
                            ok=attempt.result.ok, summary="相同代码与输入已执行，复用上次结果",
                            public_summary="已读取已有处理结果", public_text="正在根据已有结果核对处理方法。",
                        )
                    else:
                        async for item in execute_tool(tool, args, ctx):
                            if isinstance(item, ToolResult):
                                result = item
                            else:
                                yield {**item, "tool_call_id": call["id"]}
                assert result is not None
                if script_key and not repeated_script:
                    if attempt is None:
                        attempt = _ScriptAttempt(result)
                    else:
                        attempt.result = result
                        attempt.executions += 1
                    script_attempts[script_key] = attempt
                    if args.get("input_tables") is None:
                        # 默认输入会自动包含刚产生的中间表；它们不能使原脚本绕过去重。
                        # 显式选择这些表、或之后由其他工具改变输入，仍会得到新的指纹。
                        after_key = _script_key(args, ctx)
                        if after_key:
                            script_attempts["default:" + after_key] = attempt
                for table in result.tables:
                    yield {"type": "table", "tool_call_id": call["id"], "table": table}
                for extra in result.events:
                    yield {**extra, "tool_call_id": call["id"]}
                public_text = externalize_script_text(
                    result.public_text if result.public_text is not None else result.text,
                    python=name in {_SCRIPT_TOOL, "inspect_python_script"},
                )
                yield {"type": "tool_result", "id": call["id"], "tool": name, "ok": result.ok,
                       "summary": result.public_summary if result.public_summary is not None else result.summary or result.text[:200],
                       "text": public_text[:3000],
                       "debug_result": externalize_script_text(result.text, python=name in {_SCRIPT_TOOL, "inspect_python_script"}),
                       "duration_ms": int((time.perf_counter() - started) * 1000)}
                msgs.append({"role": "tool", "tool_call_id": call["id"], "content": result.text})
                end_turn = end_turn or result.end_turn
                if name != RECONSIDER_TOOL and not method_blocked:
                    recovery.observe(name, result, call_id=call["id"], diagnostic=name in _DIAGNOSTIC_TOOLS,
                                     reused=repeated_script)
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
                if recovery.must_switch and not recovery.exhausted:
                    guidance.append(_approach_switch_message(recovery))
                if result.end_turn:
                    # 等待用户回答时立即停止，后续调用不能覆盖确认或产生额外副作用。
                    # 只保留实际执行过的调用，保证计划模式接续时工具消息成对。
                    assistant["tool_calls"] = calls[:call_index + 1]
                    break
            msgs.extend({"role": "user", "content": message} for message in guidance)
            if end_turn:
                if transcript is not None:
                    transcript.extend(msgs[start:])
                # 工具前的说明已经作为 note 交付；确认问题由 clarify 卡片展示。
                yield {"type": "final_text", "content": ""}
                return
            if ctx.mode == "modify" and not built and not nudged and explored >= _NUDGE_AFTER:
                # 收束没有产出修改文件的探索；由模型选择 Recipe 或 Python 完成交付。
                msgs.append({"role": "user", "content": MODIFY_NUDGE.format(count=explored)})
                nudged = True


def _script_key(args: dict[str, Any], ctx: AgentContext) -> str | None:
    """只比较代码和实际声明的输入版本，描述变化不构成新尝试。"""
    try:
        request = ScriptRequest.model_validate(args)
    except ValueError:
        return None  # 参数错误仍由工具按原流程反馈，不能复用此前的有效调用。
    visible = {table.name: table for table in ctx.visible_tables()}
    names = request.input_tables
    if names is None:
        names = [table.name for table in visible.values() if table.role != "expected"]
    payload = {
        "code": request.code,
        "tables": [(name, visible[name].created_at if name in visible else None) for name in names],
        "files": request.input_files,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _approach_switch_message(recovery: RecoveryState) -> str:
    return APPROACH_SWITCH_MESSAGE.format(
        method=recovery.current.name, failures=recovery.current.failures, limit=METHOD_FAILURE_LIMIT,
        remaining=MAX_METHODS - len(recovery.methods),
    )


def _parse_args(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw) if raw and raw.strip() else {}
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


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


def build_system_prompt(ctx: AgentContext, message: str) -> str:
    tables = ctx.visible_tables()
    table_lines = []
    for t in tables:
        cols = ", ".join(t.column_names[:14]) + (f" …（共 {len(t.columns)} 列）" if len(t.columns) > 14 else "")
        tag = _ROLE_TAGS.get(t.role, "")
        if t.created_by == "create_workbook":
            tag += f"，建表生成，file_id={t.file_id}（可查看布局并另建新版）"
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
    files = [f for f in ctx.host.list_files() if not f.get("tables")]
    file_lines = [f"- file_id={f['file_id']}「{f['filename']}」（{f.get('scope_label', '')}）"
                  + ("，图片附件（非工作簿，无工作表）" if (f.get("media_type") or "").startswith("image/") else "")
                  + (f"，脚本 run_id={f['script_run_id']}（可查看脚本后继续调整）" if f.get("script_run_id") else "")
                  for f in files[:15]]
    if ctx.customer:
        summary = (ctx.customer.get("summary") or "").strip()
        excerpt = summary[:800] + ("…" if len(summary) > 800 else "")
        customer_block = f"已绑定客户「{ctx.customer['name']}」：按这个客户的记忆、流程和业务规则来处理。"
        if excerpt:
            customer_block += f"\n客户的整体处理逻辑：\n{excerpt}"
    else:
        customer_block = "未绑定客户：本会话的数据只属于这个会话。用户以后可以把会话保存到某个客户名下。"
    recipes = ctx.host.list_recipes()
    recipes_block = ""
    if recipes:
        lines = [f"- recipe_id={r['recipe_id']}「{r['name']}」v{r['version']}：{r['description'][:80]} "
                 f"输入 {r['inputs']}，{r['step_count']} 步" + (f"；与当前表匹配：{r['match']}" if r.get("match") else "")
                 for r in recipes[:10]]
        recipes_block = "\n## 已有的处理流程（Recipe，优先复用）\n" + "\n".join(lines) + "\n"
    return AGENT_SYSTEM.format(
        mode_label={"analyze": "分析", "modify": "修改", "create": "新建"}.get(ctx.mode, "分析"),
        mode_rules=MODE_RULES.get(ctx.mode, MODE_RULES["analyze"]) + (
            "\n本轮用户已明确要求保存或更新重复流程，可在处理完成后保存。"
            if ctx.save_recipe_requested else
            "\n本轮未授权保存重复流程：只完成本次处理，不调用 save_as_recipe，不保存或更新流程，也不主动询问是否保存。"
        ) + (
            "\n用户已经确认删除这些流程 ID：" + "、".join(sorted(ctx.delete_recipe_ids)) + "。直接执行，不重复询问。"
            if ctx.delete_recipe_ids else
            "\n用户要求删除流程时，直接调用 delete_recipe 或 delete_recipes，由工具列出目标并请求一次确认。删除全部流程用 delete_recipes(all=true)。不需要开启额外授权，确认后系统直接执行。"
        ),
        customer_block=customer_block,
        tables_block="\n".join(table_lines) or ("（还没有表，可直接根据用户文字或资料新建工作簿）" if ctx.mode == "create"
                                               else "（还没有表；可回答问题，需要处理现有数据时再加载 Excel / CSV）"),
        files_block="\n".join(file_lines) or "（没有）",
        memory_block=ctx.host.memory_text(message) or "（没有）",
        recipes_block=recipes_block,
    )
