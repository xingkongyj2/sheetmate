from __future__ import annotations

import pytest

from sheetmate.agent.llm.client import LLMError
from tests.conftest import _chunk
from tests.test_api import _chat, _new_conversation, _upload


def test_plan_preserves_reasoning_between_steps(client, fake_llm):
    conversation = _new_conversation(client)
    llm = fake_llm(script=[{"text": "已核对输入。"}, {"text": "已整理结果。"}, {"text": "处理完成。"}])
    stream_chat = llm.stream_chat

    async def with_reasoning(*args, **kwargs):
        chunk = _chunk()
        chunk.choices[0].delta.reasoning_content = f"思考{len(llm.calls) + 1}"
        yield chunk
        async for chunk in stream_chat(*args, **kwargs):
            yield chunk

    llm.stream_chat = with_reasoning
    _chat(client, conversation, "统计运单数", plan_steps=[
        {"id": 1, "description": "核对输入"}, {"id": 2, "description": "整理结果"},
    ])
    first = next(m for m in llm.calls[1] if m.get("content") == "已核对输入。")
    second = next(m for m in llm.calls[2] if m.get("content") == "已整理结果。")
    assert first["reasoning_content"] == "思考1"
    assert second["reasoning_content"] == "思考2"


def test_plan_summaries_are_separate_from_progress_and_final_reply(client, samples, fake_llm):
    conversation = _new_conversation(client)
    _upload(client, conversation, samples["fees_sep"])
    lead = "先检查表结构和日期范围，再核对运单号是否重复，避免重复计算重量。" * 8
    next_lead = "已经了解字段，接下来抽取几行核对内容。" * 10
    summary = "- 已读取明细。\n- 确认按运单去重统计。"
    answer = "## 统计结果\n\n| 指标 | 数值 |\n| --- | ---: |\n| 运单数 | 48 |\n\n## 待确认\n\n- 核实日期。"
    llm = fake_llm(script=[
        {"text": lead, "tools": [("table_info", {"table": "运费明细"})]},
        {"text": next_lead, "tools": [("preview_table", {"table": "运费明细", "limit": 3})]},
        {"text": summary},
        {"text": "- 已整理统计结果。"},
        {"text": answer},
    ])
    steps = [{"id": 1, "description": "核对输入"}, {"id": 2, "description": "整理结果"}]
    events = _chat(client, conversation, "统计运单数", plan_steps=steps)
    assert next(event for event in events if event["type"] == "plan_start")["steps"] == steps
    notes = [event for event in events if event["type"] == "note"]
    assert [event["content"] for event in notes] == [lead, next_lead]
    assert all(event["step_num"] == 1 for event in notes)
    done = [event for event in events if event["type"] == "step_done"]
    assert done[0]["summary"] == summary
    assert done[0]["detail"] == summary
    assert all(event["status"] == "done" for event in done)
    assert "".join(event["content"] for event in events if event["type"] == "text_chunk") == answer
    stored = client.get(f"/api/conversations/{conversation}").json()["conversation"]["messages"][-1]
    assert stored["content"] == answer
    assert [event for event in stored["events"] if event["type"] == "step_done"] == done
    assert "Markdown 表格" in llm.calls[0][0]["content"]
    assert any("不输出整个任务的最终回答" in (item.get("content") or "") for item in llm.calls[0])


def test_plan_waits_for_clarification_without_executing_remaining_steps(client, fake_llm):
    conversation = _new_conversation(client)
    question = "按账单文件还是下单日期统计？"
    llm = fake_llm(script=[{"tools": [("ask_user", {"question": question, "options": ["按账单文件", "按下单日期"]})]}])
    events = _chat(client, conversation, "统计本月重量", plan_steps=[
        {"id": 1, "description": "确认统计口径"}, {"id": 2, "description": "统计总重量"},
    ])
    assert len(llm.calls) == 1
    done = next(event for event in events if event["type"] == "step_done")
    assert done["status"] == "waiting" and done["summary"] == question
    assert done["detail"] == question
    assert sum(event["type"] == "step_start" for event in events) == 1
    assert not any(event["type"] == "reflect_start" for event in events)


