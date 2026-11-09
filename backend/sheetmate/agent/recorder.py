"""Persist workflow evidence separately from model and task termination reasons."""
from __future__ import annotations

import time
from copy import deepcopy
from typing import Any


_STOP_PRIORITY = {"running": 0, "completed": 1, "waiting": 2, "incomplete": 3,
                  "cancelled": 4, "interrupted": 4, "failed": 5}


class WorkflowRecorder:
    def __init__(self, request_text: str):
        self.request_text = request_text
        self.started = time.perf_counter()
        self.steps: list[dict[str, Any]] = []
        self.protocol_errors: list[dict[str, Any]] = []
        self._open: dict[str, dict[str, Any]] = {}
        self.final_text = ""
        self.error = ""
        self.files: list[str] = []
        self.recipes: list[dict[str, Any]] = []
        self.model_calls: list[dict[str, Any]] = []
        self.stops: list[dict[str, Any]] = []
        self.active_call: dict[str, Any] | None = None
        self._active_calls: dict[str, dict[str, Any]] = {}
        self.last_step: dict[str, Any] | None = None
        self.result: dict[str, Any] = {}
        self.message_id: int | None = None
        self.request_message_id: int | None = None
        self.job_id: str | None = None

    def record_stop(self, event: dict[str, Any]) -> None:
        stop = {key: value for key, value in event.items() if key != "type"}
        self.stops.append(stop)
        previous = _STOP_PRIORITY.get(str(self.result.get("status")), -1)
        priority = _STOP_PRIORITY.get(str(stop.get("status")), 3)
        verified_recovery = (
            stop.get("scope") == "task" and stop.get("status") == "completed" and stop.get("verified") is True
            and self.result.get("scope") in {"step", "agent"}
            and self.result.get("status") in {"incomplete", "waiting"}
        )
        if verified_recovery or priority > previous or (priority == previous and (
            self.result.get("code") in {None, "execution_error"} or stop.get("source") is not None
            or stop.get("status") == "completed"
            or (stop.get("status") == "waiting" and self.result.get("scope") != "task")
            or (stop.get("scope") == "task" and self.result.get("scope") in {"step", "agent"})
        )):
            self.result = stop

    def observe(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if event.get("error_kind") in {"protocol", "schema"}:
            if kind == "tool_result":
                self.protocol_errors.append({"tool": event.get("tool"), "error_kind": event["error_kind"],
                                             "errors": event.get("error_details", []), "executed": False})
            return
        if kind == "tool_call":
            step = {"tool": event["tool"], "label": event.get("label", ""), "params": event.get("params", {}),
                    "started": time.perf_counter(), "tables": []}
            self._open[event["id"]] = step
            self.steps.append(step)
            self.last_step = {"tool": event["tool"], "label": step["label"], "status": "running"}
        elif kind == "table" and event.get("tool_call_id") in self._open:
            table = event.get("table", {})
            if table.get("name"):
                self._open[event["tool_call_id"]]["tables"].append(table["name"])
        elif kind == "tool_result" and event.get("id") in self._open:
            step = self._open.pop(event["id"])
            step["ok"] = event.get("ok", True)
            step["summary"] = str(event.get("summary", ""))[:300]
            if event.get("details"):
                step["details"] = event["details"]
            step["duration_ms"] = int((time.perf_counter() - step.pop("started")) * 1000)
            self.last_step = {"tool": step["tool"], "label": step["label"],
                              "status": "completed" if step["ok"] else "failed", "summary": step["summary"]}
        elif kind == "file" and event.get("file_id"):
            if event.get("explicit") or event.get("delivery_status") != "candidate":
                self.files.append(event["file_id"])
        elif kind == "delivery_manifest":
            candidates = set(event.get("candidate_file_ids", []))
            finals = event.get("final_file_ids", []) if event.get("status") == "confirmed" else []
            self.files = list(dict.fromkeys([*(file_id for file_id in self.files if file_id not in candidates), *finals]))
        elif kind == "recipe_saved":
            self.recipes.append({"recipe_id": event.get("recipe_id"), "version": event.get("version")})
        elif kind == "final_text":
            self.final_text = event.get("content", "")
        elif kind in {"model_call_start", "completion_check_start"}:
            self.active_call = {key: value for key, value in event.items() if key != "type"}
            if kind == "completion_check_start":
                self.active_call["purpose"] = "completion_check"
            self._active_calls[str(event.get("call_id") or "current")] = self.active_call
        elif kind in {"model_call_done", "completion_check"}:
            call_id = str(event.get("call_id") or "current")
            completed = {**self._active_calls.get(call_id, {}),
                         **{key: value for key, value in event.items() if key != "type"}}
            if kind == "completion_check":
                completed["purpose"] = "completion_check"
            if kind != "completion_check" or event.get("source") != "local":
                self.model_calls.append(completed)
            if event.get("call_id"):
                self._active_calls.pop(str(event["call_id"]), None)
            elif kind == "completion_check":
                # Legacy checks lacked an id; clear only their matching check, never another agent's model call.
                check_id = next((key for key in reversed(self._active_calls)
                                 if self._active_calls[key].get("purpose") == "completion_check"), None)
                if check_id is not None:
                    self._active_calls.pop(check_id)
            else:
                self._active_calls.pop("current", None)
            self.active_call = next(reversed(self._active_calls.values()), None)
        elif kind == "execution_stop":
            self.record_stop(event)
        elif kind == "clarify":
            self.record_stop({"status": "waiting", "code": "awaiting_user", "summary": event.get("question", "等待用户确认")})
        elif kind == "error":
            self.error = event.get("content", "")
            self.record_stop({"status": "failed", "code": event.get("code") or "execution_error", "summary": self.error,
                              **({"finish_reason": event["finish_reason"]} if event.get("finish_reason") else {})})

    def finish(self, content: str = "") -> None:
        if self._open:
            self.record_stop({"status": "incomplete", "code": "missing_tool_result", "summary": "部分操作尚未收到完成结果"})
        if not self.result:
            if self.error:
                self.record_stop({"status": "failed", "code": "execution_error", "summary": self.error})
            elif content or self.final_text:
                self.record_stop({"status": "completed", "code": "response_complete", "summary": "本轮处理已结束"})
            else:
                self.record_stop({"status": "incomplete", "code": "missing_completion", "summary": "处理已结束，但没有收到完整结果"})

    def execution(self, *, final: bool = True) -> dict[str, Any]:
        result = dict(self.result) if final else {"status": "running", "code": "executing", "summary": "正在处理"}
        return {**result, "last_step": deepcopy(self.last_step), "model_calls": deepcopy(self.model_calls),
                "stops": deepcopy(self.stops), "active_call": deepcopy(self.active_call),
                "active_calls": deepcopy(list(self._active_calls.values())),
                "pending_steps": [{"tool": step["tool"], "label": step["label"]} for step in self._open.values()],
                "duration_ms": int((time.perf_counter() - self.started) * 1000)}

    def tool_sequence(self) -> str:
        return " → ".join(s["tool"] for s in self.steps)

    def digest(self) -> str:
        parts = [s.get("summary") or s["label"] or s["tool"] for s in self.steps if s.get("ok") is True][:6]
        return "（本轮执行：" + "；".join(p for p in parts if p) + "）" if parts else ""

    def to_dict(self, *, final: bool = True) -> dict[str, Any]:
        steps = deepcopy(self.steps)
        for step in steps:
            started = step.pop("started", None)
            if started is not None:
                step["duration_ms"] = int((time.perf_counter() - started) * 1000)
                if final:
                    step["ok"] = False
                    step["summary"] = "该操作未收到完成结果"
        return {"request_text": self.request_text, "steps": steps, "protocol_errors": self.protocol_errors,
                "tool_sequence": self.tool_sequence(), "final_text": self.final_text[:1000], "error": self.error,
                "files": self.files, "recipes": self.recipes, "execution": self.execution(final=final),
                "duration_ms": int((time.perf_counter() - self.started) * 1000)}
