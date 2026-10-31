from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import pandas as pd
import pytest

from sheetmate.agent.executor import AgentExecutor
from sheetmate.agent.tools import AgentContext, Tool, ToolResult, execute_tool, table_payload
from sheetmate.engine.formulas import FormulaLibrary
from sheetmate.engine.workspace import Workspace
from sheetmate.services.analysis import analysis_workspace
from sheetmate.services import chat as chat_service
from sheetmate.services import conversations as conv_service
from tests.test_api import _chat, _new_conversation, _upload


def test_intent_is_inferred_each_turn_and_client_cannot_override(client, fake_llm):
    conversation = _new_conversation(client)
    llm = fake_llm(
        script=[{"text": "请上传要修改的原表。"}, {"text": "可以继续统计。"}],
        json_handlers=[("处理意图判断助手", [{"mode": "modify"}, {"mode": "analyze"}])],
    )
    first = _chat(client, conversation, "把原表加一列含税金额", mode="analyze")
    second = _chat(client, conversation, "先只算一下总额", mode="modify")
    assert next(event for event in first if event["type"] == "start")["mode"] == "modify"
    assert next(event for event in second if event["type"] == "start")["mode"] == "analyze"
    detail = client.get(f"/api/conversations/{conversation}").json()["conversation"]
    assert detail["mode"] == "analyze"
    assert [message["meta"]["mode"] for message in detail["messages"] if message["role"] == "user"] == ["modify", "analyze"]
    prompts = [prompt for prompt in llm.json_prompts if "处理意图判断助手" in prompt]
    assert len(prompts) == 2 and "把原表加一列含税金额" in prompts[1]
    assert "请上传要修改的原表" in prompts[1]


def test_invalid_intent_does_not_execute_tools(client, fake_llm):
    conversation = _new_conversation(client)
    llm = fake_llm(json_handlers=[("处理意图判断助手", {"mode": "unknown"})])
    events = _chat(client, conversation, "修改金额")
    assert any(event["type"] == "error" and "本次尚未处理文件" in event["content"] for event in events)
    assert not llm.calls
    assert events[-1]["type"] == "done"


def test_create_intent_without_upload_uses_creation_context_and_can_change_next_turn(client, fake_llm):
    conversation = _new_conversation(client)
    request = "按这段口述转写新建费用申请表：标题合并居中，姓名、部门、用途、金额四列，真实数据先留空"
    llm = fake_llm(
        script=[{"text": "表格结构已确定：保留四列和空白填写区。"}, {"text": "可以解释这些字段的用途。"}],
        json_handlers=[("处理意图判断助手", [{"mode": "create"}, {"mode": "analyze"}])],
    )

    creation = _chat(client, conversation, request, mode="modify")
    assert next(event for event in creation if event["type"] == "start")["mode"] == "create"
    assert not any(event["type"] == "error" for event in creation)
    detail = client.get(f"/api/conversations/{conversation}").json()["conversation"]
    assert detail["mode"] == "create" and detail["messages"][0]["meta"]["mode"] == "create"
    system = llm.calls[0][0]["content"]
    assert "当前模式：新建" in system and "create_workbook" in system
    assert "可直接根据用户文字或资料新建工作簿" in system
    assert "请用户先上传" not in system
    assert "未提供的真实业务数据留空" in system
    assert "成功返回文件后" in system

    explanation = _chat(client, conversation, "先解释一下这些字段都有什么用途")
    assert next(event for event in explanation if event["type"] == "start")["mode"] == "analyze"
    prompts = [prompt for prompt in llm.json_prompts if "处理意图判断助手" in prompt]
    assert request in prompts[1] and "本轮尚未生成可下载的 Excel 工作簿" in prompts[1]


def _visible_answer(events):
    parts = []
    for event in events:
        if event["type"] == "note":
            parts.clear()
        elif event["type"] == "text_chunk":
            parts.append(event["content"])
    return "".join(parts).strip()


def test_creation_cannot_report_success_without_a_delivered_file(client, fake_llm):
    conversation = _new_conversation(client)
    claimed = "表格已建好，可以下载费用申请.xlsx。"
    fake_llm(intent_mode="create", script=[{"text": claimed}])
    events = _chat(client, conversation, "建立费用申请表，标题合并居中")
    assert not any(event["type"] == "file" for event in events)
    answer = _visible_answer(events)
    assert "本轮尚未生成可下载的 Excel 工作簿" in answer and "已建好" not in answer
    assert any(event["type"] == "note" and event["content"] == claimed for event in events)
    stored = client.get(f"/api/conversations/{conversation}").json()["conversation"]["messages"][-1]
    assert stored["content"] == answer


