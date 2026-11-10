"""思考模式：先勘察数据、起草并审查计划，再逐步执行（每步一次完整的 ReAct + 一次核对），最后自检并核对回答。

和普通模式（直接 ReAct）相比，多出来的几次与大模型的交互，每一次都在补一个容易出错的环节：

  1. 勘察 + 起草计划   规划之前先看到真实的列类型、空值率、样本和候选主键；计划里写明理解、默认口径、风险和每一步的验收标准
  2. 审查计划          另起一次调用，对照需求和数据找遗漏、顺序错误、不存在的列、缺少勾稽的地方
  3. 逐步执行 + 核对   每一步仍是完整的 ReAct 工具循环；结束后另起一次调用，只依据工具的真实返回判断是否达标，
                      不达标就带着具体问题补做一次（最多一次，仍有问题则如实标注）
  4. 综合自检 + 回答核对  最终回答先扣住，对照各步骤的工具结果检查数字和结论，有出入就让智能体用工具核实、更正后再发出

整个过程不打断用户（不需要确认计划）。需求里真正的业务歧义，仍然由执行时的 ask_user 向用户提问。

给用户看的内容像同事边做边讲，不是「第 N 步」的报告：
- 开场白：对需求的理解、默认口径、数据里要留意的、计划审查的结果；
- 每项说明和收尾总结均不超过 30 字；完整业务指令和执行证据留给执行与最终核对；
- 思考模式固定展示紧凑进度清单（plan_start.checklist）；AI 的 show_plan 判断只控制最终回答是否逐项回顾；
- 过程中大模型在忙、界面上没有别的变化时，用 activity 事件告诉用户当前在做什么（临时状态，不存库）；
- 回答核对的结果（audit_done）是结构化事件，界面在回答下面显示为一行小标识，不追加进回答文字。
所有辅助调用都有时限、都可以降级：超时、调用失败或返回无法解析的内容时，退回到更简单的做法，
把原因告诉用户并写进日志，不会让整次请求失败，也不会让一次卡住的核对拖住整个回答。
事件沿用计划模式的 plan_start / step_start / step_done / reflect_*，另有 think_start、plan_review、step_check、audit_*（存库，开发者日志可见）和 activity（临时）。
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from sheetmate.agent.content import model_history, user_content
from sheetmate.agent.evidence import evidence_projection
from sheetmate.agent.contracts import ANSWER_AUDIT_SCHEMA, STEP_VERDICT_SCHEMA
from sheetmate.agent.executor import AgentExecutor, build_system_prompt
from sheetmate.agent.llm import aux_json
from sheetmate.agent.planner import Plan, clean_list, clean_text, clip, data_brief, make_plan, plan_from_steps, review_plan
from sheetmate.agent.prompts import (
    THINK_AUDIT_FIX_MESSAGE,
    THINK_AUDIT_SYSTEM,
    THINK_FIX_MESSAGE,
    THINK_OPENER_SAID,
    THINK_OPENER_UNSAID,
    THINK_REFLECT_MESSAGE,
    THINK_REFLECT_MESSAGE_SMALL,
    THINK_STEP_MESSAGE,
    THINK_TASK_MESSAGE,
    THINK_VERIFY_SYSTEM,
)
from sheetmate.agent.reporting import grounded, public_sentence, short_step_text, step_summary, tidy_report
from sheetmate.agent.tools import AgentContext

log = logging.getLogger(__name__)

MAX_STEP_FIXES = 1  # 每一步核对不通过时最多补做的次数
CHECK_TIMEOUT = 60.0  # 步骤核对、回答核对各自最多等多久；超时就放弃这次核对，不能拖住整个回答
CHECK_MAX_TOKENS = 2000  # 核对的输出很小，给个小上限，免得模型空转到默认的 8192 才停
THINKING_TOOL_BUDGET = 60  # 思考模式的工具调用总预算：计划各步、补做和自检共用（普通模式是 40）
_DIGEST_RESULT = 700  # 送去核对的单个工具结果长度上限
_AUDIT_RESULTS = 20  # 回答核对最多参考最近多少个工具结果


@dataclass
class Verdict:
    passed: bool = True
    issues: list[str] = field(default_factory=list)
    advice: str = ""
    carry_over: str = ""
    checked: str = ""  # 通过时：核对了什么、结果如何（一句话，数字都能在工具返回里找到）
    skipped: bool = False  # 核对本身没能完成（超时、调用失败或返回无法解析），不据此阻塞执行，也不声称已核对
    reason: str = ""  # skipped 时的原因，给用户看；没有依据可核对的步骤不写原因，也就不提
    timed_out: bool = False


@dataclass
class Audit:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    skipped: bool = False
    reason: str = ""


@dataclass
class _CheckHealth:
    """核对的健康状况：模型服务卡住（超时）之后，后面的步骤核对不再各等一轮。"""

    degraded: bool = False


@dataclass
class _Ledger:
    """整个任务的证据：回答核对时拿它和最终回答对照。"""

    digests: list[dict[str, Any]] = field(default_factory=list)
    summaries: dict[int, str] = field(default_factory=dict)
    checks: list[tuple[str, str]] = field(default_factory=list)  # (这一项做什么, 核对情况)：写进终稿提示的「核对记录」
    files: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)


@dataclass
class _Run:
    """一次 ReAct 循环的产出（一个步骤、一次补做，或最终回答阶段）。"""

    text: str = ""
    transcript: list[dict[str, Any]] = field(default_factory=list)
    held: list[dict[str, Any]] = field(default_factory=list)  # 最终回答阶段扣住的文字事件
    results_ok: dict[str, bool] = field(default_factory=dict)  # 工具调用 ID → 是否成功
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    protocol_results: set[str] = field(default_factory=set)  # 参数未通过校验、尚未执行的调用，不能作为业务证据
    unresolved: set[str] = field(default_factory=set)  # 最后一次调用仍然失败的工具
    waiting: bool = False
    question: str = ""
    failed: bool = False
    error: str = ""
    incomplete: bool = False
    verified_complete: bool = False

    def observe(self, event: dict[str, Any], ledger: _Ledger) -> None:
        kind = event["type"]
        if kind == "clarify":
            self.waiting = True
            self.question = event.get("question") or "需要确认后才能继续"
        elif kind == "execution_stop" and event.get("status") == "incomplete":
            self.incomplete = True
        elif kind == "execution_stop" and event.get("status") == "completed":
            self.verified_complete = event.get("verified") is True
        elif kind == "tool_result":
            if event.get("error_kind") in {"protocol", "schema"}:
                self.protocol_results.add(event["id"])
                return
            self.results_ok[event["id"]] = bool(event["ok"])
            if event.get("evidence"):
                self.evidence[event["id"]] = evidence_projection(event["evidence"])
            if event["ok"]:
                self.unresolved.discard(event["tool"])
            else:
                self.unresolved.add(event["tool"])
        elif kind == "file" and event.get("filename"):
            ledger.files.append(str(event["filename"]))
        elif kind == "table":
            table = event.get("table") or {}
            ledger.tables.append(f"{table.get('label') or table.get('name')}（{table.get('rows', '?')} 行）")


async def _in_thread(ctx: AgentContext, function: Callable[..., Any], *args: Any) -> Any:
    """在线程里做同步的读取；被停止时和工具一样先中断工作区，避免线程继续占着连接。"""
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        ctx.ws.interrupt()
        try:
            await worker
        except Exception:
            pass
        raise


# ---------------------------------------------------------------------------
# 证据整理
# ---------------------------------------------------------------------------


def _tool_digest(transcript: list[dict[str, Any]], results_ok: dict[str, bool], step: int,
                 protocol_results: set[str] | None = None,
                 evidence: dict[str, dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """把一次 ReAct 的消息整理成「调用了什么、返回了什么」，作为核对依据。"""
    calls: dict[str, tuple[str, str]] = {}
    digest = []
    for message in transcript:
        if message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                calls[call.get("id", "")] = (function.get("name", "?"), function.get("arguments", ""))
        elif message.get("role") == "tool":
            call_id = message.get("tool_call_id", "")
            if call_id in (protocol_results or ()):
                continue
            name, arguments = calls.get(call_id, ("?", ""))
            digest.append({"call_id": call_id, "step": step, "tool": name, "ok": results_ok.get(call_id), "args": clip(arguments, 240),
                           "execution": (evidence or {}).get(call_id, {}),
                           "result": clip(message.get("content") or "", _DIGEST_RESULT)})
    return digest


def _answer_text(held: list[dict[str, Any]]) -> str:
    final = next((event.get("content", "") for event in reversed(held) if event["type"] == "final_text"), "")
    return (final or "".join(event["content"] for event in held if event["type"] == "text_chunk")).strip()


# ---------------------------------------------------------------------------
# 核对（辅助的大模型交互）
# ---------------------------------------------------------------------------


async def verify_step(llm: Any, message: str, plan: Plan, step: dict[str, Any], num: int, run: _Run,
                      digest: list[dict[str, Any]]) -> Verdict:
    """只依据工具的真实返回判断一步是否做对做完。限时；超时、调用失败或返回无法解析时返回 skipped（带原因），不阻塞执行。"""
    payload = {
        "requirement": message,
        "assumptions": plan.assumptions,
        "step": {"num": num, "description": step["description"], "check": step.get("check", "")},
        "tool_results": digest[-12:],
        "step_summary": clip(run.text.strip(), 1500),
    }
    outcome = await aux_json(llm, json.dumps(payload, ensure_ascii=False), system=THINK_VERIFY_SYSTEM,
                             timeout=CHECK_TIMEOUT, max_tokens=CHECK_MAX_TOKENS,
                             schema=STEP_VERDICT_SCHEMA, schema_name="step_verdict", label=f"思考模式第 {num} 步核对")
    if not outcome.ok:
        return Verdict(passed=False, skipped=True, reason=outcome.reason, timed_out=outcome.timed_out)
    result = outcome.value
    if not isinstance(result, dict) or not isinstance(result.get("passed"), bool):
        log.warning("思考模式第 %s 步核对：模型返回的内容无法解析：%s", num, clip(repr(result), 200))
        return Verdict(passed=False, skipped=True, reason="核对结果无法解析")
    issues = clean_list(result.get("issues"), 5, 240)
    passed = result["passed"]
    if (passed and issues) or (not passed and not issues):
        log.warning("思考模式第 %s 步核对：结论与问题列表矛盾，未确认通过", num)
        return Verdict(passed=False, skipped=True, reason="核对结论与问题列表不一致，未确认通过")
    checked = clean_text(result.get("checked"), 120) if passed else ""
    if checked and not grounded(checked, "\n".join(f"{item['args']}\n{item['result']}" for item in digest)):
        # 这句话会直接展示给用户：里面的数字必须能在工具的真实返回里找到，否则只说「核对通过」，不展示可能编造的数字
        log.info("思考模式第 %s 步核对：说明里的数字在工具返回里找不到，不展示：%s", num, clip(checked, 120))
        checked = ""
    return Verdict(
        passed=passed,
        issues=[] if passed else issues,
        advice="" if passed else clean_text(result.get("advice"), 400),
        carry_over=clean_text(result.get("carry_over"), 400),
        checked=checked,
    )


async def audit_answer(llm: Any, message: str, plan: Plan, answer: str, ledger: _Ledger, record: str = "") -> Audit:
    """最终回答发出之前，对照本轮的工具结果核对数字和结论。限时；没能完成时返回 skipped（带原因）。"""
    payload = {
        "requirement": message,
        "assumptions": plan.assumptions,
        "step_summaries": [{"step": num, "summary": clip(text, 800)} for num, text in sorted(ledger.summaries.items()) if text],
        "tool_results": [{**item, "result": clip(item["result"], 500)} for item in ledger.digests[-_AUDIT_RESULTS:]],
        "check_records": record,
        "delivered_files": ledger.files,
        "result_tables": ledger.tables[-12:],
        "final_answer": clip(answer, 6000),
    }
    outcome = await aux_json(llm, json.dumps(payload, ensure_ascii=False), system=THINK_AUDIT_SYSTEM,
                             timeout=CHECK_TIMEOUT, max_tokens=CHECK_MAX_TOKENS,
                             schema=ANSWER_AUDIT_SCHEMA, schema_name="answer_audit", label="思考模式回答核对")
    if not outcome.ok:
        return Audit(ok=False, skipped=True, reason=outcome.reason)
    result = outcome.value
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        log.warning("思考模式回答核对：模型返回的内容无法解析：%s", clip(repr(result), 200))
        return Audit(ok=False, skipped=True, reason="核对结果无法解析")
    issues = clean_list(result.get("issues"), 6, 300)
    ok = result["ok"]
    if (ok and issues) or (not ok and not issues):
        log.warning("思考模式回答核对：结论与问题列表矛盾，未确认通过")
        return Audit(ok=False, skipped=True, reason="核对结论与问题列表不一致，未确认通过")
    return Audit(ok=ok, issues=[] if ok else issues)


# ---------------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------------


def _activity(text: str) -> dict[str, Any]:
    """临时状态：大模型在忙、界面上没有别的变化时，告诉用户当前在做什么。不存库，下一个事件到来就消失。"""
    return {"type": "activity", "text": text}


def _note(content: str, step: int | None = None) -> dict[str, Any]:
    """正文里的一段说明（开场白、每一项的开场和汇报）。"""
    return {"type": "note", "content": content, **({"step_num": step} if step is not None else {})}


def _record_line(*, step_digest: list[dict[str, Any]], verdict: Verdict, retried: bool, first_issues: list[str]) -> str:
    """这一项的核对情况，写进终稿提示的「核对记录」。只写系统真的做过的核对。"""
    if not step_digest:
        return "没有调用工具，未单独核对"
    if verdict.skipped:
        why = public_sentence(verdict.reason, 100)
        return f"核对没有完成（{why}）" if why else "未单独核对"
    if verdict.passed:
        detail = f"：{public_sentence(verdict.checked)}" if public_sentence(verdict.checked) else ""
        earlier = f"（第一次核对发现「{public_sentence(first_issues[0], 80)}」，补做后通过）" if retried and first_issues else ""
        return f"核对通过{detail}{earlier}"
    problems = "；".join(item for item in (public_sentence(issue, 100) for issue in verdict.issues[:3]) if item) or "结果与预期不符"
    return f"核对发现问题{'，补做后仍然存在' if retried else ''}：{problems}"


def check_record(plan: Plan, ledger: _Ledger) -> str:
    """系统实际执行过的核对，终稿的「核对情况」只能依据它来写。"""
    lines = []
    if plan.reviewed:
        lines.append("- 计划审查：" + ("对照数据审查后做了修订" if plan.changes else "通过"))
    elif plan.reason and not plan.fallback:
        lines.append(f"- 计划审查：没有完成（{public_sentence(plan.reason, 80) or '原因不明'}）")
    lines.extend(f"- {label}：{status}" for label, status in ledger.checks)
    lines.append("（最终回答发出前，系统还会再对照工具结果核对一次，结果由系统另行告知用户，回答里不要提这一次。）")
    return "\n".join(lines)


async def _stream(executor: AgentExecutor, messages: list[dict[str, Any]], tools: list[Any], ctx: AgentContext, budget: Any,
                  run: _Run, ledger: _Ledger, *, step: int | None, scope: str, activity: str = "") -> AsyncIterator[dict[str, Any]]:
    """跑一次 ReAct 循环。step 不为空时是某一项：文字是这一项的汇报，不是最终回答；
    为空时是最终回答阶段：回答文字先扣在 run.held 里，等核对完再放出。
    activity 不为空时，每次工具返回之后补一条临时状态：模型接着分析、写汇报时界面上没有别的变化，要让用户知道还在做什么。"""
    async for event in executor.agent_stream(messages, tools, ctx, transcript=run.transcript, budget=budget,
                                             scope=scope, stop_scope="step" if step is not None else "task"):
        kind = event["type"]
        if kind == "text_chunk":
            if step is None:
                run.held.append(event)
            else:
                run.text += event["content"]
            continue
        if kind == "final_text":
            if step is None:
                run.held.append(event)
            else:
                run.text = event.get("content", "")
            continue
        if kind == "note" and step is not None:
            run.text = ""
            content = short_step_text(event.get("content", ""))
            if content:
                yield {**event, "step_num": step, "content": content}
            continue
        run.observe(event, ledger)
        yield event
        if kind == "error":
            run.failed = True
            run.error = event.get("content") or "执行失败"
            return
        if kind == "tool_result" and activity:
            yield _activity(activity)


async def execute_thinking(executor: AgentExecutor, ctx: AgentContext, message: str, history: list[dict[str, Any]],
                           steps: list[dict[str, Any]] | None = None) -> AsyncIterator[dict[str, Any]]:
    """steps 不为空时使用调用方自带的步骤，跳过起草和审查，其余核对照常进行。"""
    llm = executor.llm
    configure = getattr(executor, "configure_tools", None)
    if callable(configure):
        configure(ctx, message)
    yield {"type": "think_start"}

    # ---- 1~2. 勘察数据、起草并审查计划
    if steps:
        plan = plan_from_steps(steps)
    else:
        yield _activity("正在了解数据、制定计划")
        brief = await _in_thread(ctx, data_brief, ctx)
        plan = await make_plan(llm, ctx, message, history, brief)
        if plan.worth_reviewing:
            yield _activity("正在审查计划")
        plan = await review_plan(llm, ctx, message, plan, brief, history)
        yield {"type": "plan_review", "reviewed": plan.reviewed, "changes": plan.changes, "fallback": plan.fallback,
               "step_count": len(plan.steps), "reason": plan.reason}
    intro = plan.intro()
    if intro:
        yield _note(intro)
    total = len(plan.steps)
    # 开启思考模式即显示紧凑进度；show_plan 仅影响最终回答是否逐项回顾。
    yield {"type": "plan_start", "title": plan.title, "checklist": bool(plan.steps),
           "steps": [{"id": s["id"], "description": plan.display_description(index)}
                     for index, s in enumerate(plan.steps, 1)]}

    # ---- 3. 逐项执行并核对
    tools = executor.registry.for_context(ctx)
    seen = "用户已经看到进度面板中的简短步骤，不必复述完整计划。"
    if plan.narrated:
        seen += "每一项开始时也会看到一句说明。"
    conversation: list[dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt(ctx, message, thinking=True)}, *model_history(history),
        {"role": "user", "content": user_content(THINK_TASK_MESSAGE.format(message=message, plan=plan.overview(), seen=seen), ctx.image_parts)},
    ]
    budget = executor.new_budget()
    ledger = _Ledger()
    health = _CheckHealth()
    executed = 0
    carry = ""
    step_states: dict[int, str] = {}
    pending: list[str] = []
    for num, step in enumerate(plan.steps, start=1):
        executed = num
        description = step["description"]
        dependencies = step.get("depends_on", [num - 1] if num > 1 else [])
        if any(step_states.get(dep) != "done" for dep in dependencies):
            step_states[num] = "blocked"
            pending.append(description)
            yield {"type": "step_done", "step_num": num, "status": "warning", "quiet": True,
                   "summary": "依赖步骤尚未通过验收，本项未执行。", "blocked": True, "depends_on": dependencies}
            continue
        doing = plan.doing(num)
        yield {"type": "step_start", "step_num": num, "total": total,
               "description": plan.display_description(num), "quiet": True}
        lead = plan.lead_in(num) if plan.narrated else ""
        if lead:
            yield _note(lead, num)  # 这一项开始前先说一句现在要做什么
        yield _activity(doing)
        # 每一项都刷新系统提示里的表清单：前面生成的表要让模型看得到
        conversation[0] = {"role": "system", "content": build_system_prompt(ctx, message, thinking=True)}
        conversation.append({"role": "user", "content": THINK_STEP_MESSAGE.format(
            num=num, total=total, description=description,
            check=step.get("check") or "本项目标完成，结论和数字都有工具返回的数据支撑",
            carry=f"\n前面的提醒：{carry}" if carry else "",
            opener=THINK_OPENER_SAID if lead else THINK_OPENER_UNSAID,
        )})
        step_digest: list[dict[str, Any]] = []
        verdict = Verdict()
        run = _Run()
        retried = False
        first_issues: list[str] = []
        report = ""
        exhausted = False
        for attempt in range(MAX_STEP_FIXES + 1):
            run = _Run()
            async for event in _stream(executor, conversation, tools, ctx, budget, run, ledger, step=num, activity=doing,
                                       scope=f"本项目标：{description}\n验收：{step.get('check') or '依据实际结果完成本项'}"):
                yield event
            conversation.extend(run.transcript)
            digest = _tool_digest(run.transcript, run.results_ok, num, run.protocol_results, run.evidence)
            step_digest.extend(digest)
            ledger.digests.extend(digest)
            if run.failed or run.waiting:
                break
            exhausted = executor.budget_exhausted(budget)
            if exhausted:
                # Tool exhaustion prevents new work; it does not revoke the
                # completion check that just verified this finished step.
                verdict = (Verdict(passed=True, skipped=True) if run.verified_complete
                           else Verdict(passed=False, skipped=True, reason="本次处理的操作次数已用完"))
            elif run.unresolved:
                # 最后一次操作仍然报错：不用再问模型，直接带着事实补做（给用户看的话里不出现工具名）
                verdict = Verdict(
                    passed=False,
                    issues=["最后一次操作仍然报错，问题没有解决"],
                    advice=f"先查明「{'、'.join(sorted(run.unresolved))}」报错的原因，换一种做法重新完成本项。",
                )
            elif not step_digest:
                verdict = Verdict(skipped=True)  # 这一项没调用过任何工具，没有可核对的依据，不为它多花一次模型调用
            elif health.degraded:
                verdict = Verdict(skipped=True, reason="前面的核对已超时，这一项没有再等待核对")
            else:
                yield _activity("正在核对这一项的结果")
                verdict = await verify_step(llm, message, plan, step, num, run, step_digest)
                if verdict.timed_out:
                    health.degraded = True  # 模型服务卡住时，后面的核对不再各等一轮
            retry = not verdict.skipped and not verdict.passed and attempt < MAX_STEP_FIXES and not exhausted
            # 完整报告用于后续执行与核对，面板稍后只展示一行摘要。
            report = tidy_report(run.text)
            # 要补做时不展示有问题的数字；完整报告和核对证据仍留在执行对话里。
            if retry:
                issue = short_step_text(verdict.issues[0] if verdict.issues else "结果与预期不符", limit=20)
                yield _note(short_step_text(f"正在补充：{issue}"), num)
            if not verdict.skipped:
                yield {"type": "step_check", "step_num": num, "passed": verdict.passed, "issues": verdict.issues,
                       "retry": retry, "checked": verdict.checked}
            elif verdict.reason:  # 该核对而没核对成：说明原因
                yield {"type": "step_check", "step_num": num, "passed": None, "skipped": True, "reason": verdict.reason,
                       "issues": [], "retry": False}
            if not retry:
                break
            retried, first_issues = True, verdict.issues
            conversation.append({"role": "user", "content": THINK_FIX_MESSAGE.format(
                num=num, issues="\n".join(f"- {issue}" for issue in verdict.issues),
                advice=f"建议：{verdict.advice}\n" if verdict.advice else "",
            )})
            yield _activity(doing)

        if run.failed:
            summary = step_summary("error", reason=run.error)
            yield _note(summary, num)
            yield {"type": "step_done", "step_num": num, "status": "error", "summary": summary, "quiet": True}
            return
        if run.waiting:
            summary = step_summary("waiting", reason=run.question)
            yield _note(summary, num)
            yield {"type": "step_done", "step_num": num, "status": "waiting", "summary": summary, "quiet": True}
            return
        ledger.summaries[num] = report + (f"\n（核对仍有问题：{'；'.join(verdict.issues[:3])}）" if not verdict.passed and verdict.issues else "")
        ledger.checks.append((public_sentence(description, 60) or f"第 {num} 项",
                              _record_line(step_digest=step_digest, verdict=verdict, retried=retried, first_issues=first_issues)))
        status = "warning" if (run.incomplete or run.unresolved or not verdict.passed
                               or (exhausted and not run.verified_complete)
                               or (verdict.skipped and verdict.reason)) else "done"
        summary = step_summary(status, report=report,
                               checked=verdict.checked if not verdict.skipped else "",
                               reason=verdict.reason,
                               issues=verdict.issues)
        yield _note(summary, num)
        yield {"type": "step_done", "step_num": num, "status": status, "summary": summary, "quiet": True}
        known_failure = bool(run.incomplete or run.unresolved or (exhausted and not run.verified_complete)
                             or (not verdict.skipped and not verdict.passed))
        step_states[num] = "failed" if known_failure else "done"
        if known_failure:
            pending.append(description)
        if exhausted or run.incomplete:
            if exhausted:
                pending.extend(item["description"] for item in plan.steps[num:])
                break
        carry = verdict.carry_over

    if pending:
        answer = "本轮尚未完成，部分步骤未通过验收或依赖未就绪。\n\n仍需完成：" + "；".join(pending)
        yield {"type": "execution_stop", "scope": "task", "status": "incomplete", "code": "plan_incomplete",
               "summary": answer, "verified": False}
        yield {"type": "text_chunk", "content": answer}
        yield {"type": "final_text", "content": answer}
        return

    # ---- 4. 整理结论；最终回答先扣住，核对后再发出
    yield {"type": "reflect_start", "quiet": True}
    yield _activity("正在整理结论")
    conversation[0] = {"role": "system", "content": build_system_prompt(ctx, message, thinking=True)}
    record = check_record(plan, ledger)
    template = THINK_REFLECT_MESSAGE if plan.checklist else THINK_REFLECT_MESSAGE_SMALL
    reflect = template.format(message=message, total=executed, record=record)
    if executed < total:
        reflect += f"\n计划中还有 {total - executed} 个步骤未执行，不得称已完成。"
    reflect_messages = [*conversation, {"role": "user", "content": reflect}]
    final = _Run()
    async for event in _stream(executor, reflect_messages, tools, ctx, budget, final, ledger, step=None, activity="正在整理结论",
                               scope=f"原始任务：{message}\n根据实际执行证据整理最终回答，核对全部目标是否完成。"):
        yield event
    held = final.held
    ledger.digests.extend(_tool_digest(final.transcript, final.results_ok, 0, final.protocol_results, final.evidence))
    answer = _answer_text(held)
    if answer and not final.failed and not final.waiting and ledger.digests:
        yield {"type": "audit_start"}
        yield _activity("正在核对回答里的数字和结论")
        audit = await audit_answer(llm, message, plan, answer, ledger, record)
        corrected = False
        revalidated: Audit | None = None
        fix_incomplete = False
        if not audit.skipped and not audit.ok:
            yield _activity("核对发现出入，正在核实更正")
            fix = _Run()
            fix_messages = [*reflect_messages, *final.transcript, {"role": "user", "content": THINK_AUDIT_FIX_MESSAGE.format(
                issues="\n".join(f"- {issue}" for issue in audit.issues))}]
            async for event in _stream(executor, fix_messages, tools, ctx, budget, fix, ledger, step=None, activity="正在核实更正",
                                       scope=f"原始任务：{message}\n核实更正回答并确认全部目标已完成。"):
                yield event
            # A correction is only publishable after the same audit is run
            # against the corrected answer. A successful ReAct stop or a
            # piece of text alone is not evidence that the original issue was
            # fixed. Include correction evidence in the revalidation ledger.
            ledger.digests.extend(_tool_digest(fix.transcript, fix.results_ok, 0, fix.protocol_results, fix.evidence))
            fix_answer = _answer_text(fix.held)
            fix_incomplete = fix.failed or fix.waiting or fix.incomplete or not fix_answer
            if not fix.failed and fix.waiting:
                held = fix.held  # 澄清卡片代替旧答案，但不得声称已经更正。
            if not fix_incomplete:
                revalidation_record = record + "\n必须重新核对的原问题：\n" + "\n".join(audit.issues)
                revalidated = await audit_answer(llm, message, plan, fix_answer, ledger, revalidation_record)
                if not revalidated.skipped and revalidated.ok:
                    held = fix.held
                    corrected = True
        # 结果是结构化事件：界面在回答下面显示成一行小标识，不往回答文字里追加（追加的话会进入后续对话的历史）
        displayed_audit = revalidated if revalidated is not None else audit
        if not audit.skipped and not audit.ok and not corrected and not (not fix.failed and fix.waiting):
            # Do not publish an answer already known to conflict with the
            # execution evidence when correction failed or could not be
            # revalidated. The recorded tools remain visible for diagnosis.
            unresolved = displayed_audit.issues or audit.issues
            reason = displayed_audit.reason if displayed_audit.skipped else "；".join(unresolved)
            failure_text = "本轮结果仍未通过核对，尚未确认完成。" + (reason or "关键结果需要继续核实。")
            held = [{"type": "final_text", "content": failure_text}]
            yield {"type": "execution_stop", "scope": "task", "status": "incomplete",
                   "code": "answer_audit_failed", "summary": failure_text, "verified": False}
        yield {"type": "audit_done", "ok": None if displayed_audit.skipped else displayed_audit.ok,
               "issues": displayed_audit.issues if revalidated is not None else audit.issues,
               "skipped": displayed_audit.skipped, "corrected": corrected,
               "revalidated": revalidated is not None,
               "revalidation_ok": (None if revalidated is None or revalidated.skipped else revalidated.ok),
               "fix_incomplete": fix_incomplete,
               **({"reason": displayed_audit.reason} if displayed_audit.skipped and displayed_audit.reason else {})}
    # 出错时已经给了错误提示，扣住的半截文字不能当作完整回答放出来（不依赖执行器怎样攒文字）
    if not final.failed:
        text = _answer_text(held)
        if text:
            cleaned = tidy_report(text) or text  # 模型偶尔还是会写「第 N 步」式的标题，兜底去掉；不改数字和结论
            yield {"type": "text_chunk", "content": cleaned}
            yield {"type": "final_text", "content": cleaned}
        else:
            for event in held:
                yield event
    yield {"type": "reflect_done"}
