from __future__ import annotations

import pytest

from sheetmate.agent.tools import AgentContext, ToolRegistry, _recipe_result_text
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workspace import Workspace
from sheetmate.recipe.schema import Recipe


def test_analysis_can_transform_and_editing_uses_recipe_writer():
    workspace = Workspace(None)
    try:
        context = AgentContext(mode="analyze", ws=workspace, library=FormulaLibrary(), host=None,
                               llm=None, conversation_id="copy-contract")
        registry = ToolRegistry()
        analysis_tools = {tool.name for tool in registry.for_context(context)}
        assert {"filter_rows", "group_aggregate", "export_excel"} <= analysis_tools
        assert "build_recipe" not in analysis_tools
        context.mode = "modify"
        modification_tools = {tool.name for tool in registry.for_context(context)}
        assert {"build_recipe", "revise_recipe", "run_recipe"} <= modification_tools
        assert not {"filter_rows", "group_aggregate", "export_excel"} & modification_tools
    finally:
        workspace.close()


def test_template_failure_requests_recovery_on_copy_without_permission_gate():
    recipe = Recipe(name="删除排班行")
    result = {
        "recipe": recipe,
        "report": {
            "status": "failed",
            "steps": [],
            "error": "TemplatePreservationError: 本次行列重排会影响原表格式，已停止输出，原文件未改动",
            "escalation": "",
        },
        "summary": "执行失败",
    }
    text = _recipe_result_text(result)
    assert "原始文件未修改" in text
    assert "未生成可交付文件" in text
    assert "重新复制原件后按步骤处理" in text
    assert "格式或结构变化不需要再次授权" in text


@pytest.mark.parametrize("status", ["success", "warning"])
def test_delivered_output_reports_warnings_without_repeating_old_questions(status):
    recipe = Recipe(name="删除并重编号", metadata={"questions": [{"question": "是否以本次编号要求为准？"}]})
    result = {
        "recipe": recipe,
        "report": {
            "status": status,
            "steps": [],
            "error": "",
            "escalation": "",
            "output_warnings": ["已在新副本中重建备注合并区域，请核对布局。"],
            "outputs": [{"sheet": "排班", "rows": 26, "columns": ["序号", "姓名"]}],
        },
        "summary": "已删除并重编号",
        "file": {"filename": "排班_结果.xlsx"},
    }
    text = _recipe_result_text(result)
    assert "输出提示：已在新副本中重建备注合并区域，请核对布局。" in text
    assert "输出文件：「排班_结果.xlsx」" in text and "26 行" in text
    assert "未生成可交付文件" not in text
    assert "是否以本次编号要求为准" not in text
    assert "流程提出的待核实信息" not in text
    assert "需要和用户确认" not in text


@pytest.mark.parametrize("has_file", [False, True])
def test_failed_run_keeps_unresolved_business_questions(has_file):
    question = "同名员工如何区分，请提供工号？"
    recipe = Recipe(name="排班修改", metadata={"questions": [{"question": question}]})
    result = {
        "recipe": recipe,
        "report": {"status": "failed", "steps": [], "error": "缺少工号", "escalation": ""},
        "summary": "尚未完成",
        "file": {"filename": "待核对.xlsx"} if has_file else None,
    }
    text = _recipe_result_text(result)
    assert question in text
    assert "当前明确要求优先旧记忆" in text
