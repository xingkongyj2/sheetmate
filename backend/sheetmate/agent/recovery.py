"""AI 选择处理方法，执行器约束每种方法的失败次数和恢复预算。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sheetmate.agent.tools import Tool, ToolResult

RECONSIDER_TOOL = "reconsider_approach"
METHOD_FAILURE_LIMIT = 2
MAX_METHODS = 3


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

    def observe(self, name: str, result: ToolResult, *, call_id: str, diagnostic: bool, reused: bool = False) -> None:
        if reused:
            return  # 缓存复用没有产生新证据，也不重置既有失败。
        self.results[call_id] = _ObservedResult(name, result.ok, len(self.methods) - 1)
        self.current.attempts += 1
        if not result.ok:
            self.current.failures += 1
            self.pending.add(name)
        else:
            self.pending.discard(name)
            if not diagnostic:
                self.pending.clear()

    def choose(self, args: dict[str, Any]) -> ToolResult:
        action = args.get("action", "switch_method")
        if action not in {"switch_method", "use_existing_result"}:
            return ToolResult("action 只能是 switch_method 或 use_existing_result。", ok=False)
        evidence, validation = args.get("evidence"), args.get("validation")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 2000
               for value in (evidence, validation)):
            return ToolResult("请说明实际证据 evidence 和校验依据 validation。", ok=False)
        source_id = args.get("result_call_id")
        source = self.results.get(source_id) if isinstance(source_id, str) else None
        if action == "use_existing_result":
            if source is None or not source.ok or source_id in self.invalid_results:
                return ToolResult("只能引用真实成功、且未判定业务无效的工具调用 result_call_id；失败或未执行的结果不能作为已完成依据。", ok=False)
            self.pending.clear()
            return ToolResult(
                f"已根据工具调用 {source_id} 的实际结果重新核对目标。\n依据：{evidence.strip()}\n"
                f"校验：{validation.strip()}\n按已经确认的结果回答，不能把失败的附加操作说成成功。",
                summary="已核对已有结果", public_summary="已核对已有结果", public_text="将依据已经核实的结果回复。",
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
        return ToolResult(
            f"已选择方法 {len(self.methods)}/{MAX_METHODS}：{method}。\n"
            f"失败依据：{values['evidence'].strip()}\n实质变化：{values['change'].strip()}\n"
            f"验证目标：{values['validation'].strip()}\n"
            f"现在执行并校验；该方法最多允许 {METHOD_FAILURE_LIMIT} 次失败，声明方案不代表已经完成。",
            summary="已调整处理方法", public_summary="已调整处理方法", public_text="将按新的定位或计算方法继续核对。",
        )

    def tool(self) -> Tool:
        return Tool(
            RECONSIDER_TOOL,
            "遇到技术失败后，根据实际反馈自主选择下一种处理方法。达到当前方法失败阈值时必须先调用，再继续计算。"
            "同一个 Python 工具更换结构假设、字段定位或算法也算换方法，无需机械更换工具。"
            "脚本正常退出但结果不对时，也可引用 result_call_id 登记业务失败并切换。"
            "已有正确结果足以覆盖目标时，action=use_existing_result，引用实际成功调用并说明证据和校验，随后直接回答。"
            "说明实质变化和可验证目标；只改变量、名称、描述或重复相同代码不算换方法。不会向用户要求批准。",
            {"type": "object", "properties": {
                "action": {"type": "string", "enum": ["switch_method", "use_existing_result"], "description": "默认 switch_method 选择新方法；use_existing_result 核实已有正确结果已满足目标"},
                "result_call_id": {"type": "string", "description": "引用真实工具调用的 ID；业务结果不对但工具成功、或核实已有结果时必填"},
                "method": {"type": "string", "description": "新方法的具体名称，说明定位或计算思路"},
                "evidence": {"type": "string", "description": "已执行工具提供的失败依据或结果异常"},
                "change": {"type": "string", "description": "相对之前失败方案改变的结构假设、数据来源、定位或计算规则"},
                "validation": {"type": "string", "description": "执行后如何检查关键字段、样本和汇总是否满足用户目标"},
            }, "required": ["evidence", "validation"], "additionalProperties": False},
            run=lambda args, ctx: self.choose(args), label=lambda args: "调整处理方法",
            public_params=lambda args: {},
        )