def test_plan_stream_error_is_recorded_as_failed_step(client, fake_llm):
    conversation = _new_conversation(client)
    llm = fake_llm()

    async def failing(*args, **kwargs):
        yield _chunk(content="正在核对")
        raise LLMError("模拟连接中断")

    llm.stream_chat = failing
    events = _chat(client, conversation, "统计重量", plan_steps=[{"id": 1, "description": "统计"}])
    done = next(event for event in events if event["type"] == "step_done")
    assert done["status"] == "error" and done["summary"] == "模拟连接中断"
    assert "模拟连接中断" in done["detail"]
    stored = client.get(f"/api/conversations/{conversation}").json()["conversation"]["messages"][-1]
    assert done in stored["events"]


@pytest.mark.parametrize("recovered", [False, True])
def test_plan_does_not_hide_unresolved_tool_errors(client, samples, fake_llm, recovered):
    conversation = _new_conversation(client)
    _upload(client, conversation, samples["fees_sep"])
    script = [{"tools": [("table_info", {"table": "不存在的表"})]}]
    if recovered:
        script.append({"tools": [("table_info", {"table": "运费明细"})]})
    script.extend([{"text": "- 已核对执行情况。"}, {"text": "## 结果\n\n见执行情况。"}])
    fake_llm(script=script)
    events = _chat(client, conversation, "查看结构", plan_steps=[{"id": 1, "description": "查看结构"}])
    done = next(event for event in events if event["type"] == "step_done")
    assert done["status"] == ("done" if recovered else "warning")


@pytest.mark.parametrize("thinking", [False, True])
def test_short_step_titles_keep_full_instructions_and_result_details(client, fake_llm, thinking):
    conversation = _new_conversation(client)
    description = "核对三人两周原表的所有日期与排班，逐格检查姓名、候选人数和休息日；保留原有备注并单独说明空白格的影响。"
    check = "全部日期与三人的记录一一对应，候选人数与原表相同，并保留全部备注。"
    title = "核对三人两周原表"
    report = ("已逐格核对三人的原始排班与候选记录，原有备注均已保留。\n\n"
              "处理口径：按原表日期与姓名逐一对应，空白格保留为空，不自动推断为休息。\n\n"
              "- 姓名与日期对应一致。\n- 未确认的空白格已单独列出，等待补充业务口径。")
    llm = fake_llm(script=[{"text": report}, {"text": "核对结果已整理。"}])
    events = _chat(client, conversation, "按计划核对原表", thinking=thinking,
                   plan_steps=[{"id": 1, "title": title, "description": description, "check": check}])
    plan = next(event for event in events if event["type"] == "plan_start")
    started = next(event for event in events if event["type"] == "step_start")
    assert plan["steps"] == [{"id": 1, "description": title}]
    assert started["description"] == title
    instruction = "\n".join(item.get("content") or "" for item in llm.calls[0] if item["role"] == "user")
    assert description in instruction and check in instruction
    done = next(event for event in events if event["type"] == "step_done")
    assert done["detail"] == report
    if thinking:
        assert len(done["summary"]) <= 30
    stored = client.get(f"/api/conversations/{conversation}").json()["conversation"]["messages"][-1]
    assert next(event for event in stored["events"] if event["type"] == "step_done")["detail"] == report
    assert len(llm.calls) == 2


def test_step_title_fallback_is_local_and_does_not_modify_business_requirements():
    from sheetmate.agent.framework.planning.planner import plan_from_steps

    description = "核对原表日期与姓名，逐格核对三个人连续两周的全部排班记录，并完整保留备注。"
    plan = plan_from_steps([{"description": description}])
    assert plan.display_description(1) == "核对原表日期与姓名"
    assert plan.steps[0]["description"] == description

    long_title = "核对所有人员两周内全部日期姓名候选人数和原有备注的逐格对应情况"
    plan = plan_from_steps([{"title": long_title, "description": description}])
    assert len(plan.display_description(1)) <= 20
    assert "…" not in plan.display_description(1)
    assert plan.steps[0]["description"] == description
