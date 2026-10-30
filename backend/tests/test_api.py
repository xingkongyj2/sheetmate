"""接口 + Agent 全链路（用假 LLM 编排，不需要真实 API Key）。"""

from __future__ import annotations

import copy

import pytest

from sheetmate.demo.samples import demo_recipe_body
from tests.conftest import read_sse


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _new_conversation(client, mode="analyze", customer_id=None) -> str:
    r = client.post("/api/conversations", json={"mode": mode, "customer_id": customer_id})
    assert r.status_code == 200, r.text
    return r.json()["conversation"]["id"]


def _upload(client, conv_id, path, kind="input") -> dict:
    with open(path, "rb") as f:
        r = client.post(f"/api/conversations/{conv_id}/files", files={"file": (path.name, f)}, data={"kind": kind})
    assert r.status_code == 200, r.text
    return r.json()


def _chat(client, conv_id, message, **extra) -> list[dict]:
    r = client.post(f"/api/conversations/{conv_id}/chat", json={"message": message, **extra})
    assert r.status_code == 200, r.text
    return read_sse(r)


def _llm_recipe(owner_table: str = "店铺归属表", broken: str | None = None) -> dict:
    """把示例 Recipe 转成 LLM 会返回的 JSON 格式（表名按会话里的实际表名）。"""
    body = copy.deepcopy(demo_recipe_body("运费明细", owner_table))
    steps = body["steps"]
    if broken == "wrong_key":
        steps[2]["params"]["key"] = "店铺"
    if broken == "no_fill":
        steps[2]["params"].pop("missing_fill")
    return {
        "recipe_name": "月结账单整理",
        "recipe_description": body["description"],
        "steps": steps,
        "output_sheets": body["output_spec"],
        "validation_rules": body["validation_rules"],
        "key_columns": {"运费明细": ["运单号"]},
        "questions": [],
        "alignment_report": {"confidence_overall": 0.9},
        "notes": "",
    }


# ---------------------------------------------------------------------------
# 基础接口
# ---------------------------------------------------------------------------


def test_health_and_auth(client):
    assert client.get("/api/health").json()["ok"]
    me = client.get("/api/auth/me").json()["user"]
    assert me["account"] == "admin" and me["role"] == "admin"
    r = client.put("/api/auth/profile", json={"profile": {"job": "财务", "onboarded": True}})
    assert r.json()["user"]["onboarded"] is True


def test_unauthenticated_request_is_rejected(client):
    from fastapi.testclient import TestClient

    from sheetmate.app import app

    anon = TestClient(app)
    r = anon.get("/api/customers")
    assert r.status_code == 401
    assert r.json()["error"]["message"] == "请先登录"


def test_demo_customer_detail(client):
    customers = client.get("/api/customers").json()["customers"]
    demo = next(c for c in customers if c["name"].startswith("顺达物流"))
    assert demo["recipe_count"] == 1 and demo["file_count"] == 4
    detail = client.get(f"/api/customers/{demo['id']}").json()["customer"]
    recipe = detail["recipes"][0]
    assert recipe["status"] == "active"
    assert recipe["verification"]["match_rate"] == 1.0
    assert [s["formula"] for s in recipe["flow"]["steps"]] == ["pivot_wide", "compute_column", "lookup", "group_aggregate"]
    assert "整体处理逻辑" in detail["summary"]
    assert detail["field_mapping"]["运单号"] == ["单号", "运单编号"]


def test_formulas_and_settings(client):
    data = client.get("/api/formulas").json()
    assert any(f["id"] == "lookup" for f in data["builtin"])
    cfg = client.get("/api/settings/llm").json()
    assert cfg["configured"] is False and cfg["model"] == "deepseek-chat"


