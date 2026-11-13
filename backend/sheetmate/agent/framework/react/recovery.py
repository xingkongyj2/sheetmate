"""AI 选择处理方法，执行器约束每种方法的失败次数和恢复预算。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sheetmate.core.config import limits
from sheetmate.agent.framework.shared.feedback import public_detail as _public_detail
from sheetmate.agent.tools.contracts import Tool, ToolResult

RECONSIDER_TOOL = "reconsider_approach"
METHOD_FAILURE_LIMIT = limits.METHOD_FAILURE_LIMIT
MAX_METHODS = limits.MAX_METHODS


def _public_params(args: dict[str, Any]) -> dict[str, str]:
    values = {key: _public_detail(args.get(key)) for key in ("method", "target", "before", "after", "evidence", "change", "validation")}
    return {"action": "use_existing_result" if args.get("action") == "use_existing_result" else "switch_method",
            **{key: value for key, value in values.items() if value}}


def _public_sections(public: dict[str, str], *, existing: bool = False) -> list[dict[str, Any]]:
    fields = [("target", "处理对象")]
    fields += [("evidence", "结果依据"), ("validation", "核对内容")] if existing else [
        ("method", "处理方法"), ("evidence", "问题依据"), ("before", "调整前"),
        ("after", "调整后"), ("change", "调整内容"), ("validation", "校验目标"),
    ]
    sections = [{"label": label, "items": [public[key]]} for key, label in fields if public.get(key)]
    if not public.get("target"):
        sections.append({"label": "记录缺失", "items": ["本次未单独记录处理对象，请结合问题依据和调整内容核对。"]})
    sections.append({"label": "下一步", "items": [
        "依据已核实的结果回复，未完成的附加操作仍按未完成展示。" if existing else
        "执行新方法并核对结果；选择方案尚不代表调整已生效。",
    ]})
    return sections


@dataclass
class _Method:
    name: str
    attempts: int = 0
    failures: int = 0


@dataclass
class _ObservedResult:
    tool: str
    ok: bool
    method: int
    # These aliases come from actual result receipts, never from the model's claims.
    aliases: list[str] = field(default_factory=list)


@dataclass
class RecoveryState:
    methods: list[_Method] = field(default_factory=lambda: [_Method("初始处理方法")])
    pending: set[str] = field(default_factory=set)
    results: dict[str, _ObservedResult] = field(default_factory=dict)
    invalid_results: set[str] = field(default_factory=set)

    @property
    def current(self) -> _Method:
        return self.methods[-1]

    @property
    def must_switch(self) -> bool:
        return bool(self.pending) and self.current.failures >= METHOD_FAILURE_LIMIT

    @property
    def exhausted(self) -> bool:
        return self.must_switch and len(self.methods) >= MAX_METHODS

    @property
    def may_finish(self) -> bool:
        # 新方法只声明、未尝试，不能当成已用多种方法排查。
        return (not self.pending or
                (self.current.failures > 0 and sum(method.failures > 0 for method in self.methods) >= 2))

    def observe(self, name: str, result: ToolResult, *, call_id: str, diagnostic: bool, reused: bool = False,
                evidence_id: str | None = None, result_events: list[dict[str, Any]] | None = None) -> None:
        if reused:
            return  # 缓存复用没有产生新证据，也不重置既有失败。
        aliases = {evidence_id} if evidence_id else set()
        for event in result.events if result_events is None else result_events:
            # Showing an existing file is not a new creation receipt for that file.
            produced_file = event.get("type") == "file" and not event.get("explicit")
            if event.get("type") in {"script", "recipe_run_done", "recipe_completed"} or produced_file:
                if isinstance(event.get("run_id"), str) and event["run_id"]:
                    aliases.add(event["run_id"])
            if produced_file and isinstance(event.get("file_id"), str) and event["file_id"]:
                aliases.add(event["file_id"])
        self.results[call_id] = _ObservedResult(name, result.ok, len(self.methods) - 1, sorted(aliases))
        self.current.attempts += 1
        if not result.ok:
            self.current.failures += 1
            self.pending.add(name)
        else:
            self.pending.discard(name)
            if not diagnostic:
                self.pending.clear()

    def _resolve_result(self, reference: Any) -> tuple[str | None, _ObservedResult | None]:
        if not isinstance(reference, str):
            return None, None
        reference = reference.strip().strip("`")
        if reference in self.results:
            return reference, self.results[reference]
        matches = [(call_id, result) for call_id, result in self.results.items() if reference in result.aliases]
        # Do not guess between different executions, including failed or invalid ones.
        return matches[0] if len(matches) == 1 else (None, None)

    def _reference_error(self) -> ToolResult:
        candidates = [call_id for call_id, result in self.results.items()
                      if result.ok and call_id not in self.invalid_results][-5:]
        correction = (f"\n本任务可引用的成功调用 result_call_id：{'、'.join(candidates)}。" if candidates else
                      "\n当前任务没有可引用的成功执行结果，请先执行所需操作。")
        return ToolResult(
            "只能引用真实成功、且未判定业务无效的执行结果；可填写实际调用 ID、证据 ID，或唯一对应的运行/产出文件 ID。"
            "失败、未执行或无法唯一定位的结果不能作为已完成依据。" + correction,
            ok=False, public_summary="需要核对结果引用",
            public_text="正在核对已有执行结果，尚未确认本次处理完成。",
        )

    def choose(self, args: dict[str, Any]) -> ToolResult:
        action = args.get("action", "switch_method")
        if action not in {"switch_method", "use_existing_result"}:
            return ToolResult("action 只能是 switch_method 或 use_existing_result。", ok=False)
        evidence, validation = args.get("evidence"), args.get("validation")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 2000
               for value in (evidence, validation)):
            return ToolResult("请说明实际证据 evidence 和校验依据 validation。", ok=False)
        source_id, source = self._resolve_result(args.get("result_call_id"))
        if action == "use_existing_result":
            if source is None or not source.ok or source_id in self.invalid_results:
                return self._reference_error()
            self.pending.clear()
            public = _public_params(args)
            return ToolResult(
                f"已根据工具调用 {source_id} 的实际结果重新核对目标。\n依据：{evidence.strip()}\n"
                f"校验：{validation.strip()}\n按已经确认的结果回答，不能把失败的附加操作说成成功。",
                summary="已核对已有结果", public_summary="已核对已有结果",
                public_text="\n".join((
                    *([f"处理对象：{public['target']}"] if public.get("target") else []),
                    f"结果依据：{public.get('evidence') or '已引用此前成功执行的结果。'}",
                    f"核对内容：{public.get('validation') or '尚未收到可展示的具体核对说明。'}",
                    "下一步：依据已核实的结果回复，未完成的附加操作仍按未完成展示。",
                )),
                public_details=_public_sections(public, existing=True),
            )
        values = {key: args.get(key) for key in ("method", "evidence", "change", "validation")}
        if any(not isinstance(value, str) or not value.strip() or len(value) > 2000 for value in values.values()):
            return ToolResult("请填写非空的 method、evidence、change、validation，分别说明新方法、实际失败依据、实质变化和验证目标。",
                              ok=False, public_summary="需要完善处理方案", public_text="正在核对下一种处理方法。")
        if not self.current.attempts:
            return ToolResult("刚选择的方法尚未实际尝试，不能仅改名称继续切换。先用工具验证该方法。", ok=False)
        method = values["method"].strip()
        normalized = "".join(method.split()).casefold()
        if any("".join(item.name.split()).casefold() == normalized for item in self.methods):
            return ToolResult("该方法已经尝试过。请根据实际反馈选择有实质变化的新方法，不能只改描述重置计数。", ok=False)
        if not self.pending:
            # 代码正常退出也可能得出错误业务结果；由 AI 引用证据判断，不能只看 ok。
            if source is None or not source.ok or source.method != len(self.methods) - 1:
                return ToolResult("结果异常时请用 result_call_id 引用本方法实际执行过的工具结果，并说明业务校验为何未通过。", ok=False)
        if source is not None and source.ok and source.method == len(self.methods) - 1:
            if source_id not in self.invalid_results:
                self.current.failures += 1
                self.invalid_results.add(source_id)
            self.pending.add(source.tool)
        if len(self.methods) >= MAX_METHODS:
            return ToolResult("本次已达到方法数量上限，请依据实际结果如实收尾，不再启动新方法。", ok=False)
        self.methods.append(_Method(method))
        public = _public_params(args)
        public_method = _public_detail(method, 80)
        return ToolResult(
            f"已选择方法 {len(self.methods)}/{MAX_METHODS}：{method}。\n"
            f"失败依据：{values['evidence'].strip()}\n实质变化：{values['change'].strip()}\n"
            f"验证目标：{values['validation'].strip()}\n"
            f"现在执行并校验；该方法最多允许 {METHOD_FAILURE_LIMIT} 次失败，声明方案不代表已经完成。",
            summary="已选择新方法", public_summary=f"已选择新方法：{public_method}" if public_method else "已选择新的处理方法",
            public_text="\n".join((
                *([f"处理对象：{public['target']}"] if public.get("target") else []),
                f"问题依据：{public.get('evidence') or '尚未收到可展示的具体问题说明。'}",
                *([f"调整前：{public['before']}"] if public.get("before") else []),
                *([f"调整后：{public['after']}"] if public.get("after") else []),
                f"调整内容：{public.get('change') or '尚未收到可展示的具体调整说明。'}",
                f"校验目标：{public.get('validation') or '尚未收到可展示的具体校验说明。'}",
                "下一步：执行新方法并核对结果；选择方案尚不代表调整已生效。",
            )),
            public_details=_public_sections(public),
        )

    def tool(self) -> Tool:
        return Tool(
            RECONSIDER_TOOL,
            "遇到技术失败后，根据实际反馈自主选择下一种处理方法。达到当前方法失败阈值时必须先调用，再继续计算。"
            "同一个 Python 工具更换结构假设、字段定位或算法也算换方法，无需机械更换工具。"
            "脚本正常退出但结果不对时，也可引用 result_call_id 登记业务失败并切换。"
            "已有正确结果足以覆盖目标时，action=use_existing_result，引用实际成功调用并说明证据和校验，随后直接回答。"
            "面向用户记录具体处理对象 target、原先做法 before、新做法 after、实际依据 evidence、实质变化 change 和可验证目标 validation；"
            "对象写真实文件、工作表、字段或单元格范围，前后写实际定位、来源或计算规则，不只写工具名或已优化。"
            "只描述可观察的操作、依据和待验证结果，不输出内部推理。只改变量、名称、描述或重复相同代码不算换方法。不会向用户要求批准。",
            {"type": "object", "properties": {
                "action": {"type": "string", "enum": ["switch_method", "use_existing_result"], "description": "默认 switch_method 选择新方法；use_existing_result 核实已有正确结果已满足目标"},
                "result_call_id": {"type": "string", "description": "引用真实工具调用 ID 或 evidence_id；也可填唯一对应的运行 run_id 或产出 file_id。业务结果不对但工具成功、或核实已有结果时必填"},
                "method": {"type": "string", "description": "新方法的具体名称，说明定位或计算思路"},
                "target": {"type": "string", "description": "实际要调整的文件名、工作表名、字段或单元格范围；已有结果核对时写被核对的结果对象"},
                "before": {"type": "string", "description": "之前实际使用的做法：依据哪个来源、如何定位、匹配或计算；不要补造未执行的做法"},
                "after": {"type": "string", "description": "现在改成的具体做法，与 before 对应写清来源、字段或计算规则的变化；尚未执行不要声称成功"},
                "evidence": {"type": "string", "description": "已执行工具提供的失败依据或结果异常"},
                "change": {"type": "string", "description": "相对之前失败方案改变的结构假设、数据来源、定位或计算规则"},
                "validation": {"type": "string", "description": "执行后如何检查关键字段、样本和汇总是否满足用户目标"},
            }, "required": ["evidence", "validation"], "additionalProperties": False},
            run=lambda args, ctx: self.choose(args), label=lambda args: "调整处理方法",
            public_params=_public_params,
        )
