"""Learn small technical repair hints from completed, verified executions.

Only the static reflection catalog becomes reusable text. Execution identities
stay in a separate audit field; customer data, generated code and model-written
explanations never become rules. Learning makes no model calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from sheetmate.agent.framework.react.reflection import classify_failure, failure_advice
from sheetmate.agent.tools.results import result_reference
from sheetmate.db.models import (
    AgentExecution,
    AgentToolOutput,
    AgentToolReceipt,
    Conversation,
    FileRecord,
    Memory,
    Message,
    RecipeRecord,
    User,
)
from sheetmate.services.identity.auth import require_customer
from sheetmate.core.errors import ServiceError
from sheetmate.services.execution.state import recovery_input_fingerprint
from sheetmate.services.files.service import can_access_in_conversation
from sheetmate.services.memory.repository import set_memory

_RECIPE_TOOLS = {"build_recipe", "revise_recipe", "run_recipe"}
_PROCESSING_TOOLS = _RECIPE_TOOLS | {"run_sql", "run_python_script"}
_MAX_EXPERIENCES = 3


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _json_prefix(text: Any) -> dict[str, Any]:
    if not isinstance(text, str):
        return {}
    try:
        value, _end = json.JSONDecoder().raw_decode(text.lstrip())
    except (TypeError, ValueError):
        return {}
    return _dict(value)


def _failure_text(payload: dict[str, Any]) -> str:
    report = _json_prefix(payload.get("text"))
    if report and (report.get("error") or report.get("stderr")):
        return "\n".join(str(report.get(key) or "") for key in ("error", "stderr"))
    return str(payload.get("text") or "")


def _payload(db: Session, conv: Conversation, execution: AgentExecution,
             receipt: AgentToolReceipt) -> dict[str, Any]:
    payload = _dict(receipt.result)
    reference = result_reference(str(payload.get("text") or ""))
    if not reference:
        return payload
    # The caller already validated live conversation/customer ownership. The
    # durable output must additionally belong to this particular tool receipt.
    output = db.get(AgentToolOutput, reference)
    if (output is None or output.conversation_id != conv.id or output.user_id != execution.user_id
            or output.customer_id != conv.customer_id or output.execution_id != execution.id
            or output.call_id != receipt.call_id or output.tool_name != receipt.tool_name):
        return {}
    try:
        return _dict(json.loads(output.payload))
    except (TypeError, ValueError):
        return {}


def _source_versions(evidence: dict[str, Any]) -> list[dict[str, Any]] | None:
    sources = _dicts(evidence.get("source_tables"))
    if evidence.get("omitted_sources") or len(sources) > 20:
        return None
    result = []
    for source in sources:
        if not source.get("table") or not source.get("revision"):
            return None
        if source.get("file_id") and not source.get("file_version"):
            return None
        result.append({key: source.get(key) for key in ("table", "revision", "file_id", "file_version")})
    return sorted(result, key=lambda item: str(item["table"]))


def _baseline_versions(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    files, tables = _dict(inputs.get("files")), _dict(inputs.get("tables"))
    if len(files) + len(tables) > 20:
        return []
    result = [{"table": name, "revision": revision, "file_id": None, "file_version": None}
              for name, revision in sorted(tables.items()) if name and revision]
    result.extend({"table": None, "revision": None, "file_id": file_id, "file_version": version}
                  for file_id, version in sorted(files.items()) if file_id and version and version != "missing")
    return result


def _sources_available(db: Session, conv: Conversation, sources: list[dict[str, Any]]) -> bool:
    for source in sources:
        if not source.get("file_id"):
            continue
        record = db.get(FileRecord, source["file_id"])
        if (record is None or not Path(record.stored_path).is_file()
                or not can_access_in_conversation(db, record, conversation_id=conv.id, customer_id=conv.customer_id)):
            return False
    return bool(sources)


def _invalid_results(execution: AgentExecution) -> set[str]:
    budget = _dict(_dict(execution.checkpoint).get("runtime")).get("budget")
    budget = _dict(budget)
    invalid = set()
    for evidence_id, result in _dict(budget.get("results")).items():
        if _dict(result).get("invalid"):
            invalid.update(str(item) for item in (evidence_id, result.get("call_id")) if item)
    for row in _dicts(budget.get("recoveries")):
        state = _dict(row.get("state"))
        invalid.update(str(item) for item in state.get("invalid_results", []) if isinstance(item, str))
    return invalid


def _passing_checks(checks: Any) -> bool:
    return bool(checks) and isinstance(checks, list) and all(
        isinstance(check, dict) and check.get("ok") is True for check in checks
    )


def _recipe_context(events: list[dict[str, Any]]) -> tuple[dict[str, Any], int | None]:
    selected = next((_dict(event.get("selected_run")) for event in reversed(events)
                     if event.get("type") in {"recipe_completed", "recipe_saved"}
                     and isinstance(event.get("selected_run"), dict)), {})
    if selected:
        report = _dict(selected.get("report"))
        comparison = _dict(selected.get("verification"))
        if comparison and not comparison.get("informational") and comparison.get("status") != "pass":
            return {}, None
        return report, selected.get("round")
    report = next((_dict(event.get("report")) for event in reversed(events)
                   if event.get("type") == "recipe_run_done"), {})
    return report, None


def _recipe_passed(report: dict[str, Any]) -> bool:
    validation = _dict(report.get("validation"))
    checks = _dicts(validation.get("checks"))
    return (report.get("status") in {"success", "warning"} and validation.get("status") == "pass"
            and bool(checks) and all(item.get("status") == "pass" for item in checks))


@dataclass
class _Observed:
    index: int
    event: dict[str, Any]
    receipt: AgentToolReceipt
    payload: dict[str, Any]
    sources: list[dict[str, Any]]
    check: dict[str, Any] | None = None
    verification: str = "completion_check"


def _completion_for(events: list[dict[str, Any]], index: int, evidence: dict[str, Any],
                    invalid: set[str]) -> dict[str, Any] | None:
    aliases = {evidence.get("call_id"), evidence.get("id")}
    aliases.discard(None)
    if aliases & invalid:
        return None
    for event in events[index + 1:]:
        if (event.get("type") == "completion_check" and event.get("status") == "completed"
                and not event.get("remaining") and isinstance(event.get("call_id"), str)
                and aliases.intersection(event.get("evidence_ids") or [])):
            return event
    return None


def _observations(db: Session, conv: Conversation, execution: AgentExecution,
                  events: list[dict[str, Any]], inputs: dict[str, Any]) -> list[_Observed]:
    receipts = {item.call_id: item for item in db.scalars(select(AgentToolReceipt).where(
        AgentToolReceipt.execution_id == execution.id))}
    baseline = _baseline_versions(inputs)
    invalid, seen, results = _invalid_results(execution), set(), []
    for index, event in enumerate(events):
        if event.get("type") != "tool_result":
            continue
        call_id, tool = event.get("id"), event.get("tool")
        if not isinstance(call_id, str) or call_id in seen or not isinstance(tool, str):
            continue
        seen.add(call_id)  # A replay of an earlier call cannot repair a later failure.
        if tool not in _PROCESSING_TOOLS and not tool.startswith("call_"):
            continue
        evidence, receipt = _dict(event.get("evidence")), receipts.get(call_id)
        if (receipt is None or receipt.tool_name != tool or evidence.get("call_id") != call_id
                or evidence.get("tool") != tool or not evidence.get("id")
                or evidence.get("executed") is not True or evidence.get("reused")
                or event.get("invalid") or evidence.get("invalid")
                or call_id in invalid or evidence["id"] in invalid):
            continue
        payload = _payload(db, conv, execution, receipt)
        ok = event.get("ok")
        if (not isinstance(ok, bool) or payload.get("ok") is not ok
                or receipt.status != ("completed" if ok else "failed")
                or evidence.get("status") != ("success" if ok else "failed")
                or payload.get("error_kind") in {"schema", "protocol"}
                or event.get("error_kind") in {"schema", "protocol"}):
            continue
        sources = _source_versions(evidence)
        if sources is None:
            continue
        if not sources:
            sources = baseline
        if not _sources_available(db, conv, sources):
            continue
        observed = _Observed(index, event, receipt, payload, sources)
        if ok:
            observed.check = _completion_for(events, index, evidence, invalid)
            if observed.check is None:
                continue
            if tool == "run_python_script":
                report = _json_prefix(payload.get("text"))
                if report.get("status") != "success" or not _passing_checks(report.get("checks")):
                    continue
                observed.verification = "python_checks"
            elif tool in _RECIPE_TOOLS:
                report, _round = _recipe_context(_dicts(payload.get("events")))
                if not _recipe_passed(report):
                    continue
                observed.verification = "recipe_verification"
        results.append(observed)
    return results


def _matched_sources(failed: _Observed, repaired: _Observed, kind: str) -> bool:
    # Both receipts bind the original inputs, even if an invalid table name
    # prevented the failing call from producing a table-specific snapshot.
    if not failed.receipt.input_digest or failed.receipt.input_digest != repaired.receipt.input_digest:
        return False
    if failed.sources == repaired.sources:
        return True
    if kind != "missing_table" or _dict(failed.event.get("evidence")).get("source_tables"):
        return False
    # A misspelled/nonexistent table produces no snapshot. In that specific
    # case the immutable input receipt and matching original versions prove
    # that correcting the name did not switch to another input dataset.
    tables = {item["table"]: item["revision"] for item in failed.sources if item.get("table")}
    files = {item["file_id"]: item["file_version"] for item in failed.sources if item.get("file_id")}
    return all(tables.get(item.get("table")) == item.get("revision")
               and (not item.get("file_id") or files.get(item["file_id"]) == item["file_version"])
               for item in repaired.sources)


def _recipe_repairs(observed: _Observed) -> list[tuple[str, dict[str, Any]]]:
    if observed.event.get("tool") not in _RECIPE_TOOLS or not observed.check:
        return []
    events = _dicts(observed.payload.get("events"))
    report, selected_round = _recipe_context(events)
    result = []
    for step in _dicts(report.get("steps")):
        if step.get("status") != "repaired" or any(
                item.get("status") != "pass" for item in _dicts(step.get("assertions"))):
            continue
        if step.get("kind") == "python" and not _passing_checks(step.get("checks")):
            continue
        step_id = step.get("step_id")
        related = [event for event in events if event.get("step_id") == step_id
                   and (selected_round is None or event.get("round") == selected_round)]
        error = next((event for event in related if event.get("type") == "recipe_step_error"
                      and classify_failure(observed.receipt.tool_name, str(event.get("error") or ""))), None)
        if error is None:
            continue
        kind = classify_failure(observed.receipt.tool_name, str(error.get("error") or ""))
        attempt = error.get("attempt")
        matching_attempt = any(item.get("attempt") == attempt and item.get("error") == error.get("error")
                               for item in _dicts(step.get("attempts")))
        error_index = related.index(error)
        retry_index = next((index for index, event in enumerate(related) if index > error_index
                            and event.get("type") == "recipe_repair" and event.get("status") == "retry"
                            and event.get("attempt") == attempt), None)
        done = retry_index is not None and any(
            event.get("type") == "recipe_step_done" and event.get("status") == "repaired"
            for event in related[retry_index + 1:])
        if kind and matching_attempt and done:
            result.append((kind, {"step_id": step_id, "failed_attempt": attempt,
                                   "recipe_round": selected_round}))
    return result


def _save_experience(db: Session, conv: Conversation, execution: AgentExecution, assistant: Message,
                     failed: _Observed, repaired: _Observed, kind: str,
                     step_evidence: dict[str, Any] | None = None) -> Memory | None:
    tool, advice = repaired.receipt.tool_name, failure_advice(kind)
    key = f"verified_repair:{tool}:{kind}"
    existing = db.scalar(select(Memory).where(Memory.customer_id == conv.customer_id,
        Memory.recipe_id == conv.recipe_id, Memory.category == "history_insights", Memory.key == key)
        .order_by((Memory.source == "human").desc(), Memory.id.desc()).limit(1))
    if existing is not None and (existing.source == "human" or (
            existing.value == advice and _dict(existing.execution_evidence).get("verified") is True)):
        return None
    item = set_memory(db, category="history_insights", key=key, value=advice,
                      customer_id=conv.customer_id, recipe_id=conv.recipe_id, source="ai",
                      source_message_id=assistant.id, source_turn_id=execution.id)
    if (getattr(item, "_write_status", "blocked") == "blocked" or item.source != "ai" or item.key != key
            or item.value != advice or item.source_message_id != assistant.id):
        return None
    failed_evidence, repaired_evidence = _dict(failed.event.get("evidence")), _dict(repaired.event.get("evidence"))
    audit = {
        "kind": "verified_repair", "verified": True, "failure_kind": kind, "tool": tool,
        "conversation_id": conv.id, "request_message_id": execution.request_message_id,
        "assistant_message_id": assistant.id, "execution_id": execution.id,
        "failed_call_id": failed.receipt.call_id, "failed_evidence_id": failed_evidence["id"],
        "repaired_call_id": repaired.receipt.call_id, "repaired_evidence_id": repaired_evidence["id"],
        "completion_check_id": repaired.check["call_id"],
        "verification": repaired.verification, "source_versions": repaired.sources,
        **(step_evidence or {}),
    }
    if step_evidence:
        audit["failed_evidence_id"] += f":step:{step_evidence['step_id']}:attempt:{step_evidence['failed_attempt']}"
    # Protect a concurrent human edit just as set_memory protects rule text.
    saved = db.execute(update(Memory).where(Memory.id == item.id, Memory.version == item.version,
        Memory.source == "ai", Memory.key == key, Memory.value == advice,
        Memory.source_message_id == assistant.id).values(execution_evidence=audit, version=item.version + 1)
        .execution_options(synchronize_session=False))
    if saved.rowcount != 1:
        db.expire(item)
        return None
    db.refresh(item)
    if getattr(item, "_write_status", "") == "unchanged":
        item._write_status = "updated"
    return item


def learn_verified_repairs(db: Session, *, conversation_id: str, user_id: int,
                           assistant_message_id: int, execution_id: str | None = None) -> list[Memory]:
    """Save at most three verified repairs in the current customer/recipe scope.

    Legacy/model-only events without durable execution receipts are deliberately
    ineligible. Missing evidence merely skips optional learning.
    """
    conv, user = db.get(Conversation, conversation_id), db.get(User, user_id)
    assistant = db.get(Message, assistant_message_id)
    if (conv is None or user is None or conv.user_id != user_id or not conv.customer_id
            or assistant is None or assistant.role != "assistant" or assistant.conversation_id != conv.id):
        return []
    try:
        require_customer(db, user, conv.customer_id)
    except ServiceError:
        return []
    terminal = _dict(_dict(assistant.meta).get("execution"))
    if terminal.get("status") != "completed" or terminal.get("verified") is not True:
        return []
    execution_id = execution_id or _dict(assistant.meta).get("execution_id")
    execution = db.get(AgentExecution, execution_id) if isinstance(execution_id, str) else None
    if (execution is None or execution.user_id != user_id or execution.conversation_id != conv.id
            or execution.status != "completed" or execution.assistant_message_id != assistant.id
            or _dict(assistant.meta).get("execution_id") not in {None, execution.id}):
        return []
    try:
        inputs = recovery_input_fingerprint(db, execution)
    except ServiceError:
        return []
    if inputs.get("customer_id") != conv.customer_id or inputs.get("recipe_id") != conv.recipe_id:
        return []
    if conv.recipe_id and db.scalar(select(RecipeRecord.id).where(
            RecipeRecord.recipe_id == conv.recipe_id, RecipeRecord.customer_id == conv.customer_id).limit(1)) is None:
        return []
    previous = db.scalars(select(Memory).where(Memory.customer_id == conv.customer_id,
        Memory.recipe_id == conv.recipe_id, Memory.category == "history_insights", Memory.source == "ai"))
    available = _MAX_EXPERIENCES - sum(
        _dict(item.execution_evidence).get("execution_id") == execution.id for item in previous)
    if available <= 0:
        return []
    observations = _observations(db, conv, execution, _dicts(assistant.events), inputs)
    learned, attempted = [], set()
    for repaired in observations:
        if not repaired.event.get("ok"):
            continue
        candidates: list[tuple[_Observed, str, dict[str, Any] | None]] = []
        for failed in reversed(observations):
            if (failed.index >= repaired.index or failed.event.get("ok")
                    or failed.receipt.tool_name != repaired.receipt.tool_name
                    or failed.receipt.id >= repaired.receipt.id):
                continue
            kind = classify_failure(failed.receipt.tool_name, _failure_text(failed.payload))
            if kind and _matched_sources(failed, repaired, kind):
                candidates.append((failed, kind, None))
                break
        candidates.extend((repaired, kind, evidence) for kind, evidence in _recipe_repairs(repaired))
        for failed, kind, step in candidates:
            identity = (repaired.receipt.tool_name, kind)
            if identity in attempted:
                continue
            attempted.add(identity)
            item = _save_experience(db, conv, execution, assistant, failed, repaired, kind, step)
            if item is not None:
                learned.append(item)
                if len(learned) == available:
                    return learned
    return learned