@pytest.mark.parametrize("recovered", [False, True])
def test_creation_delivery_guard_uses_file_result_after_failed_attempt(client, fake_llm, recovered):
    conversation = _new_conversation(client)
    broken = {"filename": "费用申请", "sheets": [{"name": "费用", "rows": [["费用申请"]],
                                                 "merges": ["A1:C1", "B1:D1"]}]}
    script = [{"tools": [("create_workbook", broken)]}]
    if recovered:
        script.append({"tools": [("create_workbook", {"filename": "费用申请", "sheets": [
            {"name": "费用", "rows": [["费用申请"], ["项目", "金额"]], "merges": ["A1:B1"]},
        ]})]})
    script.append({"text": "费用申请表已生成，可以下载。"})
    fake_llm(intent_mode="create", script=script)
    events = _chat(client, conversation, "新建费用申请表")
    results = [event for event in events if event["type"] == "tool_result"]
    assert results[0]["ok"] is False
    answer = _visible_answer(events)
    if recovered:
        assert results[-1]["ok"] and any(event["type"] == "file" for event in events)
        assert answer == "费用申请表已生成，可以下载。"
    else:
        assert "本轮尚未生成可下载的 Excel 工作簿" in answer
        assert "重叠" in answer and "已生成，可以下载" not in answer
    stored = client.get(f"/api/conversations/{conversation}").json()["conversation"]["messages"][-1]
    assert stored["content"] == answer


def test_creation_delivery_guard_runs_after_complete_plan_and_keeps_delivered_file(client, fake_llm):
    conversation = _new_conversation(client)
    fake_llm(intent_mode="create", script=[
        {"text": "采用姓名、部门两个字段，数据留空。"},
        {"tools": [("create_workbook", {"filename": "登记表", "sheets": [
            {"name": "登记", "rows": [["姓名", "部门"]]},
        ]})]},
        {"text": "登记表文件已生成。"},
        {"text": "已生成登记表，包含姓名和部门两个字段。"},
    ])
    events = _chat(client, conversation, "按计划新建登记表", plan_steps=[
        {"id": 1, "description": "设计字段结构"}, {"id": 2, "description": "实际生成文件"},
    ])
    assert any(event["type"] == "file" for event in events)
    assert [event["status"] for event in events if event["type"] == "step_done"] == ["done", "done"]
    answer = _visible_answer(events)
    assert "已生成登记表" in answer and "尚未生成" not in answer


def test_creation_delivery_guard_does_not_replace_a_clarification(client, fake_llm):
    conversation = _new_conversation(client)
    fake_llm(intent_mode="create", script=[{"tools": [("ask_user", {
        "question": "要登记个人还是公司的费用？", "options": ["个人费用", "公司费用"],
    })]}])
    events = _chat(client, conversation, "新建一张费用登记表")
    assert any(event["type"] == "clarify" for event in events)
    assert "尚未生成" not in _visible_answer(events)


@pytest.mark.parametrize("stopped", [False, True])
def test_creation_delivery_guard_reports_no_file_after_error_or_stop(client, fake_llm, monkeypatch, stopped):
    conversation = _new_conversation(client)
    fake_llm(intent_mode="create")

    async def interrupted(self, ctx, message, history):
        yield {"type": "text_chunk", "content": "费用表已建好。"}
        if stopped:
            raise asyncio.CancelledError()
        raise RuntimeError("模拟建表中断")

    monkeypatch.setattr(AgentExecutor, "execute", interrupted)
    events = _chat(client, conversation, "帮我新建费用表")
    answer = _visible_answer(events)
    assert "本轮尚未生成可下载的 Excel 工作簿" in answer and "已建好" not in answer
    assert ("本次处理已停止" if stopped else "模拟建表中断") in answer
    stored = client.get(f"/api/conversations/{conversation}").json()["conversation"]["messages"][-1]
    assert stored["content"] == answer


def test_stopping_during_intent_inference_records_stop(client, fake_llm, monkeypatch):
    conversation = _new_conversation(client)
    fake_llm()

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(chat_service, "infer_mode", cancelled)
    events = _chat(client, conversation, "统计总额")
    assert not any(event["type"] == "start" for event in events)
    messages = client.get(f"/api/conversations/{conversation}").json()["conversation"]["messages"]
    assert messages[-1]["role"] == "assistant" and "尚未处理文件" in messages[-1]["content"]


def test_legacy_mode_on_create_and_update_cannot_change_execution_mode(client):
    conversation = client.post("/api/conversations", json={"mode": "modify"}).json()["conversation"]
    assert conversation["mode"] == "analyze"
    updated = client.put(f"/api/conversations/{conversation['id']}", json={"mode": "modify"}).json()["conversation"]
    assert updated["mode"] == "analyze"