def test_chat_without_llm_key_explains_how_to_configure(client):
    conv = _new_conversation(client)
    events = _chat(client, conv, "你好")
    assert any(e["type"] == "error" and "API Key" in e["content"] for e in events)
    detail = client.get(f"/api/conversations/{conv}").json()["conversation"]
    assert detail["messages"][-1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# 分析模式：ReAct 工具循环
# ---------------------------------------------------------------------------


def test_analyze_mode_tool_loop(client, samples, fake_llm):
    conv = _new_conversation(client)
    uploaded = _upload(client, conv, samples["fees_sep"])
    assert uploaded["tables"][0]["name"] == "运费明细"
    llm = fake_llm(script=[
        {"tools": [("table_info", {"table": "运费明细"})]},
        {"tools": [("group_aggregate", {"table": "运费明细", "group_by": ["店铺名称"],
                                        "aggregations": [{"column": "金额", "func": "sum"}], "result_name": "店铺金额"})]},
        {"text": "各店铺金额已汇总。\n## ✅ 最终结论\n- 共 8 家店铺"},
    ])
    events = _chat(client, conv, "按店铺统计金额")
    types = [e["type"] for e in events]
    assert types.count("tool_call") == 2 and "table" in types and types[-1] == "done"
    results = [e for e in events if e["type"] == "tool_result"]
    assert all(r["ok"] for r in results), results
    table = next(e for e in events if e["type"] == "table")["table"]
    assert table["name"] == "店铺金额" and table["rows"] == 8
    # 第二轮请求里带上了工具结果（role=tool）
    assert any(m.get("role") == "tool" for m in llm.calls[1])
    detail = client.get(f"/api/conversations/{conv}").json()["conversation"]
    assert detail["title"] == "测试对话"
    assistant = detail["messages"][-1]
    assert "最终结论" in assistant["content"] and any(e["type"] == "tool_call" for e in assistant["events"])
    assert any(t["name"] == "店铺金额" and t["role"] == "derived" for t in detail["tables"])


def test_text_before_tool_call_is_process_note_not_answer(client, samples, fake_llm):
    """工具调用前模型说的话是「过程说明」：进执行过程，不能出现在回答里（短的、长的都一样）；最终回答只在最后出现一次。"""
    conv = _new_conversation(client)
    _upload(client, conv, samples["fees_sep"])
    long_lead = "我先看一下这张表的结构，确认有哪些列、每列是什么类型，再决定怎么统计各个店铺的金额，" * 3
    fake_llm(script=[
        {"text": "先看一下表。", "tools": [("table_info", {"table": "运费明细"})]},
        {"text": long_lead, "tools": [("table_info", {"table": "运费明细"})]},
        {"text": "共 8 家店铺，合计 1.2 万元。\n## ✅ 最终结论\n- 星河最多"},
    ])
    events = _chat(client, conv, "按店铺统计金额")
    answer = "".join(e["content"] for e in events if e["type"] == "text_chunk")
    # 被撤回的实时输出之后，剩下的文字只能是最终回答
    notes = [e["content"] for e in events if e["type"] == "note" and e["content"]]
    assert "先看一下表。" in notes and any("我先看一下这张表" in n for n in notes)
    last_note = max(i for i, e in enumerate(events) if e["type"] == "note")
    final_answer = "".join(e["content"] for e in events[last_note + 1 :] if e["type"] == "text_chunk")
    assert final_answer.startswith("共 8 家店铺") and "先看一下表" not in final_answer
    assert answer.endswith(final_answer)
    stored = client.get(f"/api/conversations/{conv}").json()["conversation"]["messages"][-1]
    assert stored["content"].startswith("共 8 家店铺") and "我先看一下" not in stored["content"]


def test_tool_error_is_returned_to_llm(client, samples, fake_llm):
    conv = _new_conversation(client)
    _upload(client, conv, samples["fees_sep"])
    llm = fake_llm(script=[
        {"tools": [("select_columns", {"table": "运费明细", "columns": ["店铺"]})]},
        {"text": "## ✅ 最终结论\n- 已修正"},
    ])
    events = _chat(client, conv, "只要店铺列")
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] is False and "店铺名称" in result["text"]
    tool_msg = next(m for m in llm.calls[1] if m.get("role") == "tool")
    assert "你是不是想用" in tool_msg["content"]


def test_ask_user_ends_turn(client, samples, fake_llm):
    conv = _new_conversation(client)
    _upload(client, conv, samples["fees_sep"])
    fake_llm(script=[{"tools": [("ask_user", {"question": "按哪个金额统计？", "options": ["金额", "重量"]})]}])
    events = _chat(client, conv, "统计一下")
    clarify = next(e for e in events if e["type"] == "clarify")
    assert clarify["options"] == ["金额", "重量"]


