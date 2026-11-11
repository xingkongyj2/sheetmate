"""规划与澄清。

- data_brief：规划前的数据勘察——每张表真实的列类型、空值率、样本、候选主键和重复键（只读、不用大模型）
- make_plan / review_plan：思考模式的计划。先按数据画像起草（理解、默认口径、风险、每一项的说明和验收标准），
  再让大模型审查一轮，补遗漏、纠顺序、核对列名；全程不需要用户确认
- 思考模式统一展示紧凑任务清单；规划和审查模型通过 show_plan 判断最终回答是否逐项回顾
- check_clarification：判断需求是否有真正的歧义，有就给选项（TabClaw 保留，/clarify 接口使用）

规划和审查都可以降级：大模型调用失败或返回的内容无法使用时，退回兜底计划 / 保留原计划，不会让整次请求失败。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from sheetmate.agent.memory.short_term.context import stage_history
from sheetmate.agent.framework.shared.schemas import PLAN_REVIEW_SCHEMA, PLAN_SCHEMA
from sheetmate.agent.framework.shared.prompt_builder import build_system_prompt
from sheetmate.agent.llm.client import LLMError, aux_json
from sheetmate.agent.llm.prompts import CLARIFY_PROMPT, THINK_PLAN_SYSTEM, THINK_REVIEW_SYSTEM
from sheetmate.agent.framework.shared.reporting import (
    public_sentence,
    short_step_text,
    short_step_title,
    strip_step_label,
)
from sheetmate.agent.tools.contracts import AgentContext
from sheetmate.agent.memory.short_term.task_state import task_projection
from sheetmate.engine.tables.analysis import table_info
from sheetmate.engine.tables.sql import frame_to_text

MAX_PLAN_STEPS = 8
MAX_STEP_DESCRIPTION = 4000
MAX_STEP_CHECK = 3000
_DEFAULT_STEP_CHECK = "本项结果符合完整需求并有实际依据；涉及表格或文件时，核对实际产出及关键行数、金额或数量。"
PLAN_TIMEOUT = 120.0  # 起草计划最多等多久；超时按兜底计划继续
REVIEW_TIMEOUT = 90.0  # 审查计划最多等多久；超时保留原计划
_ROLE_TAGS = {"expected": "，人工做好的期望结果", "derived": "，分析结果", "result": "，流程产出"}
_BRIEF_LIMIT = 14000  # 数据画像总长度上限（字符）


# ---------------------------------------------------------------------------
# 数据勘察
# ---------------------------------------------------------------------------


def _tables_text(ctx: AgentContext, max_cols: int = 10) -> str:
    lines = []
    for t in ctx.visible_tables():
        cols = ", ".join(t.column_names[:max_cols]) + (" …" if len(t.columns) > max_cols else "")
        lines.append(f"- `{t.name}`（{t.label}）{t.rows} 行 | 列：{cols}")
    return "\n".join(lines) or "（还没有表）"


def clip(value: Any, limit: int) -> str:
    """截断到 limit 个字符，超出的部分用省略号。"""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _column_line(col: dict[str, Any]) -> str:
    detail = [col["dtype"], f"空值 {col['null_rate']:.0%}", f"{col['unique']} 个不同值"]
    if "min" in col and col.get("min") is not None:
        detail.append(f"范围 {col['min']}~{col['max']}")
    samples = "、".join(clip(v, 24) for v in col.get("samples", [])[:3])
    return f"- {col['name']}（{'，'.join(detail)}）" + (f" 样本：{samples}" if samples else "")


def data_brief(ctx: AgentContext, *, max_tables: int = 5, max_columns: int = 20) -> str:
    """规划用的数据画像。只读取工作区，不改动任何东西；个别表读不出来时退回到只列列名。"""
    tables = ctx.visible_tables()
    if not tables:
        return "（工作区里还没有表）"
    blocks: list[str] = []
    for table in tables[:max_tables]:
        header = (f"表 `{table.name}`（{table.label}{_ROLE_TAGS.get(table.role, '')}）："
                  f"{table.rows} 行 × {len(table.columns)} 列")
        try:
            data = table_info(ctx.ws, table.name, sample_rows=3)
            columns = data["columns"]
            lines = [header, *(_column_line(col) for col in columns[:max_columns])]
            if len(columns) > max_columns:
                lines.append(f"- 其余 {len(columns) - max_columns} 列：" + "、".join(c["name"] for c in columns[max_columns:max_columns + 30]))
            keys = data.get("keys") or {}
            if keys.get("candidates"):
                lines.append(f"候选主键：{'、'.join(keys['candidates'][:4])}")
            for dup in (keys.get("duplicated") or [])[:3]:
                lines.append(f"注意：「{dup['column']}」有 {dup['duplicate_values']} 个值重复（最多 {dup['max_repeat']} 次），可能一单多行")
            sample = data["sample"]
            kept = sample["columns"][:max_columns]
            if sample["rows"] and kept:
                frame = pd.DataFrame([row[:len(kept)] for row in sample["rows"]], columns=kept)
                lines.append("前几行：\n" + frame_to_text(frame, limit=len(frame), max_cell=20))
            blocks.append("\n".join(lines))
        except Exception:
            names = "、".join(table.column_names[:max_columns])
            blocks.append(f"{header}\n列：{names}")
    if len(tables) > max_tables:
        blocks.append("另有表（未展开）：" + "、".join(f"`{t.name}`" for t in tables[max_tables:max_tables + 10]))
    brief = "\n\n".join(blocks)
    return brief if len(brief) <= _BRIEF_LIMIT else brief[: _BRIEF_LIMIT - 1] + "…"


# ---------------------------------------------------------------------------
# 计划
# ---------------------------------------------------------------------------


@dataclass
class Plan:
    title: str = "执行需求"
    understanding: str = ""
    assumptions: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)  # {"id", "description", "check"}
    show_plan: bool = False  # 规划/审查模型判断最终回答是否逐项回顾
    reviewed: bool = False  # 审查确实执行并给出了结论
    changes: list[str] = field(default_factory=list)  # 审查时发现并修订的问题
    fallback: bool = False  # 规划失败后的兜底计划
    reason: str = ""  # 规划失败（兜底计划）或审查没完成的原因，给用户看
    acceptance: list[str] = field(default_factory=list)  # 最终交付的可观察验收条件

    def to_json(self) -> dict[str, Any]:
        return {"title": self.title, "understanding": self.understanding, "assumptions": self.assumptions,
                "risks": self.risks, "steps": self.steps, "show_plan": self.show_plan,
                "acceptance": self.acceptance}

    def overview(self) -> str:
        """给执行助手看的整份计划，让它知道每一步在全局里的位置。"""
        lines = []
        if self.understanding:
            lines.append(f"理解：{self.understanding}")
        if self.assumptions:
            lines.append("默认口径：\n" + "\n".join(f"- {item}" for item in self.assumptions))
        if self.risks:
            lines.append("需要留意：\n" + "\n".join(f"- {item}" for item in self.risks))
        if self.acceptance:
            lines.append("最终交付验收：\n" + "\n".join(f"- {item}" for item in self.acceptance))
        steps = [f"{step['id']}. {step['description']}" + (f"（验收：{step['check']}）" if step.get("check") else "")
                 + (f"（依赖步骤：{step['depends_on']}）" if step.get("depends_on") else "")
                 for step in self.steps]
        lines.append("步骤：\n" + "\n".join(steps))
        return "\n".join(lines)

    # ---- 展示：面板每项只保留简短说明，完整业务指令仍在 steps 里
    @property
    def narrated(self) -> bool:
        """两项以上、且不是兜底计划：每一项开始时向用户说一句现在要做什么。只有一项时说了也只是重复需求。"""
        return len(self.steps) >= 2 and not self.fallback

    @property
    def checklist(self) -> bool:
        """按模型判断是否在最终回答逐项回顾；兜底计划不回顾。"""
        return self.show_plan and not self.fallback

    @property
    def worth_reviewing(self) -> bool:
        """兜底计划没有可审查的内容；单项且没有风险的计划不值得再多一次模型调用。"""
        return not (self.fallback or (len(self.steps) < 2 and not self.risks))

    def lead_in(self, num: int) -> str:
        """第 num 项开始时对用户说的一句话。优先用规划时写好的口语化说明，没有就在描述前加「先 / 接着 / 最后」。"""
        step = self.steps[num - 1]
        text = strip_step_label(step.get("narration") or "")
        if not text:
            text = strip_step_label(step["description"])
            if text and not _CONNECTOR.match(text) and len(self.steps) >= 2:
                text = ("先" if num == 1 else "最后" if num == len(self.steps) else "接着") + text
        text = text.rstrip("。；;，, ")
        text = short_step_text(text, limit=29)
        return f"{text}。" if text else ""

    def display_description(self, num: int) -> str:
        """面板只用 20 字内的动作标题，执行助手仍读取完整业务指令。"""
        step = self.steps[num - 1]
        return short_step_title(step.get("title") or step.get("narration") or step["description"])

    def doing(self, num: int) -> str:
        """第 num 项进行中时「正在做什么」的短句（界面上随进度闪动的那一行）。"""
        text = _CONNECTOR.sub("", strip_step_label(self.steps[num - 1]["description"])).strip().rstrip("。；;，, ")
        text = f"正在{text}" if text and not text.startswith("正在") else text or "正在处理"
        return short_step_text(text, fallback="正在处理")

    def review_line(self) -> str:
        """计划审查的结果，如实写：审查没有做成就说没有做成，不能让用户以为审过了。"""
        if self.reviewed:
            changes = [item for item in (public_sentence(change, 80) for change in self.changes[:2]) if item]
            return ("计划已对照数据审查，补充了：" + "；".join(changes) + "。") if changes else "计划已对照数据审查，没有发现问题。"
        why = public_sentence(self.reason, 80)
        return f"计划审查没有完成（{why}），按原计划进行。" if why and not self.fallback else ""

    def intro(self) -> str:
        """开场白（给用户看）：怎么理解的需求、按什么口径做、数据里要留意什么、计划审查的结果。
        具体做哪几项由进度清单展示，这里不重复列一遍；没有内容时返回空字符串。"""
        if self.fallback:
            why = public_sentence(self.reason, 80)
            return f"没能制定详细计划{f'（{why}）' if why else ''}，先按通用步骤处理。"
        blocks = []
        if self.understanding and len(self.steps) >= 2:
            text = f"**我的理解**：{self.understanding}"
            if self.checklist:
                text += f"\n\n我把它分成 {len(self.steps)} 项来做，做完一项勾一项。"
            blocks.append(text)
        if self.assumptions:
            blocks.append("**默认口径**（需求里没说清，先按这样处理）：\n" + "\n".join(f"- {item}" for item in self.assumptions))
        if self.risks:
            blocks.append("**数据里要留意**：\n" + "\n".join(f"- {item}" for item in self.risks[:4]))
        if blocks or self.reviewed:
            review = self.review_line()
            if review:
                blocks.append(review)
        elif self.review_line():
            blocks.append(self.review_line())
        return "\n\n".join(blocks)


_CONNECTOR = re.compile(r"^(先|首先|然后|接着|接下来|再|之后|随后|最后|最终|同时)")


def plan_from_steps(steps: list[dict[str, Any]]) -> Plan:
    """调用方自带步骤时仍执行和核对；跳过模型规划，最终回答默认不逐项回顾。"""
    cleaned = _clean_steps(steps)
    if not cleaned:
        raise ValueError("提供的计划无法执行：请保留完整步骤、有效依赖及非空业务描述，最多 8 项。")
    return Plan(steps=cleaned)


def clean_text(value: Any, limit: int) -> str:
    """模型返回的字段可能不是字符串：不是就当作空。"""
    return clip(value.strip(), limit) if isinstance(value, str) else ""


def clean_list(value: Any, limit: int, item_limit: int = 200) -> list[str]:
    """字符串列表：丢掉非字符串和空项，限制条数和每条长度。"""
    if not isinstance(value, list):
        return []
    items = [clean_text(item, item_limit) for item in value if isinstance(item, str)]
    return [item for item in items if item][:limit]


def _clean_steps(value: Any, *, require_checks: bool = False) -> list[dict[str, Any]]:
    """完整验证步骤，再稳定拓扑排序；拒绝整份非法计划，绝不静默删步或截断业务条件。"""
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_PLAN_STEPS:
        return []
    steps: list[dict[str, Any]] = []
    source_ids: set[int] = set()
    for position, item in enumerate(value, start=1):
        if isinstance(item, str):
            item = {"description": item}
        if not isinstance(item, dict):
            return []
        # 模型偶尔会在描述里写「第 N 步：」，用户看到的清单和开场白里不要编号
        raw_description = item.get("description")
        if not isinstance(raw_description, str) or len(raw_description) > MAX_STEP_DESCRIPTION:
            return []
        description = strip_step_label(raw_description.strip())
        raw_check = item.get("check", "")
        if not isinstance(raw_check, str) or len(raw_check) > MAX_STEP_CHECK:
            return []
        check = raw_check.strip()
        if not description or (require_checks and not check):
            return []
        source_id = item.get("id", position)
        if type(source_id) is not int or source_id < 1 or source_id in source_ids:
            return []
        dependencies = item.get("depends_on", [steps[-1]["id"]] if steps else [])
        if not isinstance(dependencies, list) or any(type(dep) is not int or dep < 1 for dep in dependencies):
            return []
        source_ids.add(source_id)
        step = {"id": source_id, "description": description, "check": check or _DEFAULT_STEP_CHECK,
                "depends_on": list(dict.fromkeys(dependencies))}
        title = strip_step_label(clean_text(item.get("title"), 160))
        if title:
            step["title"] = short_step_title(title)
        narration = strip_step_label(clean_text(item.get("narration"), 160))
        if narration:
            step["narration"] = narration
        steps.append(step)
    if any(dep not in source_ids or dep == step["id"] for step in steps for dep in step["depends_on"]):
        return []
    ordered = []
    mapping: dict[int, int] = {}
    pending = list(steps)
    while pending:
        # 只移动尚未就绪的项；独立步骤之间保持原顺序。
        ready = next((step for step in pending if all(dep in mapping for dep in step["depends_on"])), None)
        if ready is None:
            return []  # 依赖成环，无可执行顺序
        pending.remove(ready)
        mapping[ready["id"]] = len(ordered) + 1
        ordered.append(ready)
    return [{**step, "id": mapping[step["id"]], "depends_on": [mapping[dep] for dep in step["depends_on"]]}
            for step in ordered]


def _parse_plan(result: Any, *, require_acceptance: bool = False) -> Plan | None:
    if not isinstance(result, dict):
        return None
    steps = _clean_steps(result.get("steps"), require_checks=True)
    if not steps:
        return None
    acceptance = result.get("acceptance", [])
    if (not isinstance(acceptance, list) or len(acceptance) > MAX_PLAN_STEPS
            or any(not isinstance(item, str) or not item.strip() or len(item) > 1000 for item in acceptance)
            or ((require_acceptance or "acceptance" in result) and not acceptance)):
        return None
    return Plan(
        title=clean_text(result.get("title"), 40) or "执行计划",
        understanding=clean_text(result.get("understanding"), 300),
        assumptions=clean_list(result.get("assumptions"), 6),
        risks=clean_list(result.get("risks"), 6),
        steps=steps,
        show_plan=result.get("show_plan") is True,
        acceptance=[item.strip() for item in acceptance],
    )


def fallback_plan(ctx: AgentContext, message: str, reason: str = "") -> Plan:
    has_tables = bool(ctx.visible_tables())
    return Plan(
        title="执行需求",
        steps=[
            {"id": 1, "description": "查看相关表的结构和数据" if has_tables and ctx.mode != "create" else "理解需求并确定表格结构",
             "check": "需求字段、表头和布局已明确，未提供的真实数据不虚构" if ctx.mode == "create" else "确认用到的表和列真实存在"},
            {"id": 2, "description": "按原始需求完成处理及交付，保留全部业务条件", "check": "结果满足原始需求，能和来源数据对上（行数、合计）"},
            {"id": 3, "description": "整理并说明结果", "check": "回答中的数字与工具结果一致"},
        ],
        fallback=True,
        reason=reason,
        acceptance=["原始需求中的全部交付完成，结果与实际来源核对一致"],
    )


def _history_context(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = stage_history(history, "planning")
    return [{"role": item["role"], "message_id": item.get("message_id", item.get("id")),
             "content": str(item.get("content", ""))} for item in selected]


def _image_kwargs(ctx: AgentContext, history: list[dict[str, Any]]) -> dict[str, Any]:
    selected = stage_history(history, "planning")
    parts = [part for item in selected for part in item.get("image_parts", [])] + ctx.image_parts
    return {"image_parts": parts} if parts else {}


async def make_plan(llm: Any, ctx: AgentContext, message: str, history: list[dict[str, Any]], brief: str) -> Plan:
    """起草计划。规划依据是真实的数据画像和执行助手完整的工作环境，不是只有列名。
    限时；失败或超时就退回兜底计划（并记下原因）。"""
    system = THINK_PLAN_SYSTEM + "\n\n# 执行助手的工作环境（计划必须能在其中落地）\n" + build_system_prompt(ctx, message, thinking=True)
    payload = {"history": _history_context(history), "data_profile": brief, "requirement": message}
    outcome = await aux_json(llm, json.dumps(payload, ensure_ascii=False), system=system, timeout=PLAN_TIMEOUT,
                             schema=PLAN_SCHEMA, schema_name="execution_plan",
                             label="思考模式起草计划", **_image_kwargs(ctx, history))
    if not outcome.ok:
        if ctx.image_parts and (outcome.timed_out or isinstance(outcome.error, LLMError)):
            # 模型没能读懂图片时，不能悄悄按图片内容未知的情况给出计划
            raise outcome.error if isinstance(outcome.error, LLMError) else LLMError("规划时模型响应超时，请稍后再试。")
        return fallback_plan(ctx, message, outcome.reason)
    return _parse_plan(outcome.value, require_acceptance=True) or fallback_plan(ctx, message, "模型返回的计划无法使用")


async def review_plan(llm: Any, ctx: AgentContext, message: str, plan: Plan, brief: str,
                      history: list[dict[str, Any]] | None = None) -> Plan:
    """再起一次大模型交互，对照需求和数据审查计划；有问题就采用修订版。
    限时；审查失败、超时或结果无法使用时保留原计划（并记下原因）。"""
    if not plan.worth_reviewing:
        return plan
    plan.reviewed = False
    plan.changes = []
    payload = {"requirement": message, "history": _history_context(history or []),
               "task_state": task_projection(ctx.task_state),
               "data_profile": brief, "plan": plan.to_json()}
    system = THINK_REVIEW_SYSTEM + "\n\n# 执行助手的工作环境（与起草计划相同）\n" + build_system_prompt(ctx, message, thinking=True)
    outcome = await aux_json(llm, json.dumps(payload, ensure_ascii=False), system=system, timeout=REVIEW_TIMEOUT,
                             schema=PLAN_REVIEW_SCHEMA, schema_name="plan_review",
                             label="思考模式审查计划", **_image_kwargs(ctx, history or []))
    if not outcome.ok:
        plan.reason = outcome.reason
        return plan
    result = outcome.value
    verdict = result.get("verdict") if isinstance(result, dict) else None
    if verdict not in {"ok", "revise"}:
        plan.reason = "审查结果无法解析"
        return plan
    issues = clean_list(result.get("issues"), 8, 240)
    if verdict == "ok":
        if isinstance(result.get("show_plan"), bool):
            plan.show_plan = result["show_plan"]
        plan.reviewed = True
        plan.reason = ""
        return plan
    revised = _parse_plan(result)
    if revised is None:
        plan.reason = "审查给出的修订步骤无法使用"
        return plan  # 声称要修订却没给出可用的步骤，保留原计划
    # 审查没写的字段沿用原计划
    revised.title = clean_text(result.get("title"), 40) or plan.title
    revised.understanding = revised.understanding or plan.understanding
    if not isinstance(result.get("assumptions"), list):
        revised.assumptions = plan.assumptions
    if not isinstance(result.get("risks"), list):
        revised.risks = plan.risks
    if not isinstance(result.get("show_plan"), bool):
        revised.show_plan = plan.show_plan
    if "acceptance" not in result:
        revised.acceptance = list(plan.acceptance)
    revised.reviewed = True
    revised.changes = issues
    return revised


async def check_clarification(llm: Any, ctx: AgentContext, message: str) -> dict[str, Any]:
    if not ctx.visible_tables():
        return {"needs_clarification": False}
    try:
        result = await llm.complete_json(CLARIFY_PROMPT.format(message=message, tables=_tables_text(ctx, 6)))
        if not isinstance(result.get("needs_clarification"), bool):
            return {"needs_clarification": False}
        options = [str(o) for o in result.get("options") or [] if str(o).strip()]
        if result["needs_clarification"] and len(options) < 2:
            return {"needs_clarification": False}
        return {"needs_clarification": result["needs_clarification"], "question": result.get("question", ""), "options": options[:4]}
    except Exception:
        return {"needs_clarification": False}
