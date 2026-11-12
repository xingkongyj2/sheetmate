"""registry tool capabilities."""

from __future__ import annotations

import json
from typing import Any
from sheetmate.agent.memory.short_term.task_state import inspect_task
from sheetmate.agent.tools.analysis.builtin import (
    _tool_column_stats,
    _tool_preview,
    _tool_run_sql,
    _tool_table_info,
    _tool_value_counts,
)
from sheetmate.agent.tools.contracts import AgentContext, Tool, ToolResult, _obj
from sheetmate.agent.tools.files.builtin import (
    _tool_export_excel,
    _tool_list_files,
    _tool_load_file,
    _tool_show_files,
)
from sheetmate.agent.tools.formulas.builtin import formula_tool
from sheetmate.agent.tools.interaction.builtin import _tool_ask_user, _tool_inspect_evidence
from sheetmate.agent.tools.memory.builtin import _tool_remember, _tool_save_field_mapping
from sheetmate.agent.tools.workflows.builtin import (
    _stream_build_recipe,
    _stream_revise_recipe,
    _stream_run_recipe,
    _tool_delete_recipe,
    _tool_delete_recipes,
    _tool_get_recipe,
    _tool_list_recipes,
    _tool_save_as_recipe,
)


_STR = {"type": "string"}


_STR_LIST = {"type": "array", "items": {"type": "string"}}