def test_multi_table_compare(client, samples, fake_llm):
    conv = _new_conversation(client)
    _upload(client, conv, samples["fees_sep"])
    _upload(client, conv, samples["owners"])
    fake_llm(script=[{"text": "表一结论"}, {"text": "表二结论"}, {"text": "[一致] 两表可以关联\n## ✅ 最终结论\n- ok"}])
    events = _chat(client, conv, "对比这两张表")
    types = [e["type"] for e in events]
    assert "agent_pool_start" in types and types.count("agent_done") == 2 and "aggregate_start" in types


def test_plan_execution(client, samples, fake_llm):
    conv = _new_conversation(client)
    _upload(client, conv, samples["fees_sep"])
    fake_llm(script=[{"text": "第一步完成"}, {"text": "第二步完成"}, {"text": "## ✅ 最终结论\n- 自检通过"}])
    steps = [{"id": 1, "description": "看结构"}, {"id": 2, "description": "统计"}]
    events = _chat(client, conv, "分析", plan_steps=steps)
    types = [e["type"] for e in events]
    assert types.count("step_start") == 2 and "reflect_done" in types


def test_save_analysis_as_recipe_then_bind_new_customer(client, samples, fake_llm):
    conv = _new_conversation(client)
    _upload(client, conv, samples["fees_sep"])
    fake_llm(script=[
        {"tools": [("filter_rows", {"table": "运费明细", "condition": "\"金额\" > 10", "result_name": "大额"})]},
        {"tools": [("group_aggregate", {"table": "大额", "group_by": ["店铺名称"],
                                        "aggregations": [{"column": "金额", "func": "sum"}], "result_name": "大额汇总"})]},
        {"tools": [("save_as_recipe", {"output_tables": ["大额汇总"], "name": "大额费用汇总"})]},
        {"text": "## ✅ 操作结果\n- 已保存流程"},
    ])
    events = _chat(client, conv, "统计大额费用并保存成流程")
    saved = next(e for e in events if e["type"] == "recipe_saved")
    assert saved["step_count"] == 2
    # 保存到新客户：文件和流程一起迁移
    r = client.post(f"/api/conversations/{conv}/bind", json={"new_customer": {"name": "测试客户甲"}})
    customer_id = r.json()["customer_id"]
    detail = client.get(f"/api/customers/{customer_id}").json()["customer"]
    assert [f["filename"] for f in detail["files"]] == ["运费明细_2026-09.xlsx"]
    assert detail["recipes"][0]["name"] == "大额费用汇总"
    # 保存到客户后，后台自动写好了整体处理逻辑（这里是假 LLM 的回复）
    assert detail["summary"] == "测试对话" and detail["summary_by"] == "ai"
    # 客户页：上传新文件并重复执行
    file_id = detail["files"][0]["file_id"]
    match = client.post(f"/api/recipes/{saved['recipe_id']}/match", json={"file_ids": [file_id]}).json()
    assert match["inputs"][0]["match"]["key"].startswith(file_id)
    run_events = read_sse(client.post(f"/api/recipes/{saved['recipe_id']}/run", json={"file_ids": [file_id]}))
    done = next(e for e in run_events if e["type"] == "recipe_run_done")
    assert done["status"] in {"success", "warning"}
    file_event = next(e for e in run_events if e["type"] == "file")
    assert client.get(file_event["download_url"]).status_code == 200


# ---------------------------------------------------------------------------
# 修改模式：生成 Recipe → 执行 → 修复 → 比对 → 保存
# ---------------------------------------------------------------------------


def _modify_conversation(client, samples) -> str:
    conv = _new_conversation(client, mode="modify")
    _upload(client, conv, samples["fees_sep"])
    _upload(client, conv, samples["owners"])
    _upload(client, conv, samples["expected_sep"], kind="expected")
    return conv


