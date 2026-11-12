"""Shared prompt assembly for direct execution, planning and specialist agents."""
from __future__ import annotations

import json
import re
from typing import Any
from sheetmate.agent.memory.short_term.context import ContextBlock, assemble_context
from sheetmate.agent.memory.short_term.task_state import task_projection
from sheetmate.agent.llm.prompts import AGENT_SYSTEM, AGENT_TABLE_LINE, MODE_RULES, THINKING_RULES
from sheetmate.agent.memory.short_term.tokens import estimate_tokens
from sheetmate.agent.tools.analysis.table_probe import table_probe_context
from sheetmate.agent.tools.contracts import AgentContext

_ROLE_TAGS = {"input": "", "expected": "，⭐期望结果（人工做好的样例）", "derived": "，分析结果", "result": "，流程产出"}

_SCRIPT_TOOL = "run_python_script"


def build_customer_block(ctx: AgentContext) -> str:
    """客户身份独立于聊天历史和所选流程，每轮从宿主提供的最新资料构建。"""
    if not ctx.customer:
        return "未绑定客户：本会话的数据和会话记忆只属于这个会话。用户以后可以把会话保存到某个客户名下；不要根据文件名猜测客户身份。"
    customer = ctx.customer
    lines = [f"已绑定客户「{customer['name']}」：这是本轮的明确客户身份，不需要用户重复介绍。"]
    if customer.get("code"):
        lines.append(f"客户编号：{customer['code']}")
    if customer.get("description"):
        lines.append(f"客户说明：{customer['description']}")
    summary = (customer.get("summary") or "").strip()
    if summary:
        lines.append("已保存的客户处理说明（派生摘要，具体条件以原始记忆和当前用户要求为准）：\n" + summary)
    if customer.get("recipe_id"):
        lines.append(f"当前流程：{customer.get('recipe_name') or '已选择的流程'}（ID：{customer['recipe_id']}），使用这个流程的资料、字段映射和记忆。")
    recipe_summary = (customer.get("recipe_summary") or "").strip()
    if recipe_summary:
        lines.append("当前流程的处理逻辑：\n" + recipe_summary)
    lines.append("这些客户资料和下方已保存记忆由应用长期存储，每轮按当前客户绑定读取；同一客户的新旧对话都可以使用，与本轮是否执行表格任务无关。")
    return "\n".join(lines)

def _recipe_context_blocks(ctx: AgentContext, message: str, recipes: list[dict[str, Any]]) -> list[ContextBlock]:
    """Keep a small relevant directory ahead of large reference facts.

    This is a directory, not the recipe implementation. Exact IDs and versions
    remain intact; long descriptions and input/match lists advertise excerpts.
    Detailed steps are still fetched through ``get_recipe`` on demand.
    """
    if not recipes:
        return []

    def excerpt(value: Any, chars: int) -> str:
        text = " ".join(str(value or "").split())
        return text if len(text) <= chars else text[:chars] + "…（片段）"

    def line(recipe: dict[str, Any]) -> str:
        inputs = list(recipe.get("inputs") or [])
        inputs_text = "、".join(excerpt(value, 40) for value in inputs[:4])
        if len(inputs) > 4:
            inputs_text += f"（另 {len(inputs) - 4} 项，按 ID 读取）"
        return (f"- recipe_id={recipe['recipe_id']}「{excerpt(recipe.get('name'), 80)}」v{recipe['version']}："
                f"{excerpt(recipe.get('description'), 80)} 输入 {inputs_text or '（无）'}，{recipe['step_count']} 步"
                + (f"；与当前表匹配：{excerpt(recipe['match'], 180)}" if recipe.get("match") else ""))

    current = (ctx.customer or {}).get("recipe_id") or getattr(ctx.host, "recipe_id", None)
    query = message.casefold()

    def relevance(recipe: dict[str, Any]) -> int:
        if recipe["recipe_id"] == current:
            return 3
        name = str(recipe.get("name") or "").strip().casefold()
        if str(recipe["recipe_id"]).casefold() in query or (len(name) >= 2 and name in query):
            return 2
        return 1 if recipe.get("match") else 0

    ranked = sorted(recipes, key=relevance, reverse=True)
    preferred = [recipe for recipe in ranked if relevance(recipe)]
    selected_ids: set[str] = set()

    def directory(items: list[dict[str, Any]], *, source: str, heading: str,
                  budget: int, priority: int) -> ContextBlock:
        note = "目录仅作索引；具体步骤用 get_recipe 按 ID 读取，其他流程用 list_recipes 查看。"
        lines = [heading, note]
        used = estimate_tokens("\n".join(lines)) + 100  # Reserve the omission receipt.
        selected = []
        for recipe in items:
            rendered = line(recipe)
            size = estimate_tokens(rendered) + 1
            if used + size > budget:
                continue
            lines.append(rendered)
            selected.append(str(recipe["recipe_id"]))
            selected_ids.add(str(recipe["recipe_id"]))
            used += size
        omitted = len(items) - len(selected)
        if omitted:
            lines.append(f"（本段目录省略 {omitted} 项；流程原文仍保存，调用 list_recipes / get_recipe 按需读取。）")
        return ContextBlock(source, "\n".join(lines), priority=priority, references=tuple(selected),
                            retrieve="调用 list_recipes / get_recipe 按需读取流程。")

    blocks = []
    if preferred:
        blocks.append(directory(preferred, source="relevant_recipes", heading="## 当前或相关的处理流程（Recipe，优先复用）",
                                budget=2_000, priority=90))
    remaining = [recipe for recipe in ranked if str(recipe["recipe_id"]) not in selected_ids]
    if remaining:
        blocks.append(directory(remaining, source="recipes", heading="## 其他已有的处理流程（Recipe 目录片段）",
                                budget=1_000, priority=30))
    return blocks

