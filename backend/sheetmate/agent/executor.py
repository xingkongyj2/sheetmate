"""ReAct 工具循环（移植自 TabClaw agent/executor.py）。

一轮循环：流式调用 LLM → 收集文本增量和 tool_call 分片 → 有工具调用就执行工具、把结果作为 role=tool 回传 → 再调 LLM；
没有工具调用时，当前文本就是最终回答。默认最多 max_iterations 轮；使用脚本时增加有界的修正轮数。

保留了 TabClaw 的两个细节：
- 工具调用参数是分片流式返回的，按 index 拼接完整后再 json.loads
- DeepSeek 偶尔把内部工具标记 <｜tool▁call▁begin｜> 泄漏到正文，检测到后不再显示，并在最终文本里清掉
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from sheetmate.agent.llm import LLMError
from sheetmate.agent.prompts import (
    AGENT_SYSTEM,
    AGENT_TABLE_LINE,
    FINAL_ROUND_MESSAGE,
    MODE_RULES,
    MODIFY_NUDGE,
    PLAN_STEP_MESSAGE,
    REPEATED_FAILURE_MESSAGE,
    REFLECT_MESSAGE,
    SCRIPT_RETRY_MESSAGE,
    TOOL_FALLBACK_MESSAGE,
)
from sheetmate.agent.tools import AgentContext, Tool, ToolRegistry, ToolResult, execute_tool

_TOOL_MARKER = "<｜tool▁call▁begin｜>"
_TOOL_RE = re.compile(r"<｜tool[\s\S]*?(?:<｜tool▁calls?▁end｜>|$)")
_ROLE_TAGS = {"input": "", "expected": "，⭐期望结果（人工做好的样例）", "derived": "，分析结果", "result": "，流程产出"}
_EXPLORE_TOOLS = {"table_info", "value_counts", "column_stats", "preview_table", "run_sql", "list_files", "get_recipe"}
_RECIPE_TOOLS = {"build_recipe", "revise_recipe", "run_recipe"}
_SCRIPT_TOOL = "run_python_script"
_SCRIPT_EXTRA_ITERATIONS = 12
_NUDGE_AFTER = 4  # 修改模式下查看数据超过这么多次还没生成流程，就提醒一次
_LIVE_AFTER_CHARS = 140  # 一轮文字超过这么长、且还没出现工具调用，就当作最终回答开始实时输出


class AgentExecutor:
    def __init__(self, llm: Any, registry: ToolRegistry | None = None, max_iterations: int = 12):
        self.llm = llm
        self.registry = registry or ToolRegistry()
        self.max_iterations = max_iterations

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    async def execute(self, ctx: AgentContext, message: str, history: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """直接执行（不带计划）。"""
        messages = [{"role": "system", "content": build_system_prompt(ctx, message)}, *history[-12:],
                    {"role": "user", "content": message}]
        async for event in self.agent_stream(messages, self.registry.for_context(ctx), ctx):
            yield event

    async def execute_plan(self, ctx: AgentContext, message: str, steps: list[dict[str, Any]],
                           history: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """按用户确认的计划逐步执行，每步一次 ReAct，最后自检（TabClaw execute_plan）。"""
        tools = self.registry.for_context(ctx)
        conversation = [{"role": "system", "content": build_system_prompt(ctx, message)}, *history[-12:],
                        {"role": "user", "content": f"原始需求：{message}\n下面按计划逐步执行。"}]
        yield {"type": "plan_start", "steps": [{"id": index, "description": str(step.get("description") or f"第 {index} 步")}
                                                for index, step in enumerate(steps, start=1)]}
        for i, step in enumerate(steps, start=1):
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
            async for event in self.agent_stream(conversation, tools, ctx, transcript=transcript):
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
            status = "waiting" if waiting else "warning" if unresolved_tools else "done"
            yield {"type": "step_done", "step_num": i, "status": status, "summary": question if waiting else step_text.strip()}
            if waiting:
                return
        yield {"type": "reflect_start"}
        conversation[0] = {"role": "system", "content": build_system_prompt(ctx, message)}
        reflect = REFLECT_MESSAGE.format(message=message, total=len(steps))
        async for event in self.agent_stream(conversation + [{"role": "user", "content": reflect}], tools, ctx):
            yield event
        yield {"type": "reflect_done"}

    # ------------------------------------------------------------------
    # 核心循环
    # ------------------------------------------------------------------

    async def agent_stream(self, messages: list[dict[str, Any]], tools: list[Tool], ctx: AgentContext,
                           transcript: list[dict[str, Any]] | None = None) -> AsyncIterator[dict[str, Any]]:
        """transcript 不为空时，把这一轮新增的消息（工具调用、工具结果、最终回答）追加进去，供计划模式接续上下文。"""
        msgs = list(messages)
        start = len(msgs)
        tool_map = {t.name: t for t in tools}
        definitions = [t.definition() for t in tools] or None
        explored, built, nudged = 0, False, False
        iteration, iteration_limit = 0, max(1, self.max_iterations)
        script_budget_added, fallback_suggested = False, False
        failures: dict[tuple[str, str], int] = {}
        while iteration < iteration_limit:
            # 最后一轮不再允许调用工具，保证一定有回答（TabClaw 原来是直接报「超过最大轮数」）
            final_round = iteration == iteration_limit - 1 and definitions is not None
            iteration += 1
            if final_round:
                msgs.append({"role": "user", "content": FINAL_ROUND_MESSAGE})
            content, reasoning = "", ""
            calls_acc: dict[int, dict[str, str]] = {}
            suppress = False
            live = False  # 已经开始向用户实时输出这一轮的文字
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
                        # 先把文字攒着：后面跟着工具调用的话，这段只是「我先看一下表结构」之类的过程说明，不该出现在回答里。
                        # 只有确定是最终回答（没出现工具调用、文字已经比较长）才开始实时输出
                        if not live and not calls_acc and not suppress and len(content) >= _LIVE_AFTER_CHARS:
                            live = True
                            yield {"type": "text_chunk", "content": content}
                        elif live and not suppress:
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
            if not calls_acc or final_round:
                if display and not live:  # 短回答：攒到最后一次性给出，前端会做逐字显示的动画
                    yield {"type": "text_chunk", "content": display}
                if transcript is not None:
                    transcript.extend(m for m in msgs[start:] if m.get("content") != FINAL_ROUND_MESSAGE)
                    if display:
                        transcript.append({"role": "assistant", "content": display})
                yield {"type": "final_text", "content": display}
                return
            if display or live:
                # 这段文字后面跟着工具调用：是过程说明，归到「执行过程」里，并让前端把已经显示的部分撤回
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
                result: ToolResult | None = None
                name = call["function"]["name"]
                args = _parse_args(call["function"]["arguments"])
                tool = tool_map.get(name)
                yield {"type": "tool_call", "id": call["id"], "tool": name,
                       "label": tool.display(args or {}) if tool else name,
                       "params": tool.public_params(args or {}) if tool and tool.public_params else args or {}}
                started = time.perf_counter()
                if tool is None:
                    result = ToolResult(f"没有工具 `{name}`。可用工具：{', '.join(tool_map)}", ok=False)
                elif args is None:
                    result = ToolResult("工具参数不是合法的 JSON 对象，请重新调用。", ok=False)
                else:
                    async for item in execute_tool(tool, args, ctx):
                        if isinstance(item, ToolResult):
                            result = item
                        else:
                            yield {**item, "tool_call_id": call["id"]}
                assert result is not None
                for table in result.tables:
                    yield {"type": "table", "tool_call_id": call["id"], "table": table}
                for extra in result.events:
                    yield {**extra, "tool_call_id": call["id"]}
                yield {"type": "tool_result", "id": call["id"], "tool": name, "ok": result.ok,
                       "summary": result.public_summary if result.public_summary is not None else result.summary or result.text[:200],
                       "text": (result.public_text if result.public_text is not None else result.text)[:3000],
                       "duration_ms": int((time.perf_counter() - started) * 1000)}
                msgs.append({"role": "tool", "tool_call_id": call["id"], "content": result.text})
                end_turn = end_turn or result.end_turn
                explored += name in _EXPLORE_TOOLS
                built = built or name in _RECIPE_TOOLS
                if name == _SCRIPT_TOOL and tool is not None:
                    if not script_budget_added:
                        iteration_limit += _SCRIPT_EXTRA_ITERATIONS
                        script_budget_added = True
                    # 脚本中间表或打印完成不算修改交付，实际文件可替代 Recipe 完成交付。
                    built = built or (result.ok and any(event.get("type") == "file" and event.get("file_id")
                                                       for event in result.events))
                if not result.ok:
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
                if result.end_turn:
                    # 等待用户回答时立即停止，后续调用不能覆盖确认或产生额外副作用。
                    # 只保留实际执行过的调用，保证计划模式接续时工具消息成对。
                    assistant["tool_calls"] = calls[:call_index + 1]
                    break
            msgs.extend({"role": "user", "content": message} for message in guidance)
            if end_turn:
                if transcript is not None:
                    transcript.extend(msgs[start:])
                yield {"type": "final_text", "content": display}
                return
            if ctx.mode == "modify" and not built and not nudged and explored >= _NUDGE_AFTER:
                # 收束没有产出修改文件的探索；由模型选择 Recipe 或 Python 完成交付。
                msgs.append({"role": "user", "content": MODIFY_NUDGE.format(count=explored)})
                nudged = True


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
            tag += f"，脚本生成，file_id={t.file_id}（可作为脚本输入副本继续处理）"
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
        lines = [f"- recipe_id={r['recipe_id']}「{r['name']}」v{r['version']}（{r['status_label']}）：{r['description'][:80]} "
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
