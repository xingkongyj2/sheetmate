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