def test_modify_mode_builds_verified_recipe(client, samples, fake_llm):
    conv = _modify_conversation(client, samples)
    llm = fake_llm(
        script=[{"tools": [("build_recipe", {"requirement": "费用拆列、算合计、匹配归属（匹配不到记新食研）、按归属汇总"})]},
                {"text": "## ✅ 操作结果\n- 已生成流程"}],
        json_handlers=[("Recipe 生成器", _llm_recipe())],
    )
    events = _chat(client, conv, "按人工结果的样子整理账单，以后每月都要做")
    types = [e["type"] for e in events]
    for t in ("recipe_evidence", "recipe_generated", "recipe_step_done", "recipe_verify", "recipe_saved", "file"):
        assert t in types, t
    verify = next(e for e in events if e["type"] == "recipe_verify")["report"]
    assert verify["status"] == "pass"
    saved = next(e for e in events if e["type"] == "recipe_saved")
    assert saved["status"] == "active"
    # 学习提示词里包含 diff 证据
    assert any("长转宽" in p for p in llm.json_prompts)
    detail = client.get(f"/api/conversations/{conv}").json()["conversation"]
    assert any(t["role"] == "result" for t in detail["tables"])


def test_modify_mode_refines_until_verified(client, samples, fake_llm):
    conv = _modify_conversation(client, samples)
    fake_llm(
        script=[{"tools": [("build_recipe", {"requirement": "整理账单"})]}, {"text": "完成"}],
        json_handlers=[("比对没有通过", _llm_recipe()), ("Recipe 生成器", _llm_recipe(broken="no_fill"))],
    )
    events = _chat(client, conv, "整理账单")
    verifies = [e["report"] for e in events if e["type"] == "recipe_verify"]
    # 第一轮漏了「匹配不到填新食研」：12 行归属为空，没达到 98% 的通过线，且「归属不能为空」校验失败 → 修正后通过
    assert verifies[0]["status"] != "pass" and verifies[0]["overall_match_rate"] < 0.98
    assert verifies[-1]["status"] == "pass" and len(verifies) == 2
    assert any(e["type"] == "recipe_refine" for e in events)
    saved = next(e for e in events if e["type"] == "recipe_saved")
    assert saved["status"] == "active"


def test_modify_mode_repairs_broken_step_with_llm(client, samples, fake_llm):
    conv = _modify_conversation(client, samples)
    fixed = _llm_recipe()["steps"][2]
    fake_llm(
        script=[{"tools": [("build_recipe", {"requirement": "整理账单"})]}, {"text": "完成"}],
        json_handlers=[("步骤修复器", {"formula": "lookup", "params": fixed["params"], "explanation": "列名应为店铺名称"}),
                       ("Recipe 生成器", _llm_recipe(broken="wrong_key"))],
    )
    events = _chat(client, conv, "整理账单")
    assert any(e["type"] == "recipe_repair" and e["status"] == "retry" for e in events)
    assert next(e for e in events if e["type"] == "recipe_verify")["report"]["status"] == "pass"


def test_rerun_from_step_with_new_params(client):
    customers = client.get("/api/customers").json()["customers"]
    demo = next(c for c in customers if c["name"].startswith("顺达物流"))
    detail = client.get(f"/api/customers/{demo['id']}").json()["customer"]
    run_id = detail["runs"][-1]["run_id"]
    run = client.get(f"/api/runs/{run_id}").json()
    assert any(t["name"] == "账单明细" for t in run["tables"])  # 中间表都保留着
    page = client.get(f"/api/runs/{run_id}/tables/运单费用").json()["table"]
    assert page["total"] == 48
    params = copy.deepcopy(run["run"]["steps"][2]["params"])
    params["missing_fill"] = {"归属": "其他"}
    events = read_sse(client.post(f"/api/runs/{run_id}/rerun/3", json={"params": params}))
    done = next(e for e in events if e["type"] == "recipe_run_done")
    assert done["status"] in {"success", "warning"}
    assert [e["step_id"] for e in events if e["type"] == "recipe_step_done"] == [3, 4]
    saved = next(e for e in events if e["type"] == "recipe_saved")
    assert saved["status"] == "draft"
    after = client.get(f"/api/runs/{run_id}").json()["run"]
    assert [s["step_id"] for s in after["steps"]] == [1, 2, 3, 4]  # 前两步的明细沿用原记录
    assert after["recipe_version"] == saved["version"]  # 记录的是实际执行的版本
    assert after["steps"][2]["params"]["missing_fill"] == {"归属": "其他"}


@pytest.mark.parametrize("bad_name", ["../../etc/passwd.xlsx", "a.exe"])
def test_upload_rejects_bad_files(client, bad_name):
    conv = _new_conversation(client)
    r = client.post(f"/api/conversations/{conv}/files", files={"file": (bad_name, b"hello")}, data={"kind": "input"})
    assert r.status_code == 400