def test_analysis_copies_original_and_only_publishes_visible_results(client, samples, fake_llm, monkeypatch):
    conversation = _new_conversation(client)
    uploaded = _upload(client, conversation, samples["fees_sep"])
    original_bytes = samples["fees_sep"].read_bytes()
    source_name = uploaded["tables"][0]["name"]
    source = conv_service.workspace(conversation)
    original_frame = source.df(source_name)
    copied_paths: list[Path] = []

    def process_copy(args, ctx):
        assert ctx.ws is not source
        assert ctx.ws.path is not None
        path = ctx.host.file_path(uploaded["file"]["file_id"])
        copied_paths.extend([path, ctx.ws.path])
        assert path.read_bytes() == original_bytes
        path.write_bytes(b"temporary analysis data")
        ctx.ws.put_dataframe(source_name, pd.DataFrame({"temporary": [1]}))
        ctx.ws.put_dataframe("未交付的中间表", pd.DataFrame({"number": [1]}))
        ctx.ws.put_dataframe("统计结果", pd.DataFrame({"合计": [42]}), role="derived")
        return ToolResult("统计完成", tables=[table_payload(ctx.ws, "统计结果")])

    monkeypatch.setitem(chat_service._registry._static, "process_copy", Tool(
        "process_copy", "处理临时副本", {"type": "object"}, frozenset({"analyze"}), run=process_copy,
    ))
    fake_llm(script=[{"tools": [("process_copy", {})]},
                     {"tools": [("export_excel", {"sheets": [{"table": "统计结果"}], "filename": "统计结果"})]},
                     {"text": "合计 42。"}])
    events = _chat(client, conversation, "统计合计并导出")
    assert all(event["ok"] for event in events if event["type"] == "tool_result")
    assert copied_paths and all(not path.exists() for path in copied_paths)
    assert all(not path.parent.exists() for path in copied_paths)
    pd.testing.assert_frame_equal(source.df(source_name), original_frame)
    assert not source.exists("未交付的中间表")
    page = client.get(f"/api/conversations/{conversation}/tables/统计结果").json()["table"]
    assert page["rows"] == [[42]]
    assert client.get(uploaded["file"]["download_url"]).content == original_bytes
    output = next(event for event in events if event["type"] == "file")
    assert client.get(output["download_url"]).status_code == 200


@pytest.mark.parametrize("stopped", [False, True])
def test_analysis_cleans_temporary_workspace_on_error_or_stop(client, samples, fake_llm, monkeypatch, stopped):
    conversation = _new_conversation(client)
    _upload(client, conversation, samples["fees_sep"])
    roots: list[Path] = []

    async def interrupted(self, ctx, message, history):
        roots.append(ctx.ws.path.parent)
        assert list((ctx.ws.path.parent / "files").iterdir())
        yield {"type": "note", "content": "处理临时副本"}
        if stopped:
            raise asyncio.CancelledError()
        raise RuntimeError("模拟计算失败")

    monkeypatch.setattr(AgentExecutor, "execute", interrupted)
    fake_llm()
    events = _chat(client, conversation, "统计总额")
    assert any(event["type"] == "error" for event in events)
    assert roots and all(not root.exists() for root in roots)
    messages = client.get(f"/api/conversations/{conversation}").json()["conversation"]["messages"]
    assert messages[-1]["role"] == "assistant"


def test_analysis_stop_waits_for_sync_tool_before_removing_temporary_files():
    started = threading.Event()
    release = threading.Event()
    completed = threading.Event()
    source = Workspace(None)
    roots: list[Path] = []

    def slow_tool(args, ctx):
        started.set()
        assert release.wait(5)
        assert ctx.ws.path.exists()
        ctx.ws.put_dataframe("临时表", pd.DataFrame({"number": [1]}))
        completed.set()
        return ToolResult("完成")

    async def run():
        with analysis_workspace(source) as analysis:
            roots.append(analysis.root)
            ctx = AgentContext(mode="analyze", ws=analysis.ws, library=FormulaLibrary(), host=None, llm=None,
                               conversation_id="temporary-test")
            tool = Tool("slow", "临时计算", {"type": "object"}, frozenset({"analyze"}), run=slow_tool)

            async def consume():
                return [result async for result in execute_tool(tool, {}, ctx)]

            task = asyncio.create_task(consume())
            try:
                assert await asyncio.to_thread(started.wait, 5)
                task.cancel()
                await asyncio.sleep(0)
                assert not task.done() and analysis.root.exists()
                release.set()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert completed.is_set()
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)

    try:
        asyncio.run(run())
        assert roots and all(not root.exists() for root in roots)
    finally:
        source.close()
