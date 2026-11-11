from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from sheetmate.agent.tools.contracts import AgentContext
from sheetmate.agent.tools.registry import ToolRegistry
from sheetmate.agent.tools.workflows.builtin import _learn_context, _recipe_result_text, _resolve_inputs
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.tables.workspace import Workspace
from sheetmate.recipe.definition.compiler import compile_from_workspace
from sheetmate.recipe.definition.schema import Recipe


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
        assert {"filter_rows", "group_aggregate", "run_sql"} <= modification_tools
        assert "export_excel" not in modification_tools
    finally:
        workspace.close()


@pytest.mark.parametrize("mode", ["analyze", "modify"])
def test_auxiliary_tools_preserve_inputs_and_replayable_intermediate_steps(mode):
    workspace = Workspace(None)
    try:
        original = pd.DataFrame({"编号": [1, 2, 3], "金额": [10, 20, 30]})
        workspace.put_dataframe("原表", original, source="uploaded", role="input", file_id="source")
        context = AgentContext(mode=mode, ws=workspace, library=FormulaLibrary(), host=None,
                               llm=None, conversation_id="auxiliary")
        registry = ToolRegistry()
        query = registry.find("run_sql", context)
        first = query.run({"sql": 'SELECT * FROM "原表" WHERE "编号" > 1', "result_name": "辅助"}, context)
        first_name = first.tables[0]["name"]
        second = registry.find("compute_column", context).run(
            {"table": first_name, "column": "金额", "expression": '"金额" + 1', "result_name": "辅助"}, context)
        second_name = second.tables[0]["name"]
        assert second_name != first_name
        assert workspace.df(first_name)["金额"].tolist() == [20, 30]
        assert workspace.df(second_name)["金额"].tolist() == [21, 31]
        pd.testing.assert_frame_equal(workspace.df("原表"), original)
        recipe = compile_from_workspace(workspace, [second_name], context.library, name="重做辅助计算")
        assert [item.alias for item in recipe.inputs] == ["原表"]
        assert [step.formula for step in recipe.steps] == ["custom_sql", "compute_column"]
    finally:
        workspace.close()


def test_recipe_inputs_keep_original_source_instead_of_auxiliary_or_expected_tables():
    workspace = Workspace(None)
    try:
        frame = pd.DataFrame({"编号": [1], "金额": [10]})
        workspace.put_dataframe("人工样例", frame, source="uploaded", role="expected")
        workspace.put_dataframe("辅助分析", frame, source="computed", role="derived")
        context = AgentContext(mode="modify", ws=workspace, library=FormulaLibrary(),
                               host=SimpleNamespace(field_mapping=lambda: {}), llm=None, conversation_id="auxiliary")
        recipe = Recipe(name="修改", inputs=[{"alias": "输入", "required_columns": ["编号"],
                                               "expected_columns": ["编号", "金额"]}])
        with pytest.raises(ValueError, match="找不到流程需要的输入"):
            _resolve_inputs(context, recipe, None)
        for table in ("人工样例", "辅助分析"):
            with pytest.raises(ValueError, match="原始输入表"):
                _resolve_inputs(context, recipe, {"输入": table})
            with pytest.raises(ValueError, match="原始输入表"):
                _learn_context(context, "修改金额", {"输入": table}, [])
        workspace.put_dataframe("原表", frame, source="uploaded", role="input", file_id="source")
        assert _resolve_inputs(context, recipe, None) == {"输入": "原表"}
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