def _demo_customer_id(client) -> str:
    customers = client.get("/api/customers").json()["customers"]
    return next(c["id"] for c in customers if c["name"].startswith("顺达物流"))


def test_run_existing_recipe_in_chat_with_field_mapping(client, samples, fake_llm):
    """绑定客户的对话里，用客户已有的流程处理下个月的新文件（列名变了，靠客户字段映射统一）。"""
    customer_id = _demo_customer_id(client)
    detail = client.get(f"/api/customers/{customer_id}").json()["customer"]
    seed_recipe = next(r for r in detail["recipes"] if r["created_by"] == "human")
    conv = _new_conversation(client, mode="modify", customer_id=customer_id)
    _upload(client, conv, samples["fees_oct"])
    _upload(client, conv, samples["owners"])
    llm = fake_llm(script=[{"tools": [("run_recipe", {"recipe_id": seed_recipe["recipe_id"]})]}, {"text": "## ✅ 操作结果\n- 已处理 10 月账单"}])
    events = _chat(client, conv, "用月结账单流程处理 10 月的新文件")
    # 系统提示里列出了客户已有的流程，并标出和当前表的匹配关系
    system = llm.calls[0][0]["content"]
    assert seed_recipe["recipe_id"] in system and "与当前表匹配" in system
    inputs = [e for e in events if e["type"] == "recipe_input"]
    assert inputs[0]["renames"] == {"单号": "运单号", "店铺": "店铺名称"}
    done = next(e for e in events if e["type"] == "recipe_run_done")
    assert done["status"] in {"success", "warning"}
    assert any(e["type"] == "file" for e in events)
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] and "55 行" in result["text"]


def test_revise_recipe_creates_new_version(client, samples, fake_llm):
    conv = _modify_conversation(client, samples)
    revised = _llm_recipe()
    revised["steps"][3]["params"]["aggregations"].append({"column": "运费", "func": "sum", "as": "运费合计"})
    revised["steps"][3]["description"] = "按归属统计票数、金额合计和运费合计"
    revised["steps"][3]["explanation"] = "按用户要求：汇总里加上运费合计"
    fake_llm(
        script=[{"tools": [("build_recipe", {"requirement": "整理账单"})]}, {"text": "完成"},
                {"tools": [("revise_recipe", {"instruction": "归属汇总里再加一列运费合计"})]}, {"text": "已修改"}],
        json_handlers=[("用户要求修改已有的流程", revised), ("Recipe 生成器", _llm_recipe())],
    )
    first = _chat(client, conv, "整理账单")
    v1 = next(e for e in first if e["type"] == "recipe_saved")
    second = _chat(client, conv, "归属汇总里再加一列运费合计")
    v2 = next(e for e in second if e["type"] == "recipe_saved")
    assert v2["recipe_id"] == v1["recipe_id"] and v2["version"] == v1["version"] + 1
    step4 = next(e for e in second if e["type"] == "recipe_step_done" and e["step_id"] == 4)
    assert "运费合计" in step4["columns"]
    # 修订时和原人工结果的对比只作参考，不会触发自动修正
    verify = next(e for e in second if e["type"] == "recipe_verify")
    assert verify["informational"] is True and verify["report"]["status"] == "pass"
    assert not any(e["type"] == "recipe_refine" for e in second)
    # 用户要求新增的列必须出现在最终交付的 Excel 里（不能被人工结果的列清单过滤掉）
    file_event = next(e for e in second if e["type"] == "file")
    import io

    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(client.get(file_event["download_url"]).content))
    header = [c.value for c in wb["归属汇总"][1]]
    assert header == ["归属", "票数", "合计", "运费合计"]
    assert v2["status"] == "active"
    versions = client.get(f"/api/recipes/{v1['recipe_id']}").json()["versions"]
    assert [v["version"] for v in versions][:2] == [v2["version"], v1["version"]]