def _static_tools() -> list[Tool]:
    from sheetmate.agent.tools.workbooks.skill import workbook_creation_tool, workbook_inspection_tool
    from sheetmate.agent.tools.scripts.skill import python_script_tools

    both = frozenset({"analyze", "modify", "create"})
    analyze, modify = frozenset({"analyze"}), frozenset({"modify"})
    return [
        Tool("read_tool_result", "按 tool_result_id=tr_… 引用分页读取已保存的完整安全工具结果；只接受实际返回的 tr_ ID。ev_ 是执行证据，应调用 inspect_execution_evidence(evidence_id='ev_…',section='text')。text 按文本分页，tables/events/payload 等结构按 JSON 字符分页。只能读取当前会话和当前专员范围内的结果。",
             _obj({"result_id": {**_STR, "pattern": "^tr_[a-f0-9]{64}$", "description": "实际返回的 tool_result_id（tr_ 前缀），不能填 evidence_id（ev_ 前缀）。"}, "section": {"type": "string", "enum": [
                 "text", "summary", "tables", "events", "public_text", "public_summary", "public_details", "error_details", "payload"]},
                   "offset": {"type": "integer", "minimum": 0},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 8000}}, ["result_id"]), both,
             run=lambda args, ctx: ToolResult(json.dumps(ctx.host.read_tool_result(
                 **args, branch_id=ctx.branch_id), ensure_ascii=False), summary="已读取完整工具结果片段"),
             label=lambda args: "回查工具结果"),
        Tool("inspect_execution_evidence", "按 evidence_id=ev_… 分页读取实际执行记录；tr_ 工具结果引用应调用 read_tool_result(result_id='tr_…',section='text')。section=text（默认）读取当次工具返回原文，args 读取原始调用参数，facts 读取执行状态与源表版本；args/facts 按 JSON 文本分页。"
             "工具原文若是表格预览，回查不会补齐省略行，应按其 next_call 读取表。args 若以 script_id 引用代码，请先用 text 取得 run_id，再调用 inspect_python_script 读取完整代码。",
             _obj({"evidence_id": {**_STR, "pattern": "^ev_[A-Za-z0-9_]{1,97}$", "description": "实际返回的 evidence_id（ev_ 前缀），不能填 tool_result_id（tr_ 前缀）。"}, "section": {"type": "string", "enum": ["text", "args", "facts"]},
                   "offset": {"type": "integer", "minimum": 0},
                   "limit": {"type": "integer", "minimum": 100, "maximum": 8000}}, ["evidence_id"]), both,
             run=_tool_inspect_evidence, label=lambda args: "回查执行证据"),
        Tool("inspect_task_context", "按消息 ID 分页读取当前任务的用户原始要求，或按关键词查找。摘要未完整展示的要求必须按需回查。",
             _obj({"message_id": {"type": "integer"}, "query": _STR, "offset": {"type": "integer", "minimum": 0},
                   "limit": {"type": "integer", "minimum": 100, "maximum": 8000}}), both,
             run=lambda args, ctx: ToolResult(json.dumps(inspect_task(ctx.task_state, **args), ensure_ascii=False), summary="已读取任务原始要求"),
             label=lambda args: "查看任务要求"),
        Tool("inspect_conversation_history", "按需检索本会话全部历史原消息，包括已压缩内容和已完成旧任务。省略 message_id 时按关键词/角色检索片段，offset/limit 为消息条数（默认8，最多12）；指定 message_id 后分页读取原文，offset/limit 为字符数（默认4000，最多8000）。不读取其他会话；旧助手声明不代表当前事实或完成证据。",
             _obj({"message_id": {"type": "integer", "minimum": 1}, "query": {**_STR, "maxLength": 200},
                   "role": {"type": "string", "enum": ["user", "assistant"]},
                   "offset": {"type": "integer", "minimum": 0},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 8000}}), both,
             run=lambda args, ctx: ToolResult(json.dumps(ctx.host.inspect_conversation_history(**args), ensure_ascii=False),
                                              summary="已检索会话原始消息"),
             label=lambda args: "回查历史原文"),
        Tool("inspect_saved_memories", "只检索当前可用的个人/客户/会话/流程记忆。省略 memory_id 时按关键词找相关记忆或列出近期记忆，offset/limit 按条目分页（最多12条）；指定 memory_id 后按字符分页读取原文。结果带记忆版本、来源和范围；不扩大当前权限范围。",
             _obj({"memory_id": {"type": "integer", "minimum": 1}, "query": {**_STR, "maxLength": 200},
                   "offset": {"type": "integer", "minimum": 0},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 8000}}), both,
             run=lambda args, ctx: ToolResult(json.dumps(ctx.host.inspect_saved_memories(**args), ensure_ascii=False),
                                              summary="已检索当前作用域记忆"),
             label=lambda args: "回查已保存记忆"),
        Tool("load_tool_group", "按需加载工具定义：formulas=清洗/匹配/汇总公式，workbook=建表与格式，python=脚本，workflow=已有流程/保存/删除，all=全部。只加载能力，不执行或授权操作。",
             _obj({"group": {"type": "string", "enum": ["formulas", "workbook", "python", "workflow", "all"]}}, ["group"]), both,
             run=_tool_load_group, label=lambda args: "加载处理工具"),
        *python_script_tools(),
        workbook_creation_tool(),
        workbook_inspection_tool(),
        Tool("table_info", "［表格探测技能］读取当前表版本的结构化长期记忆：全部列名、类型、空值、不同值数量、样本、候选主键和前几行。首次补测后自动持久保存，表未更新直接复用。系统已提供所需结构时直接使用，仅缺少必要信息时调用。",
             _obj({"table": {**_STR, "description": "表名"}}, ["table"]), both, run=_tool_table_info,
             label=lambda a: f"查看结构 · {a.get('table', '')}"),
        Tool("value_counts", "统计某一列的取值分布（出现次数和占比），按当前表版本和参数保存并复用探测结果。已有相同范围分布时直接使用。",
             _obj({"table": _STR, "column": _STR, "limit": {"type": "integer", "minimum": 1, "maximum": 10000, "description": "最多返回多少个取值，默认 20，最多 10000"}},
                  ["table", "column"]), both, run=_tool_value_counts, label=lambda a: f"取值分布 · {a.get('column', '')}"),
        Tool("column_stats", "列的描述统计：最小、最大、均值、分位数、空值比例，按当前表版本和字段保存并复用。已有所需统计时直接使用。",
             _obj({"table": _STR, "columns": {**_STR_LIST, "description": "可选，只统计这些列"}}, ["table"]), both,
             run=_tool_column_stats, label=lambda a: f"描述统计 · {a.get('table', '')}"),
        Tool("preview_table", "读取表数据供分析核对，按字符预算返回完整行；complete 仅表示本条观察是否包含整表，不代表源数据读取质量。截断时按 next_call 及 expected_revision 续读；超长行的 json_fragment 按 text_offset 拼接同一行后解析。正式明细和较大结果通过文件交付。",
             _obj({"table": _STR, "offset": {"type": "integer", "minimum": 0},
                   "limit": {"type": "integer", "minimum": 1, "maximum": 100, "description": "行读取上限，默认 100；实际回传还受字符预算约束。"},
                   "expected_revision": {**_STR, "minLength": 1, "maxLength": 100, "description": "续读时填写上次返回的 revision；版本变化会停止拼接。"},
                   "text_offset": {"type": "integer", "minimum": 0, "description": "仅用于 json_fragment，照抄 next_call 的字符偏移，并同时传 expected_revision。"}}, ["table"]),
             both, run=_tool_preview, label=lambda a: f"查看数据 · {a.get('table', '')}"),
        Tool("run_sql", "［优先数据处理工具］在分析副本上执行一条 DuckDB SELECT，适合关联、筛选、分组汇总、窗口计算和复杂条件。列名和表名用双引号。给 result_name 保存有版本的结果表，可按 next_call 稳定续读。未命名查询受读取上限约束，total_rows=null 表示总数未知；不完整时需提供 result_name 重新执行，不能拼接两次执行结果。0 行查询无需展示空表，正式明细或较大结果需通过文件交付。",
             _obj({"sql": {**_STR, "description": "一条 SELECT 语句"},
                   "result_name": {**_STR, "description": "可选：辅助分析结果表名，两种模式均可使用"},
                   "description": {**_STR, "description": "可选：这条查询做什么"}}, ["sql"]),
             both, run=_tool_run_sql, label=lambda a: "SQL 查询" + (f" → {a['result_name']}" if a.get("result_name") else "")),
        Tool("list_files", "查找当前会话或客户可访问的输入、期望结果、输出文件及准确 ID；只返回清单，不会向用户展示文件卡片。", _obj({}), both,
             run=_tool_list_files, label=lambda a: "查看文件"),
        Tool("show_files", "向用户展示任意已有输入、期望结果或输出文件的原生查看和下载卡片；保留原文件，不读取数据、不重新导出。查看单个用 file_id，多个用 file_ids，全部用 all=true，按单种类型用 kind，多种类型用 kinds；原始文件对应 input，不按文件名判断。用户只是要看文件时必须调用本工具，不能用 preview_table 或 Markdown 数据表代替。",
             _obj({"file_id": {**_STR, "description": "单个已有文件的 ID；与 file_ids、all=true 三选一"},
                   "file_ids": {**_STR_LIST, "description": "多个已有文件的 ID；按提供顺序展示并去重"},
                   "all": {"type": "boolean", "description": "true 展示当前客户或会话全部可访问文件；可结合 kind 筛选"},
                   "kind": {"type": "string", "enum": ["input", "expected", "output"], "description": "筛选单种文件类型；input为原始输入、expected为期望结果、output为已有输出。单独提供时展示该类型全部文件"},
                   "kinds": {"type": "array", "items": {"type": "string", "enum": ["input", "expected", "output"]},
                             "description": "筛选多种文件类型；例如查看所有输入输出用 [input,output]，与 kind 互斥；单独提供时展示这些类型全部文件"}}),
             both, run=_tool_show_files, label=lambda a: "展示文件"),
        Tool("load_file", "把一个文件加载到工作区（每个 sheet 一张表）。",
             _obj({"file_id": _STR}, ["file_id"]), both, run=_tool_load_file, label=lambda a: "加载文件"),
        Tool("export_excel", "把一张或多张已核对的结果表导出成 Excel，由文件卡片提供查看和下载。正式明细或较大结果使用此工具交付，不把完整数据粘贴到回复中；已有交付文件不重复导出。",
             _obj({"sheets": {"type": "array", "items": {"anyOf": [_STR, _obj({"table": _STR, "sheet_name": _STR}, ["table"])]}},
                   "filename": {**_STR, "description": "文件名（不含扩展名）"}}, ["sheets"]),
             analyze, run=_tool_export_excel, label=lambda a: f"导出 Excel · {a.get('filename', '')}"),
        Tool("build_recipe",
             "［数据修改优先］规划并执行本次表格处理步骤：比对输入和人工结果（如有），优先使用公式或 SQL，仅为它们不好做或超出范围的部分加入 Python，可混合步骤，由相应引擎执行、"
             "出错自动修复、校验并验证能否复现人工结果，最后输出 Excel。调用本工具不代表保存流程；仅当用户明确要求保存或保留本套规则供以后复用时才持久化。",
             _obj({"requirement": {**_STR, "description": "清楚的处理说明：要做哪些处理、业务规则、输出什么（相当于 SheetAgent 的 process.md）"},
                   "input_tables": {**_STR_LIST, "description": "原始上传的输入表名；不填自动使用。辅助分析结果用于完善 requirement，不代替原始输入"},
                   "input_files": {"type": "object", "additionalProperties": {"type": "string"}, "description": "可选：{完整文件输入别名: file_id}，用于 Python 排版或读取原始工作簿；与 input_tables 可同时提供"},
                   "expected_tables": {**_STR_LIST, "description": "人工做好的期望结果表（如有）；不填会自动使用标记为期望结果的表"},
                   "recipe_name": {**_STR, "description": "可选：本次处理名称，不构成保存授权"}}, ["requirement"]),
             modify, stream=_stream_build_recipe, label=lambda a: "处理表格"),
        Tool("revise_recipe", "通过对话修改流程的输入、输出或指定步骤，并从输入开始重新执行完整流程、适配上下游依赖和验证。"
             "输入新增/删除/替换用 input_changes；单步修改用 step_ids；输出表增删改可提供完整 output_sheets。"
             "删除流程中的输入/输出仅修改本次处理定义，不删除用户原文件。仅在用户明确要求保存或长期更新规则时持久化，选中或修改流程本身不构成保存授权。",
             _obj({"instruction": {**_STR, "description": "修改要求"},
                   "recipe_id": {**_STR, "description": "可选：不填表示本会话最近的流程"},
                   "base_version": {"type": "integer", "minimum": 1, "description": "所修改流程的当前版本，选中流程时必须与选择版本相同；版本已更新时先刷新"},
                   "inputs": {"type": "object", "additionalProperties": {"type": "string", "minLength": 1}, "description": "可选：{保留输入的完整alias: 表名或文件file_id}。仅覆盖运行绑定，不增加或删除定义；结构变动用input_changes"},
                   "input_changes": {"type": "array", "maxItems": 40, "items": _obj({
                       "operation": {"type": "string", "enum": ["add", "replace", "remove"]},
                       "alias": {"type": "string", "minLength": 1, "description": "完整输入别名，删除文件时列出该文件的全部输入别名"},
                       "kind": {"type": "string", "enum": ["table", "file"], "description": "add默认table，replace默认沿用；remove不填"},
                       "source": {"type": "string", "minLength": 1, "description": "add/replace必填：table用真实原始输入表名，file用当前可访问file_id；remove不填"}}, ["operation", "alias"])},
                   "step_ids": {"type": "array", "uniqueItems": True, "items": {"type": "integer", "minimum": 1}, "description": "本次修改的真实步骤编号；其余步骤保留，仅因依赖适配所需时同步修改"},
                   "output_sheets": {"type": "array", "maxItems": 100, "description": "可选完整最终工作表声明，遗漏项将不再输出；保留未选中输出。[]明确取消普通表输出。脚本文件产物由instruction调整脚本声明", "items": _obj({
                       "name": {"type": "string", "minLength": 1}, "source": {"type": "string", "minLength": 1},
                       "description": _STR, "columns": {"type": ["array", "null"], "items": _STR},
                       "split_by": {"type": ["string", "null"]}}, ["name", "source"])},
                   "expected_tables": {**_STR_LIST, "description": "人工期望结果表；省略沿用当前样例，[]明确移除样例约束"}}, ["instruction"]),
             modify, stream=_stream_revise_recipe, label=lambda a: "修订处理流程"),
        Tool("run_recipe", "用已保存的公式、SQL、Python 或混合流程直接处理当前表和文件，不需要重新生成步骤或重写代码，输出正式结果。",
             _obj({"recipe_id": _STR, "inputs": {"type": "object", "description": "可选：{流程输入名: 表名或文件file_id}；键使用 get_recipe 返回的完整输入 alias；省略时优先沿用当前范围内的已有绑定，再按列或文件名匹配"},
                   "allow_repair": {"type": "boolean", "description": "出错时是否允许 AI 自动修复，默认 true"}}, ["recipe_id"]),
             modify, stream=_stream_run_recipe, label=lambda a: "执行已有流程"),
        Tool("save_as_recipe", "仅用户明确要求保存重复流程时调用，直接保存正式流程。支持公式、SQL、Python 脚本和混合流程；保存实际执行的代码与依赖，不需要重新处理文件。"
             "若 build_recipe、revise_recipe 或 run_recipe 已返回 recipe_saved，则同一流程已经保存，不要再次调用本工具。保存后界面自动展示流程图，无需再调用 get_recipe。",
             _obj({"output_tables": {**_STR_LIST, "description": "可选：公式、SQL 或脚本生成的最终结果表。不填则保存指定或最近已执行流程"}, "name": _STR,
                   "run_id": {**_STR, "description": "可选：明确指定脚本或混合流程的运行 ID，优先于 recipe_id"},
                   "recipe_id": {**_STR, "description": "可选：指定本会话已执行的流程 ID，不填用最近的"},
                   "description": {**_STR, "description": "流程说明"}}),
             both, run=_tool_save_as_recipe, label=lambda a: f"保存流程 · {a.get('name', '')}"),
        Tool("get_recipe", "在本轮对话中展示客户页同款原生流程图：单个用 recipe_id，多个用 recipe_ids，查看所有流程图直接用 all=true。界面自动绘制输入、步骤和输出，模型只需简短说明，不要画字符图或 Mermaid。只读，不执行、修改或保存流程。",
             _obj({"recipe_id": {**_STR, "description": "查看单个流程时指定 ID，与 recipe_ids、all=true 三选一"},
                   "recipe_ids": {**_STR_LIST, "description": "查看指定多个流程的 ID 列表"},
                   "all": {"type": "boolean", "description": "true 表示展示当前客户或会话全部已保存流程的原生流程图，无需先查 ID"}}),
             both, run=_tool_get_recipe, label=lambda a: "查看全部流程图" if a.get("all") is True else "查看流程"),
        Tool("list_recipes", "查看当前客户或会话已保存的流程及准确 ID。查找待删除流程或区分同名流程时使用。",
             _obj({}), both, run=_tool_list_recipes, label=lambda a: "查看已保存流程"),
        Tool("delete_recipe", "删除指定的已保存流程及全部版本。调用后由系统列出目标，请用户确认一次；确认后系统直接删除，无需额外授权。删除表格行或文件不等于删除流程。",
             _obj({"recipe_id": {**_STR, "description": "要删除的已保存流程 ID"}}, ["recipe_id"]),
             both, run=_tool_delete_recipe, label=lambda a: "删除已保存流程"),
        Tool("delete_recipes", "批量删除已保存流程及全部版本。用户说删除全部流程时使用 all=true；指定多条时提供 recipe_ids。系统列出清单并请用户确认一次，确认后直接执行。",
             _obj({"all": {"type": "boolean", "description": "true表示当前客户或会话的全部已保存流程"},
                   "recipe_ids": {**_STR_LIST, "description": "指定要删除的流程 ID 列表；all=true时无需提供"}}),
             both, run=_tool_delete_recipes, label=lambda a: "删除全部流程" if a.get("all") is True else "批量删除流程"),
        Tool("remember", "记住用户明确表达的偏好、业务规则或背景。必须引用本轮用户原话，不得把模型猜测或表格样本推断保存为规则；经验记忆需要已验证执行证据，当前工具不支持。",
             _obj({"category": {"type": "string", "enum": ["preferences", "domain_knowledge", "user_context", "history_insights"]},
                   "key": _STR, "value": {**_STR, "description": "逐字摘自用户原话的完整规则，不得改写推断；保留否定、条件及范围，不得保存本次临时例外。"},
                   "evidence_span": {**_STR, "description": "支持本条记忆的用户消息确切原文，不可引用助手文字或工具数据。"}},
                  ["category", "key", "value"]), both, run=_tool_remember,
             label=lambda a: f"记住 · {a.get('key', '')}"),
        Tool("save_field_mapping", "为当前客户保存字段映射：标准字段名 ← 各种表里出现的别名（以后读入时自动统一）。只把确定是同一个意思的列配成别名。",
             _obj({"standard": _STR, "aliases": _STR_LIST}, ["standard", "aliases"]), both, run=_tool_save_field_mapping,
             label=lambda a: f"字段映射 · {a.get('standard', '')}", needs_customer=True),
        Tool("ask_user", "需求有歧义、不同理解会得到明显不同的结果时，向用户提一个问题并给 2~4 个选项，然后停止等待回答。",
             _obj({"question": _STR, "options": _STR_LIST}, ["question", "options"]), both, run=_tool_ask_user,
             label=lambda a: "向你提问"),
    ]