def build_system_prompt(ctx: AgentContext, message: str, *, thinking: bool = False) -> str:
    tables = ctx.visible_tables()
    table_lines = []
    for t in tables:
        cols = ", ".join(t.column_names[:14]) + (f" …（共 {len(t.columns)} 列）" if len(t.columns) > 14 else "")
        tag = _ROLE_TAGS.get(t.role, "")
        if t.created_by == "create_workbook":
            tag += f"，建表生成，file_id={t.file_id}（可查看布局并继续调整）"
        elif t.created_by == _SCRIPT_TOOL:
            tag += f"，脚本生成，file_id={t.file_id}（结果表可继续用公式或 SQL；文件副本可供必要的脚本处理）"
            script_run_id = (t.header or {}).get("script_run_id")
            if not script_run_id:
                match = re.search(r"run_id=([A-Za-z0-9_]+)", t.description or "")
                script_run_id = match.group(1) if match else None
            if script_run_id:
                tag += f"，run_id={script_run_id}"
        table_lines.append(AGENT_TABLE_LINE.format(name=t.name, label=t.label, rows=t.rows, cols=len(t.columns),
                                                   tag=tag, columns=cols))
    available_files = ctx.host.list_files()
    if ctx.table_filter is not None:
        allowed_file_ids = {table.file_id for table in tables if table.file_id}
        available_files = [item for item in available_files if item.get("file_id") in allowed_file_ids]
    files = [f for f in available_files if not f.get("tables")]
    replacements = [{"file_id": file["file_id"], "filename": file["filename"]} for file in available_files
                    if file["file_id"] in ctx.replace_file_ids]
    saving_rule = (
        "\n本轮更新这些已有结果：" + json.dumps(replacements, ensure_ascii=False)
        + "。基于用户指定对象继续调整或重算，沿用结果名称；新结果核对成功后由系统替换当前交付，不自行删除文件。"
        if replacements else "\n本轮未选定替换目标；需要生成文件时作为独立结果交付，已有适用文件直接使用。"
    )
    file_lines = [f"- file_id={f['file_id']}「{f['filename']}」（{f.get('scope_label', '')}）"
                  + ("，图片附件（非工作簿，无工作表）" if (f.get("media_type") or "").startswith("image/") else "")
                  + (f"，脚本 run_id={f['script_run_id']}（可查看脚本后继续调整）" if f.get("script_run_id") else "")
                  for f in files]
    recipes = ctx.host.list_recipes() if ctx.table_filter is None else []
    recipe_blocks = _recipe_context_blocks(ctx, message, recipes)
    formula_menu = ""
    if ctx.table_filter is None and ctx.tool_group == "core" and not (ctx.enabled_tool_groups & {"formulas", "all"}):
        specs = [*ctx.library.customer_specs(), *ctx.library.builtin_specs()]
        formula_menu = "\n\n可按需加载的公式（load_tool_group group=formulas）：" + "、".join(
            f"{spec.id}（{spec.name}）" for spec in specs if spec.id != "custom_sql")[:1600]
    policy = AGENT_SYSTEM.partition("\n## 客户\n")[0].format(
        mode_label={"analyze": "分析", "modify": "修改", "create": "新建"}.get(ctx.mode, "分析"),
        mode_rules=MODE_RULES.get(ctx.mode, MODE_RULES["analyze"]) + (
            "\n本轮用户已明确要求保存或更新重复流程，可在处理完成后保存。"
            if ctx.save_recipe_requested else
            "\n本轮未授权保存重复流程：只完成本次处理，不调用 save_as_recipe，不保存或更新流程，也不主动询问是否保存。"
        ) + (
            "\n用户已经确认删除这些流程 ID：" + "、".join(sorted(ctx.delete_recipe_ids)) + "。直接执行，不重复询问。"
            if ctx.delete_recipe_ids else
            "\n用户要求删除流程时，直接调用 delete_recipe 或 delete_recipes，由工具列出目标并请求一次确认。删除全部流程用 delete_recipes(all=true)。不需要开启额外授权，确认后系统直接执行。"
        ) + saving_rule,
    )
    task = ctx.task_state_block or task_projection(ctx.task_state)
    scope = "table_specialist" if ctx.table_filter is not None else "conversation"
    stage = "specialist" if ctx.table_filter is not None else "planning" if thinking else "execution"
    blocks = [
        ContextBlock("system_policy", policy + (THINKING_RULES if thinking else ""), required=True),
        ContextBlock("recipe_edit", ("## 用户选中的流程修改对象（已由服务端校验）\n"
            + json.dumps(ctx.recipe_edit, ensure_ascii=False)
            + "\n结合当前消息理解操作；如果只是询问解释，不执行修改。要求修改处理逻辑时必须使用 revise_recipe，明确 recipe_id、base_version、instruction，只是改名字、说明或脚本文字（输入/输出文件名、步骤名、脚本名、脚本内容）时改用 edit_recipe_nodes 直接修改，不必重新生成流程；"
              "单步修改传 step_ids，输入变更传 input_changes；通过完整流程重跑适配依赖并核对结果后才能报告完成。"
              "移除输入或输出指解除流程绑定/交付声明，原文件与历史记录保留。用户上传的新输入必须实际绑定到重跑，不能只改名称。"
              "除用户要求及必要的依赖适配外保留原流程。重跑失败不得声称已更新。"
            if ctx.recipe_edit else ""), required=True),
        ContextBlock("customer", "## 客户\n" + build_customer_block(ctx), required=True),
        ContextBlock("business_memory", "## 客户与当前流程的长期记忆（用户维护的规则优先于 AI 参考）\n" + (ctx.host.memory_text(message) or "（没有）"), required=True),
        ContextBlock("task_requirements", "## 当前任务状态\n" + task if task else "", required=True,
                     references=tuple(str(value) for value in ctx.task_state.get("source_message_ids", []))),
        ContextBlock("tables", "## 工作区里的表（调用工具时用「表名」）\n" + ("\n".join(table_lines) or (
                     "（还没有表，可直接根据用户文字或资料新建工作簿）" if ctx.mode == "create" else
                     "（还没有表；可回答问题，需要处理现有数据时再加载 Excel / CSV）")), priority=80,
                     scope=scope, references=tuple(t.name for t in tables), retrieve="调用 table_info / preview_table 读取当前版本结构。"),
        ContextBlock("files", "## 可加载的文件\n" + ("\n".join(file_lines) or "（没有）"), priority=50,
                     scope=scope, references=tuple(str(f["file_id"]) for f in files), retrieve="调用 list_files 按范围查找可访问原文件。"),
        ContextBlock("table_memory", table_probe_context(ctx.ws, tables, ctx.file_memory if ctx.table_filter is None else "", query=message),
                     priority=70, scope=scope, references=tuple(t.name for t in tables), retrieve="调用 table_info 或具体统计工具补读当前版本依据。"),
        *recipe_blocks,
        ContextBlock("formula_catalog", formula_menu, priority=10, retrieve="调用 load_tool_group(group='formulas') 加载完整算子。"),
    ]
    return assemble_context(blocks, stage=stage)