def test_revise_rule_change_is_not_reverted(client, samples, fake_llm):
    """用户有意改了业务规则（兜底值改成「其他」）：和旧样例对不上是预期的，不能被「修正」回去。"""
    conv = _modify_conversation(client, samples)
    revised = _llm_recipe()
    revised["steps"][2]["params"]["missing_fill"] = {"归属": "其他"}
    llm = fake_llm(
        script=[{"tools": [("build_recipe", {"requirement": "整理账单"})]}, {"text": "完成"},
                {"tools": [("revise_recipe", {"instruction": "匹配不到归属的改记为其他"})]}, {"text": "已修改"}],
        json_handlers=[("用户要求修改已有的流程", revised), ("Recipe 生成器", _llm_recipe())],
    )
    _chat(client, conv, "整理账单")
    events = _chat(client, conv, "匹配不到归属的改记为其他")
    verify = next(e for e in events if e["type"] == "recipe_verify")
    assert verify["informational"] is True and verify["report"]["status"] != "pass"
    assert not any(e["type"] == "recipe_refine" for e in events)
    assert not any("比对没有通过" in p for p in llm.json_prompts)
    saved = next(e for e in events if e["type"] == "recipe_saved")
    assert saved["status"] == "active"
    tables = client.get(f"/api/conversations/{conv}").json()["conversation"]["tables"]
    results = [t for t in tables if t["role"] == "result" and "账单明细" in t["name"]]
    assert len(results) == 1  # 同一个流程再次执行时覆盖上一次的结果表，不会越积越多
    detail = results[0]
    page = client.get(f"/api/conversations/{conv}/tables/{detail['name']}", params={"limit": 100}).json()["table"]
    owners = {row[-1] for row in page["rows"]}
    assert "其他" in owners and "新食研" not in owners


def test_human_edited_summary_is_not_overwritten(client, samples, fake_llm):
    fake_llm()
    customer = client.post("/api/customers", json={"name": "测试客户乙"}).json()["customer"]
    client.put(f"/api/customers/{customer['id']}", json={"summary": "### 整体处理逻辑\n我自己写的说明"})
    conv = _new_conversation(client)
    _upload(client, conv, samples["owners"])
    client.post(f"/api/conversations/{conv}/bind", json={"customer_id": customer["id"]})  # 会触发自动更新
    detail = client.get(f"/api/customers/{customer['id']}").json()["customer"]
    assert detail["summary"] == "### 整体处理逻辑\n我自己写的说明" and detail["summary_by"] == "human"
    # 手动点「重新总结」才会更新，并恢复为自动模式
    result = client.post(f"/api/customers/{customer['id']}/summary").json()
    assert result["by_ai"] is True
    assert client.get(f"/api/customers/{customer['id']}").json()["customer"]["summary_by"] == "ai"


def test_new_category_next_month_is_caught_and_revisable(client, samples, fake_llm, tmp_path):
    """下个月出现新的费用类型：执行要标记失败并说清原因（不能悄悄少算钱），文件仍可下载核对；修订后能通过。"""
    import pandas as pd

    from sheetmate.demo.samples import fee_rows

    rows = fee_rows(11, 40, seed=11)
    rows += [{**r, "费用类型": "偏远费", "金额": 12.0} for i, r in enumerate(list(rows)) if i % 9 == 0]
    nov = tmp_path / "运费明细_2026-11.xlsx"
    pd.DataFrame(rows).to_excel(nov, index=False, sheet_name="明细")
    customer_id = _demo_customer_id(client)
    detail = client.get(f"/api/customers/{customer_id}").json()["customer"]
    recipe_id = next(r["recipe_id"] for r in detail["recipes"] if r["created_by"] == "human")
    with open(nov, "rb") as f:
        file_id = client.post(f"/api/customers/{customer_id}/files", files={"file": (nov.name, f)}).json()["file"]["file_id"]
    owner_id = next(f["file_id"] for f in detail["files"] if f["filename"] == "店铺归属表.xlsx")

    events = read_sse(client.post(f"/api/recipes/{recipe_id}/run", json={"file_ids": [file_id, owner_id], "allow_repair": False}))
    done = next(e for e in events if e["type"] == "recipe_run_done")
    assert done["status"] == "failed"
    assert "偏远费" in done["summary"] and "84.00" in done["summary"] and "建议" in done["summary"]
    file_event = next(e for e in events if e["type"] == "file")  # 校验没过也能下载核对
    assert "未通过校验" in file_event["filename"]
    assert client.get(file_event["download_url"]).status_code == 200
