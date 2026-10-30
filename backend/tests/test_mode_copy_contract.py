from __future__ import annotations

from sheetmate.agent.tools import AgentContext, ToolRegistry, _recipe_result_text
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workspace import Workspace
from sheetmate.recipe.schema import Recipe


def test_analysis_can_transform_but_editing_cannot_bypass_template_writer():
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


def test_template_failure_is_explained_and_does_not_offer_a_downgraded_file():
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
    assert "不能通过普通表格导出来绕过格式要求" in text
    assert "重复相同的修订尝试" in text
