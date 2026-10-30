"""工作流记录（对应 TabClaw 的 WorkflowRecorder）：记下每次对话调用了哪些工具、参数、结果和耗时。

TabClaw 存成 data/workflows/*.json；SheetMate 存进 runs 表（kind=analysis / modify），客户页的「运行记录」里能看到。
"""

from __future__ import annotations

import time
from typing import Any


class WorkflowRecorder:
    def __init__(self, request_text: str):
        self.request_text = request_text
        self.started = time.perf_counter()
        self.steps: list[dict[str, Any]] = []
        self._open: dict[str, dict[str, Any]] = {}
        self.final_text = ""
        self.error = ""
        self.files: list[str] = []
        self.recipes: list[dict[str, Any]] = []

    def observe(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "tool_call":
            step = {"tool": event["tool"], "label": event.get("label", ""), "params": event.get("params", {}),
                    "started": time.perf_counter(), "tables": []}
            self._open[event["id"]] = step
            self.steps.append(step)
        elif kind == "table" and event.get("tool_call_id") in self._open:
            table = event.get("table", {})
            if table.get("name"):
                self._open[event["tool_call_id"]]["tables"].append(table["name"])
        elif kind == "tool_result" and event.get("id") in self._open:
            step = self._open.pop(event["id"])
            step["ok"] = event.get("ok", True)
            step["summary"] = str(event.get("summary", ""))[:300]
            step["duration_ms"] = int((time.perf_counter() - step.pop("started")) * 1000)
        elif kind == "file" and event.get("file_id"):
            self.files.append(event["file_id"])
        elif kind == "recipe_saved":
            self.recipes.append({"recipe_id": event.get("recipe_id"), "version": event.get("version")})
        elif kind == "final_text":
            self.final_text = event.get("content", "")
        elif kind == "error":
            self.error = event.get("content", "")

    def tool_sequence(self) -> str:
        return " → ".join(s["tool"] for s in self.steps)

    def digest(self) -> str:
        """给下一轮对话的历史用：这一轮做了什么（一行）。"""
        if not self.steps:
            return ""
        parts = [s.get("summary") or s["label"] or s["tool"] for s in self.steps if s.get("ok", True)][:6]
        return "（本轮执行：" + "；".join(p for p in parts if p) + "）"

    def to_dict(self) -> dict[str, Any]:
        for step in self.steps:
            step.pop("started", None)
        return {
            "request_text": self.request_text,
            "steps": self.steps,
            "tool_sequence": self.tool_sequence(),
            "final_text": self.final_text[:1000],
            "error": self.error,
            "files": self.files,
            "recipes": self.recipes,
            "duration_ms": int((time.perf_counter() - self.started) * 1000),
        }
