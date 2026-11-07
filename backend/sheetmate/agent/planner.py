"""规划与澄清。

- data_brief：规划前的数据勘察——每张表真实的列类型、空值率、样本、候选主键和重复键（只读、不用大模型）
- make_plan / review_plan：思考模式的计划。先按数据画像起草（理解、默认口径、风险、每一项的说明和验收标准），
  再让大模型审查一轮，补遗漏、纠顺序、核对列名；全程不需要用户确认
- 规划模型通过 show_plan 判断是否向用户展示任务清单，审查模型可以调整该判断；
  展示清单时在最后回顾每一项，不再按步骤数量决定是否展示
- check_clarification：判断需求是否有真正的歧义，有就给选项（TabClaw 保留，/clarify 接口使用）

规划和审查都可以降级：大模型调用失败或返回的内容无法使用时，退回兜底计划 / 保留原计划，不会让整次请求失败。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from sheetmate.agent.contracts import PLAN_REVIEW_SCHEMA, PLAN_SCHEMA
from sheetmate.agent.executor import build_system_prompt
from sheetmate.agent.llm import LLMError, aux_json
from sheetmate.agent.prompts import CLARIFY_PROMPT, THINK_PLAN_SYSTEM, THINK_REVIEW_SYSTEM
from sheetmate.agent.reporting import public_sentence, strip_step_label
from sheetmate.agent.tools import AgentContext
from sheetmate.engine.analysis import table_info
from sheetmate.engine.sqlutil import frame_to_text

MAX_PLAN_STEPS = 8
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
    show_plan: bool = False  # 规划/审查模型判断是否展示任务清单，未给出有效判断时隐藏
    reviewed: bool = False  # 审查确实执行并给出了结论
    changes: list[str] = field(default_factory=list)  # 审查时发现并修订的问题
    fallback: bool = False  # 规划失败后的兜底计划
    reason: str = ""  # 规划失败（兜底计划）或审查没完成的原因，给用户看

    def to_json(self) -> dict[str, Any]:
        return {"title": self.title, "understanding": self.understanding, "assumptions": self.assumptions,
                "risks": self.risks, "steps": self.steps, "show_plan": self.show_plan}

    def overview(self) -> str:
        """给执行助手看的整份计划，让它知道每一步在全局里的位置。"""
        lines = []
        if self.understanding:
            lines.append(f"理解：{self.understanding}")
        if self.assumptions:
            lines.append("默认口径：\n" + "\n".join(f"- {item}" for item in self.assumptions))
        if self.risks:
            lines.append("需要留意：\n" + "\n".join(f"- {item}" for item in self.risks))
        steps = [f"{step['id']}. {step['description']}" + (f"（验收：{step['check']}）" if step.get("check") else "")
                 for step in self.steps]
        lines.append("步骤：\n" + "\n".join(steps))
        return "\n".join(lines)

    # ---- 展示：任务清单由模型决定，每项说明保留简洁表达
    @property
    def narrated(self) -> bool:
        """两项以上、且不是兜底计划：每一项开始时向用户说一句现在要做什么。只有一项时说了也只是重复需求。"""
        return len(self.steps) >= 2 and not self.fallback

    @property
    def checklist(self) -> bool:
        """按模型判断展示任务清单和最终回顾；规划失败的兜底计划不展示。"""
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
        return f"{text}。" if text else ""

    def doing(self, num: int) -> str:
        """第 num 项进行中时「正在做什么」的短句（界面上随进度闪动的那一行）。"""
        text = _CONNECTOR.sub("", strip_step_label(self.steps[num - 1]["description"])).strip().rstrip("。；;，, ")
        text = text if len(text) <= 40 else text[:39] + "…"
        return f"正在{text}" if text and not text.startswith("正在") else text or "正在处理"

    def review_line(self) -> str:
        """计划审查的结果，如实写：审查没有做成就说没有做成，不能让用户以为审过了。"""
        if self.reviewed:
            changes = [item for item in (public_sentence(change, 80) for change in self.changes[:2]) if item]
            return ("计划已对照数据审查，补充了：" + "；".join(changes) + "。") if changes else "计划已对照数据审查，没有发现问题。"
        why = public_sentence(self.reason, 80)
        return f"计划审查没有完成（{why}），按原计划进行。" if why and not self.fallback else ""

    def intro(self) -> str:
        """开场白（给用户看）：怎么理解的需求、按什么口径做、数据里要留意什么、计划审查的结果。
        模型决定展示清单时，具体做哪几项由输入框上方的清单展示，这里不再重复列一遍；没有内容时返回空字符串。"""
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
    """调用方自带步骤时仍执行和核对；跳过了模型规划，没有展示判断时默认隐藏清单。"""
    return Plan(steps=_clean_steps(steps) or [{"id": 1, "description": "完成需求", "check": ""}])


def clean_text(value: Any, limit: int) -> str:
    """模型返回的字段可能不是字符串：不是就当作空。"""
    return clip(value.strip(), limit) if isinstance(value, str) else ""


def clean_list(value: Any, limit: int, item_limit: int = 200) -> list[str]:
    """字符串列表：丢掉非字符串和空项，限制条数和每条长度。"""
    if not isinstance(value, list):
        return []
    items = [clean_text(item, item_limit) for item in value if isinstance(item, str)]
    return [item for item in items if item][:limit]


def _clean_steps(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    steps = []
    for item in value:
        if isinstance(item, str):
            item = {"description": item}
        if not isinstance(item, dict):
            continue
        # 模型偶尔会在描述里写「第 N 步：」，用户看到的清单和开场白里不要编号
        description = strip_step_label(clean_text(item.get("description"), 400))
        if description:
            step = {"description": description, "check": clean_text(item.get("check"), 300)}
            narration = strip_step_label(clean_text(item.get("narration"), 160))
            if narration:
                step["narration"] = narration
            steps.append(step)
    if len(steps) > MAX_PLAN_STEPS:
        # 最后一步通常是核对与交付，截断时保留它。
        steps = [*steps[: MAX_PLAN_STEPS - 1], steps[-1]]
    return [{"id": index, **step} for index, step in enumerate(steps, start=1)]


def _parse_plan(result: Any) -> Plan | None:
    if not isinstance(result, dict):
        return None
    steps = _clean_steps(result.get("steps"))
    if not steps:
        return None
    return Plan(
        title=clean_text(result.get("title"), 40) or "执行计划",
        understanding=clean_text(result.get("understanding"), 300),
        assumptions=clean_list(result.get("assumptions"), 6),
        risks=clean_list(result.get("risks"), 6),
        steps=steps,
        show_plan=result.get("show_plan") is True,
    )


def fallback_plan(ctx: AgentContext, message: str, reason: str = "") -> Plan:
    has_tables = bool(ctx.visible_tables())
    return Plan(
        title="执行需求",
        steps=[
            {"id": 1, "description": "查看相关表的结构和数据" if has_tables and ctx.mode != "create" else "理解需求并确定表格结构",
             "check": "确认用到的表和列真实存在"},
            {"id": 2, "description": f"执行需求：{clip(message, 300)}", "check": "结果能和来源数据对上（行数、合计）"},
            {"id": 3, "description": "整理并说明结果", "check": "回答中的数字与工具结果一致"},
        ],
        fallback=True,
        reason=reason,
    )


def _history_context(history: list[dict[str, Any]], count: int = 6, limit: int = 1500) -> list[dict[str, str]]:
    return [{"role": item["role"], "content": str(item.get("content", ""))[-limit:]} for item in history[-count:]]


def _image_kwargs(ctx: AgentContext, history: list[dict[str, Any]]) -> dict[str, Any]:
    parts = [part for item in history[-6:] for part in item.get("image_parts", [])] + ctx.image_parts
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
    return _parse_plan(outcome.value) or fallback_plan(ctx, message, "模型返回的计划无法使用")


async def review_plan(llm: Any, ctx: AgentContext, message: str, plan: Plan, brief: str,
                      history: list[dict[str, Any]] | None = None) -> Plan:
    """再起一次大模型交互，对照需求和数据审查计划；有问题就采用修订版。
    限时；审查失败、超时或结果无法使用时保留原计划（并记下原因）。"""
    if not plan.worth_reviewing:
        return plan
    payload = {"requirement": message, "data_profile": brief, "plan": plan.to_json()}
    outcome = await aux_json(llm, json.dumps(payload, ensure_ascii=False), system=THINK_REVIEW_SYSTEM, timeout=REVIEW_TIMEOUT,
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
