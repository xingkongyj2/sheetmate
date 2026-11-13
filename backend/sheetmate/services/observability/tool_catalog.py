"""统计页「工具调用」用的工具目录：全部工具的中文名、所属分类和一句话作用。

目录只用于展示（让没用过的工具也能列出来、并说明它是做什么的），不影响任何工具的行为。
固定工具的说明在下面手写；内置公式工具直接取公式自带的名称、分类和用途，不重复维护。
tests/test_tool_catalog.py 会拿它和真实的工具注册表对照：新增工具忘了写说明，测试会失败。
"""

from __future__ import annotations

import re
from typing import Any

# (分类, 工具名, 中文名, 一句话作用)。分类和工具在页面上按这里的顺序显示。
_STATIC: list[tuple[str, str, str, str]] = [
    ("查看与分析数据", "table_info", "查看表结构", "看每张表有哪些列、什么类型、长什么样"),
    ("查看与分析数据", "preview_table", "查看数据", "查看表里具体的数据行"),
    ("查看与分析数据", "value_counts", "取值分布", "统计某一列每个取值出现几次"),
    ("查看与分析数据", "column_stats", "描述统计", "算某一列的最大、最小、平均值等"),
    ("查看与分析数据", "run_sql", "SQL 查询", "用 SQL 查询、筛选、关联和汇总数据"),

    ("建表与脚本", "create_workbook", "新建 Excel", "按你的描述从零做一份带格式的 Excel"),
    ("建表与脚本", "inspect_workbook_creation", "查看建表布局", "查看新建表格的版式和数据，便于继续调整"),
    ("建表与脚本", "run_python_script", "运行脚本", "公式和 SQL 做不到时，用 Python 脚本处理"),
    ("建表与脚本", "inspect_python_script", "查看脚本记录", "查看某次脚本的代码、日志和输出，便于调整"),

    ("处理流程", "build_recipe", "生成流程", "规划并执行表格处理，沉淀成可重复使用的流程"),
    ("处理流程", "revise_recipe", "修订流程", "按你的要求修改已有流程，并重新跑一遍"),
    ("处理流程", "run_recipe", "执行流程", "用已保存的流程直接处理新文件"),
    ("处理流程", "save_as_recipe", "保存流程", "把这次的处理方法保存下来，以后重复使用"),
    ("处理流程", "get_recipe", "查看流程", "展示流程图，看流程有哪些步骤"),
    ("处理流程", "list_recipes", "查看已保存流程", "列出已保存的流程"),
    ("处理流程", "inspect_recipe_nodes", "查看流程节点", "看流程里哪些输入、步骤可以修改"),
    ("处理流程", "edit_recipe_nodes", "修改流程节点", "直接改流程的某个节点，存成新版本"),
    ("处理流程", "delete_recipe", "删除流程", "删除一个已保存的流程"),
    ("处理流程", "delete_recipes", "批量删除流程", "一次删除多个已保存的流程"),

    ("文件", "list_files", "查看文件", "列出当前对话或客户下的文件"),
    ("文件", "load_file", "加载文件", "把文件载入工作区，每个工作表成为一张表"),
    ("文件", "show_files", "展示文件", "以卡片形式展示文件，供你查看和下载"),
    ("文件", "export_excel", "导出 Excel", "把结果表导出成 Excel 交付给你"),
    ("文件", "rename_files", "修改文件名", "修改已上传文件的显示名称"),

    ("记忆与字段", "remember", "记住", "记住你明确说过的偏好、业务规则或背景"),
    ("记忆与字段", "save_field_mapping", "保存字段映射", "保存列名的不同叫法，以后读表时自动统一"),
    ("记忆与字段", "inspect_saved_memories", "查看已保存记忆", "查找已保存的偏好和规则"),

    ("回查与沟通", "ask_user", "向你提问", "要求有歧义时向你提问，等你选择后再继续"),
    ("回查与沟通", "inspect_task_context", "查看任务要求", "回看你本次任务的原始要求"),
    ("回查与沟通", "inspect_conversation_history", "回查历史原文", "检索本次对话里更早的内容"),
    ("回查与沟通", "read_tool_result", "回查工具结果", "回头读取某次工具返回的完整结果"),
    ("回查与沟通", "inspect_execution_evidence", "回查执行证据", "回头核对某次操作的真实执行记录"),
    ("回查与沟通", "load_tool_group", "加载处理工具", "按需加载更多处理工具"),
    ("回查与沟通", "reconsider_approach", "更换方法", "同一种方法连续失败时，换一种思路再试"),
]

_CUT = re.compile(r"[；（(]|，例如")


def _brief(text: str) -> str:
    """公式的用途说明有的带括号举例或补充，只取第一句。"""
    return _CUT.split(text.strip(), maxsplit=1)[0].strip("，。 ")


def tool_catalog() -> list[dict[str, Any]]:
    """全部工具：{tool, label, group, purpose}，按分类顺序排好。"""
    from sheetmate.engine.formulas import FormulaLibrary

    items = [{"tool": name, "label": label, "group": group, "purpose": purpose}
             for group, name, label, purpose in _STATIC]
    # 内置公式在对话里就是同名工具（custom_sql 由 run_sql 承担，不单独成为工具）。
    for spec in FormulaLibrary().builtin_specs():
        if spec.id == "custom_sql":
            continue
        items.append({"tool": spec.id, "label": spec.name, "group": f"公式 · {spec.category}",
                      "purpose": _brief(spec.intent)})
    # 公式分类按首次出现的顺序排在固定分类之前的位置：先看数据，再处理，再其他。
    order: dict[str, int] = {}
    for item in items:
        order.setdefault(item["group"], len(order))
    formulas_first = ["查看与分析数据"] + [g for g in order if g.startswith("公式 · ")]
    rest = [g for g in order if g not in formulas_first]
    rank = {g: i for i, g in enumerate(formulas_first + rest)}
    return sorted(items, key=lambda item: rank[item["group"]])  # sorted 是稳定排序，组内顺序保持不变