def reserved_tool_names() -> set[str]:
    """固定工具的名字。公式会被注册成同名工具，所以客户公式的 id 不能和它们重名（services/workflows/service.py 保存公式时检查）。"""
    return {t.name for t in _static_tools()} | {"reconsider_approach"}


def _tool_load_group(args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    ctx.enabled_tool_groups.add(str(args["group"]))
    return ToolResult("相应工具已加载，下一轮可直接调用。加载工具不代表已经执行任何处理或获得保存/删除授权。", summary="已加载所需工具")


class ToolRegistry:
    # Read-only analysis keeps the common path small while preserving all
    # formula tools (they are often the cheapest exact operation). Heavy
    # workflow/code tools remain available through the full group.
    _HEAVY_TOOL_NAMES = frozenset({
        "run_python_script", "inspect_python_script", "create_workbook", "inspect_workbook_creation",
        "build_recipe", "revise_recipe", "run_recipe", "save_as_recipe", "get_recipe",
        "list_recipes", "delete_recipe", "delete_recipes",
    })
    _BRANCH_TOOL_NAMES = frozenset({"table_info", "value_counts", "column_stats", "preview_table", "run_sql", "ask_user", "inspect_task_context", "inspect_execution_evidence", "read_tool_result"})
    _GROUP_TOOLS = {
        "workbook": frozenset({"create_workbook", "inspect_workbook_creation"}),
        "python": frozenset({"run_python_script", "inspect_python_script"}),
        "workflow": frozenset({"build_recipe", "revise_recipe", "run_recipe", "save_as_recipe", "get_recipe", "list_recipes", "delete_recipe", "delete_recipes"}),
    }

    def __init__(self) -> None:
        self._static = {t.name: t for t in _static_tools()}
        for name in ("run_sql", "column_stats", "value_counts", "table_info", "preview_table"):
            self._static[name].process_safe = True
        for name in ("column_stats", "value_counts", "table_info", "preview_table"):
            self._static[name].parallel_read_safe = True

    def for_context(self, ctx: AgentContext, *, group: str | None = None) -> list[Tool]:
        tools = [t for t in self._static.values() if ctx.mode in t.modes and (not t.needs_customer or ctx.customer)]
        selected_group = group or ctx.tool_group
        if ctx.table_filter is not None:
            # Specialists analyse one slice; file/recipe/memory side effects
            # and cross-table work are owned by the coordinating agent.
            return [tool for tool in tools if tool.name in self._BRANCH_TOOL_NAMES]
        if selected_group == "core":
            enabled = self._HEAVY_TOOL_NAMES if "all" in ctx.enabled_tool_groups else frozenset().union(
                *(self._GROUP_TOOLS.get(group, frozenset()) for group in ctx.enabled_tool_groups))
            tools = [tool for tool in tools if tool.name not in self._HEAVY_TOOL_NAMES or tool.name in enabled]
        if ctx.mode in {"analyze", "modify", "create"} and (
            selected_group != "core" or bool(ctx.enabled_tool_groups & {"formulas", "all"})
        ):
            for spec in ctx.library.customer_specs():
                tools.append(formula_tool(spec, "customer"))
            for spec in ctx.library.builtin_specs():
                if spec.id != "custom_sql":
                    tools.append(formula_tool(spec, "builtin"))
        # 与选择规则一致，脚本入口放在公式、SQL 和流程工具之后。
        tools.sort(key=lambda tool: tool.name == "run_python_script")
        return tools

    def core_for_context(self, ctx: AgentContext) -> list[Tool]:
        """Return the low-cost read/transform tool set for a common analysis turn."""
        return self.for_context(ctx, group="core")

    def load_named_tool(self, name: str, ctx: AgentContext) -> Tool | None:
        """A remembered tool name may activate its group, never widen scope."""
        if ctx.table_filter is not None:
            return None
        tool = next((item for item in self.for_context(ctx, group="full") if item.name == name), None)
        if tool is not None:
            group = next((key for key, names in self._GROUP_TOOLS.items() if name in names),
                         "formulas" if name not in self._static else None)
            if group:
                ctx.enabled_tool_groups.add(group)
        return tool

    def find(self, name: str, ctx: AgentContext) -> Tool | None:
        for tool in self.for_context(ctx):
            if tool.name == name:
                return tool
        return None
