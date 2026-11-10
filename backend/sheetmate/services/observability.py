"""Bounded, private aggregates over durable tasks, without copying their text.

One execution is counted once even after recovery. Latest persisted usage wins;
events contain repeated cumulative snapshots and must never be summed.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session

from sheetmate.db.models import AgentExecution, AgentToolReceipt, Conversation, Customer, Message, User, UserCustomer

MAX_EXECUTIONS = 5000
STATUSES = ("completed", "failed", "incomplete", "cancelled", "interrupted", "waiting", "running", "other")
_CODES = frozenset({
    "execution_error", "tool_error", "tool_timeout", "task_timeout", "model_timeout", "request_timeout",
    "model_call_budget", "prompt_token_budget", "token_budget", "input_budget", "tool_budget",
    "missing_tool_result", "missing_completion", "missing_delivery", "delivery_unverified",
    "completion_unverified", "model_error", "protocol_error", "tool_protocol", "loop_detected",
    "max_iterations", "watchdog_timeout", "recovery_blocked", "recovery_expired",
})
_STAGES = frozenset({"chat", "execution", "structured", "request_intent", "execution_plan", "plan_review",
    "plan_repair", "step_verdict", "answer_audit", "task_completion", "final_delivery", "recipe",
    "memory_extraction", "title", "history_compaction", "customer_summary", "recipe_persistence", "recipe_deletion"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    return float(value) if type(value) in {int, float} and math.isfinite(value) and value >= 0 else None


def _count(value: Any, default: int = 0) -> int:
    number = _number(value)
    return int(number) if number is not None else default


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(len(ordered) * fraction) - 1)], 1)


def _empty_usage() -> dict[str, int]:
    return dict.fromkeys(("model_calls", "prompt_tokens", "completion_tokens", "usage_missing_calls",
        "cached_tokens", "cache_miss_tokens", "cached_tokens_missing_calls", "cache_miss_tokens_missing_calls",
        "inflight_calls", "pending_tasks", "missing_tasks"), 0)


def _add_usage(total: dict[str, int], usage: dict[str, Any], calls: int) -> None:
    for key in ("prompt_tokens", "completion_tokens", "cached_tokens", "cache_miss_tokens"):
        total[key] += _count(usage.get(key))
    total["model_calls"] += calls
    inflight = _count(_dict(usage.get("budget")).get("inflight_calls"))
    total["inflight_calls"] += inflight
    total["usage_missing_calls"] += min(calls, _count(usage.get("usage_missing_calls"), calls if not usage else 0))
    for key in ("cached_tokens_missing_calls", "cache_miss_tokens_missing_calls"):
        total[key] += min(calls, _count(usage.get(key), max(0, calls - inflight)))


def _checkpoint_usage(row: Any) -> dict[str, Any]:
    budget, raw, stages = _dict(row["saved_budget"]), _dict(row["saved_usage"]), _dict(row["saved_stages"])
    if not budget and not raw and not stages:
        return {}
    calls = _count(budget.get("calls"))
    inflight = len(_dict(budget.get("active")))
    recorded = sum(_count(_dict(value).get("calls")) for value in stages.values())
    lost = max(0, calls - recorded - inflight)
    result = {**raw, "model_calls": calls, "stages": stages,
              "budget": {"model_calls": calls, "inflight_calls": inflight,
                         "elapsed_seconds": budget.get("elapsed_seconds")},
              "usage_missing_calls": lost + sum(_count(_dict(v).get("usage_missing_calls")) for v in stages.values())}
    for key in ("cached_tokens", "cache_miss_tokens"):
        result[key] = sum(_count(_dict(v).get(key)) for v in stages.values())
        result[f"{key}_missing_calls"] = lost + sum(
            _count(_dict(v).get(f"{key}_missing_calls"), _count(_dict(v).get("calls"))) for v in stages.values())
    return result


def _usage_progress(usage: dict[str, Any]) -> tuple[int, int, int]:
    budget = _dict(usage.get("budget"))
    calls = _count(usage.get("model_calls"), _count(budget.get("model_calls")))
    return (max(0, calls - _count(budget.get("inflight_calls"))), calls,
            _count(usage.get("prompt_tokens")) + _count(usage.get("completion_tokens")))


def _settle_abandoned_usage(usage: dict[str, Any], status: str, *, pending: bool = False) -> dict[str, Any]:
    if status == "running" or status == "completed" and pending:
        return usage
    budget = _dict(usage.get("budget"))
    inflight = _count(budget.get("inflight_calls"))
    if not inflight:
        return usage
    calls = _count(usage.get("model_calls"), _count(budget.get("model_calls")))
    result = {**usage, "budget": {**budget, "inflight_calls": 0},
              "usage_missing_calls": _count(usage.get("usage_missing_calls")) + inflight}
    for key in ("cached_tokens_missing_calls", "cache_miss_tokens_missing_calls"):
        result[key] = _count(usage.get(key), max(0, calls - inflight)) + inflight
    return result


def execution_metrics(db: Session, user: User, *, days: int = 30, at: datetime | None = None) -> dict[str, Any]:
    """Only the caller's tasks in currently accessible conversations are visible.

    Administrators also get their own tasks here. No account-wide scope or raw
    prompts/errors/arguments are exposed through the statistics endpoint.
    """
    if type(days) is not int or not 1 <= days <= 90:
        raise ValueError("统计范围需为 1–90 天")
    end = at or datetime.now()
    start = end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    filters = [AgentExecution.user_id == user.id, Conversation.user_id == user.id,
               AgentExecution.created_at >= start, AgentExecution.created_at <= end]
    if user.role != "admin":
        grant = exists(select(UserCustomer.user_id).where(
            UserCustomer.user_id == user.id, UserCustomer.customer_id == Conversation.customer_id))
        owner = exists(select(Customer.id).where(Customer.id == Conversation.customer_id, Customer.created_by == user.id))
        filters.append(or_(Conversation.customer_id.is_(None), grant, owner))

    # Project only counters. Loading checkpoints, event histories or message
    # bodies here would turn a small dashboard into a full transcript scan.
    selected = (select(AgentExecution.id).join(Conversation, Conversation.id == AgentExecution.conversation_id)
        .where(*filters).order_by(AgentExecution.created_at.desc(), AgentExecution.request_message_id.desc())
        .limit(MAX_EXECUTIONS))
    recovered_reply = (select(Message.id).where(Message.conversation_id == AgentExecution.conversation_id,
        Message.role == "assistant", Message.meta["execution_id"].as_string() == AgentExecution.id)
        .order_by(Message.id.desc()).limit(1).correlate(AgentExecution).scalar_subquery())
    meta = Message.meta["execution"]
    rows = db.execute(select(
        AgentExecution.id, AgentExecution.created_at, AgentExecution.status, AgentExecution.recovery_attempts,
        AgentExecution.checkpoint["request_budget"].label("saved_budget"),
        AgentExecution.checkpoint["llm_usage"]["usage"].label("saved_usage"),
        AgentExecution.checkpoint["llm_usage"]["stages"].label("saved_stages"),
        meta["status"].as_string().label("outcome"), meta["code"].as_string().label("code"),
        meta["duration_ms"].as_float().label("duration"), meta["llm_usage"].label("usage"),
        meta["llm_usage_pending"].as_boolean().label("usage_pending"),
    ).join(Conversation, Conversation.id == AgentExecution.conversation_id)
      .outerjoin(Message, and_(Message.id == func.coalesce(AgentExecution.assistant_message_id, recovered_reply),
                              Message.conversation_id == AgentExecution.conversation_id, Message.role == "assistant"))
      .where(*filters).order_by(AgentExecution.created_at.desc(), AgentExecution.request_message_id.desc())
      .limit(MAX_EXECUTIONS + 1)).mappings().all()
    truncated = len(rows) > MAX_EXECUTIONS
    rows = rows[:MAX_EXECUTIONS]
    statuses: Counter[str] = Counter({key: 0 for key in STATUSES})
    failures: Counter[str] = Counter()
    daily = {(start + timedelta(days=index)).date().isoformat(): {
        "tasks": 0, "completed": 0, "failed": 0, "prompt_tokens": 0, "completion_tokens": 0,
    } for index in range(days)}
    usage_total = _empty_usage()
    stages: dict[str, dict[str, int]] = {}
    durations: list[float] = []
    recovered_tasks = 0
    for row in rows:
        durable_status, outcome = row["status"], row["outcome"]
        # A paused task can be waiting for input or stopped as incomplete.
        # Running/interrupted durable checkpoints outrank an older UI snapshot.
        status = durable_status
        if durable_status == "paused":
            status = outcome if outcome in {"incomplete", "waiting"} else "waiting"
        elif durable_status in {"blocked", "expired"}:
            status = "incomplete"
        status = status if status in STATUSES else "other"
        statuses[status] += 1
        recovered_tasks += int(row["recovery_attempts"] > 0)
        day = daily[row["created_at"].date().isoformat()]
        day["tasks"] += 1
        day["completed"] += int(status == "completed")
        day["failed"] += int(status in {"failed", "incomplete"})
        if status in {"failed", "incomplete"}:
            code = row["code"]
            failures[code if code in _CODES else "other"] += 1
        usage = _settle_abandoned_usage(_dict(row["usage"]), durable_status, pending=bool(row["usage_pending"]))
        budget = _dict(row["saved_budget"])
        saved_usage = _settle_abandoned_usage(_checkpoint_usage(row), durable_status)
        # Checkpoints can outrun the UI after recovery; background title/memory
        # calls can subsequently make message metadata newer. Choose one whole
        # cumulative snapshot, never add or mix their overlapping counters.
        if not usage or _usage_progress(saved_usage) > _usage_progress(usage):
            usage = saved_usage
            usage_total["missing_tasks"] += int(not _dict(row["saved_usage"]))
        calls = _count(usage.get("model_calls"), _count(_dict(usage.get("budget")).get("model_calls")))
        usage_total["pending_tasks"] += int(bool(row["usage_pending"]))
        _add_usage(usage_total, usage, calls)
        for key in ("prompt_tokens", "completion_tokens"):
            day[key] += _count(usage.get(key))
        for name, value in _dict(usage.get("stages")).items():
            stage = name if name in _STAGES else "other"
            aggregate = stages.setdefault(stage, _empty_usage())
            item = _dict(value)
            _add_usage(aggregate, item, _count(item.get("calls")))
        # Restored request budgets keep active elapsed time across attempts;
        # wall-clock gaps while the app is off must not inflate task latency.
        elapsed = _number(budget.get("elapsed_seconds"))
        snapshot_elapsed = _number(_dict(usage.get("budget")).get("elapsed_seconds"))
        duration = _number(row["duration"])
        samples = [v for v in (duration, elapsed * 1000 if elapsed is not None else None,
                              snapshot_elapsed * 1000 if snapshot_elapsed is not None else None) if v is not None]
        if samples and status in {"completed", "failed", "incomplete", "cancelled"}:
            durations.append(max(samples))

    # Receipts replayed after a restart retain their original started_at. Their
    # wall-clock interval includes downtime and is not an active-time sample.
    tool_duration = case((AgentExecution.recovery_attempts == 0,
        (func.julianday(AgentToolReceipt.finished_at) - func.julianday(AgentToolReceipt.created_at)) * 86400000), else_=None)
    tool_rows = db.execute(select(AgentToolReceipt.tool_name, AgentToolReceipt.status,
        func.count().label("calls"),
        func.avg(tool_duration).label("duration_ms"), func.count(tool_duration).label("duration_samples"),
    ).join(AgentExecution, AgentExecution.id == AgentToolReceipt.execution_id)
     .where(AgentToolReceipt.execution_id.in_(selected))
     .group_by(AgentToolReceipt.tool_name, AgentToolReceipt.status)).mappings().all()
    tools: dict[str, dict[str, Any]] = {}
    for row in tool_rows:
        # Tool definitions use machine identifiers. Unexpected identifiers are
        # folded away so arbitrary stored text never becomes a metric label.
        name = row["tool_name"]
        name = name if isinstance(name, str) and name.isascii() and name.replace("_", "").isalnum() and len(name) <= 64 else "other"
        item = tools.setdefault(name, {"tool": name, "calls": 0, "completed": 0, "failed": 0, "pending": 0,
                                       "duration_total": 0.0, "duration_samples": 0})
        count = row["calls"]
        item["calls"] += count
        key = row["status"] if row["status"] in {"completed", "failed"} else "pending"
        item[key] += count
        duration = _number(row["duration_ms"])
        if duration is not None:
            item["duration_total"] += duration * row["duration_samples"]
            item["duration_samples"] += row["duration_samples"]
    for item in tools.values():
        samples = item["duration_samples"]
        total = item.pop("duration_total")
        item["average_duration_ms"] = round(total / samples, 1) if samples else None
    denominator = statuses["completed"] + statuses["failed"] + statuses["incomplete"]
    cache_known = not usage_total["cached_tokens_missing_calls"] and not usage_total["cache_miss_tokens_missing_calls"]
    cache_input = usage_total["cached_tokens"] + usage_total["cache_miss_tokens"]
    return {
        "scope": "own", "days": days, "from": start.isoformat(), "to": end.isoformat(),
        "coverage": {"source": "durable_executions", "limit": MAX_EXECUTIONS, "truncated": truncated},
        "tasks": len(rows), "statuses": dict(statuses), "recovered_tasks": recovered_tasks,
        "success_rate": round(statuses["completed"] / denominator, 4) if denominator else None,
        "success_rate_tasks": denominator,
        "latency": {"samples": len(durations), "p50_ms": _percentile(durations, .5), "p95_ms": _percentile(durations, .95)},
        "usage": {**usage_total, "total_tokens": usage_total["prompt_tokens"] + usage_total["completion_tokens"],
                  "cache_hit_rate": round(usage_total["cached_tokens"] / cache_input, 4) if cache_known and cache_input else None},
        "daily": [{"date": key, **value} for key, value in daily.items()],
        "failures": [{"code": key, "tasks": value} for key, value in failures.most_common()],
        "stages": [{"stage": key, **value} for key, value in sorted(stages.items())],
        "tools": sorted(tools.values(), key=lambda item: (-item["calls"], item["tool"]))[:50],
    }
