"""客户公式：定义检查 / 版本 / 启停 / 试运行，以及客户访问规则（管理员看全部，其他人只看自己建的或被授权的）。

用的是 conftest 里已经登录成管理员的 client；普通用户用 _register_member 自助注册，各自有自己的 Cookie。
"""

from __future__ import annotations

import copy
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from sheetmate.db.database import session_scope
from sheetmate.db.models import UserCustomer
from sheetmate.engine.formulas import Formula, FormulaLibrary
from sheetmate.engine.formulas.base import quoted_placeholders
from sheetmate.engine.reader import ReadError, read_pasted_table
from sheetmate.recipe.schema import OutputSheet, Recipe, RecipeInput, RecipeStep, check_recipe
from sheetmate.services import formulas as formula_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services.errors import Forbidden

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _suffix() -> str:
    return uuid.uuid4().hex[:6]


def _anonymous() -> TestClient:
    from sheetmate.app import app

    return TestClient(app)


def _register_member() -> tuple[TestClient, int]:
    """自助注册一个普通用户（注册即登录）。返回 (带登录状态的客户端, 用户 id)。"""
    c = _anonymous()
    r = c.post("/api/auth/register", json={"account": f"m_{_suffix()}", "password": "pw123456"})
    assert r.status_code == 200, r.text
    user = r.json()["user"]
    assert user["role"] == "member"
    return c, user["id"]


def _new_customer(client, name: str | None = None) -> dict:
    r = client.post("/api/customers", json={"name": name or f"测试客户_{_suffix()}"})
    assert r.status_code == 200, r.text
    return r.json()["customer"]


def _upload(client, customer_id: str, path, kind: str = "input") -> dict:
    with open(path, "rb") as f:
        r = client.post(f"/api/customers/{customer_id}/files", files={"file": (path.name, f)}, data={"kind": kind})
    assert r.status_code == 200, r.text
    return r.json()["file"]


def _customer_ids(c: TestClient) -> list[str]:
    return [x["id"] for x in c.get("/api/customers").json()["customers"]]


# ---------------------------------------------------------------------------
# 客户公式：定义检查
# ---------------------------------------------------------------------------

GOOD = {
    "id": "big_fee",
    "name": "大额费用",
    "intent": "筛出金额大于阈值的行",
    "params": {
        "table": {"type": "table", "required": True, "desc": "费用明细表"},
        "amount_col": {"type": "column", "of": "table", "required": True, "desc": "金额列"},
        "threshold": {"type": "number", "default": 10, "desc": "金额阈值"},
    },
    "sql": "SELECT * FROM {{table}} WHERE {{amount_col}} > {{threshold}} ORDER BY {{amount_col}} DESC",
}


def _variant(**changes) -> dict:
    body = copy.deepcopy(GOOD)
    body.update(changes)
    return body


BAD_FORMULAS = [
    ("id 有大写", _variant(id="BigFee"), "公式 id 只能用小写字母"),
    ("没有名称", _variant(name=" "), "公式名称不能为空"),
    ("没有 SQL", _variant(sql=" "), "必须填写 sql 模板"),
    ("占位符没声明", _variant(sql="SELECT {{nope}} FROM {{table}}"), "未声明的参数"),
    ("占位符写在引号里", _variant(sql="SELECT * FROM {{table}} WHERE 店铺 LIKE '%{{threshold}}%'"), "写在了引号里面"),
    ("占位符写法不对", _variant(sql="SELECT * FROM {{ta ble}}"), "写法不对的占位符"),
    ("SQL 语法错误", _variant(sql="SELECT * FROM {{table}} WHERE"), "模板检查没通过"),
    ("不是 SELECT", _variant(sql="DELETE FROM {{table}}"), "模板检查没通过"),
    ("参数类型不存在", _variant(params={"x": {"type": "float"}}, sql="SELECT 1"), "不支持"),
    ("默认值类型不对", _variant(params={"n": {"type": "int", "default": "abc"}}, sql="SELECT {{n}}"), "默认值类型不对"),
    ("选项没有可选值", _variant(params={"e": {"type": "enum"}}, sql="SELECT {{e}}"), "可选值"),
    ("选项默认值不在可选值里", _variant(params={"e": {"type": "enum", "values": ["a", "b"], "default": "c"}}, sql="SELECT {{e}}"), "默认值只能是"),
    ("列不属于表参数", _variant(params={"s": {"type": "str"}, "c": {"type": "column", "of": "s"}}, sql="SELECT {{c}}"), "所属的表"),
    ("对象参数放进模板", _variant(params={"d": {"type": "dict"}}, sql="SELECT {{d}}"), "对象类型"),
    ("参数名不合法", _variant(params={"1a": {"type": "str"}}, sql="SELECT 1"), "不合法"),
    ("保留的参数名", _variant(params={"result_name": {"type": "str"}}, sql="SELECT 1"), "系统保留"),
    ("和系统工具重名", _variant(id="run_sql"), "系统工具重名"),
]


@pytest.fixture(scope="module")
def scratch_customer(client) -> str:
    return _new_customer(client)["id"]


@pytest.mark.parametrize("label,body,needle", BAD_FORMULAS, ids=[b[0] for b in BAD_FORMULAS])
def test_bad_formula_is_rejected_with_a_readable_reason(client, scratch_customer, label, body, needle):
    saved = client.post(f"/api/customers/{scratch_customer}/formulas", json=body)
    assert saved.status_code == 400, saved.text
    assert needle in saved.json()["error"]["message"], saved.text
    # 表单里边写边检查，和保存给出同样的结论；而且什么都没有保存
    checked = client.post(f"/api/customers/{scratch_customer}/formulas/check", json=body).json()
    assert len(checked["problems"]) == 1 and needle in checked["problems"][0]
    assert client.get(f"/api/customers/{scratch_customer}/formulas").json()["formulas"] == []


def test_all_param_types_pass_the_check(client, scratch_customer):
    """每种参数类型代入示例值后都应该是合法的 SELECT（不会误伤正常模板）。"""
    types = ["str", "int", "number", "bool", "list", "enum", "expr", "sql", "any", "tables", "columns", "table", "column"]
    params = {t: {"type": t, **({"values": ["a", "b"]} if t == "enum" else {})} for t in types}
    sql = ("SELECT {{str}}, {{int}}, {{number}}, {{bool}}, {{enum}}, {{any}}, {{columns}} FROM {{table}} "
           "WHERE {{expr}} AND {{column}} IN ({{list}}) AND x IN (SELECT 1 FROM {{sql}} t)")
    body = {"id": "all_types", "name": "全类型", "params": params, "sql": sql}
    assert client.post(f"/api/customers/{scratch_customer}/formulas/check", json=body).json()["problems"] == []


def test_quoted_placeholders_ignores_comments_and_unbalanced_quotes():
    assert quoted_placeholders("SELECT '{{a}}', \"{{b}}\", {{c}}") == ["a", "b"]
    assert quoted_placeholders("SELECT {{c}} -- '{{d}}'\n/* \" {{e}} */ FROM t WHERE x = ''''") == []
    assert quoted_placeholders("SELECT 'it''s' || {{x}}") == []
    assert quoted_placeholders("SELECT 'abc {{a}}") == []  # 引号没配对：交给语法检查去报错


def test_formula_library_uses_the_latest_version():
    def make(version: int, status: str) -> Formula:
        return Formula(id="f1", name="公式", version=version, status=status, type="sql", sql="SELECT 1")

    # 最新版本被停用：整个公式不可用，不会退回旧版本继续生效（和列表的顺序无关）
    assert not FormulaLibrary([make(1, "active"), make(2, "deprecated")]).has("f1")
    assert not FormulaLibrary([make(2, "deprecated"), make(1, "active")]).has("f1")
    assert FormulaLibrary([make(1, "active"), make(2, "deprecated"), make(3, "active")]).resolve("f1").spec.version == 3


def test_read_pasted_table():
    sheet = read_pasted_table("\n\n运单号\t店铺\t金额\nSF001\tA\t10\nSF002\tB\t20.5\n")
    assert list(sheet.df.columns) == ["运单号", "店铺", "金额"] and len(sheet.df) == 2
    assert str(sheet.df["金额"].dtype) == "float64"
    assert read_pasted_table("编号,名称\n001,甲\n002,乙").df["编号"].tolist() == ["001", "002"]  # 以 0 开头的编号保持文本
    for empty in ("", "  \n \n"):
        with pytest.raises(ReadError):
            read_pasted_table(empty)


# ---------------------------------------------------------------------------
# 客户公式：保存 / 版本 / 启停 / 删除
# ---------------------------------------------------------------------------


def test_formula_versions_status_and_library(client):
    cid = _new_customer(client)["id"]
    url = f"/api/customers/{cid}/formulas"
    first = client.post(url, json=GOOD)
    assert first.status_code == 200, first.text
    saved = first.json()["formula"]
    assert (saved["id"], saved["version"], saved["status"], saved["used_by"]) == ("big_fee", 1, "active", [])
    assert saved["params"]["amount_col"]["of"] == "table" and saved["params"]["threshold"]["default"] == 10

    # 每保存一次升一个版本；列表和客户详情里每个公式只有最新版本
    assert client.post(url, json=_variant(intent="筛出金额超过阈值的行")).json()["formula"]["version"] == 2
    listed = client.get(url).json()["formulas"]
    assert [(f["id"], f["version"], f["intent"]) for f in listed] == [("big_fee", 2, "筛出金额超过阈值的行")]
    assert [f["id"] for f in client.get(f"/api/customers/{cid}").json()["customer"]["formulas"]] == ["big_fee"]

    # 停用：公式库里不再有，也不会退回旧版本；再启用就恢复
    r = client.put(f"{url}/big_fee/status", json={"status": "deprecated"})
    assert r.status_code == 200 and r.json()["formula"]["status"] == "deprecated"
    assert client.get("/api/formulas", params={"customer_id": cid}).json()["customer"] == []
    with session_scope() as db:
        assert not recipe_service.library_for(db, cid).has("big_fee")
    assert client.put(f"{url}/big_fee/status", json={"status": "active"}).json()["formula"]["status"] == "active"
    with session_scope() as db:
        assert recipe_service.library_for(db, cid).resolve("big_fee").spec.version == 2
    assert client.put(f"{url}/big_fee/status", json={"status": "draft"}).status_code == 400
    assert client.put(f"{url}/nope/status", json={"status": "active"}).status_code == 404

    assert client.delete(f"{url}/big_fee").status_code == 200
    assert client.get(url).json()["formulas"] == []
    assert client.delete(f"{url}/big_fee").status_code == 404


def test_formula_with_builtin_id_is_flagged_as_override(client):
    cid = _new_customer(client)["id"]
    body = _variant(id="top_n")
    saved = client.post(f"/api/customers/{cid}/formulas", json=body).json()["formula"]
    assert saved["overrides_builtin"] is True and saved["overrides_name"]
    assert client.post(f"/api/customers/{cid}/formulas", json=GOOD).json()["formula"]["overrides_builtin"] is False
    catalog = client.get("/api/formulas", params={"customer_id": cid}).json()
    assert "run_sql" in catalog["reserved_ids"] and "top_n" not in catalog["reserved_ids"]


def test_formula_in_use_cannot_be_disabled(client):
    cid = _new_customer(client)["id"]
    url = f"/api/customers/{cid}/formulas"
    client.post(url, json=GOOD)
    recipe = Recipe(
        name="大额费用流程", customer_id=cid, created_by="human",
        inputs=[RecipeInput(alias="明细")],
        steps=[RecipeStep(id=1, name="筛大额", formula="big_fee", formula_kind="customer", output="大额",
                          params={"table": "明细", "amount_col": "金额", "threshold": 20})],
        output_spec=[OutputSheet(name="大额", source="大额")],
    )
    with session_scope() as db:
        recipe_service.save_recipe(db, recipe)
    used = client.get(url).json()["formulas"][0]["used_by"]
    assert [(u["name"], [s["id"] for s in u["steps"]], u["problems"]) for u in used] == [("大额费用流程", [1], [])]

    # 停用规则不变；删除由单独的一次确认入口决定。
    denied = client.put(f"{url}/big_fee/status", json={"status": "deprecated"})
    assert denied.status_code == 409 and "大额费用流程" in denied.json()["error"]["message"]

    # 新版本多了一个必填参数：保存会成功，但会指出「流程的第 1 步会出问题」
    v2 = _variant(params={**GOOD["params"], "region": {"type": "str", "required": True, "desc": "区域"}})
    problems = client.post(url, json=v2).json()["formula"]["used_by"][0]["problems"]
    assert len(problems) == 1 and "第 1 步" in problems[0] and "region" in problems[0]

    # 先删除引用流程，再停用公式。
    assert client.delete(f"/api/recipes/{recipe.id}").status_code == 200
    assert client.put(f"{url}/big_fee/status", json={"status": "deprecated"}).status_code == 200
    assert client.delete(f"{url}/big_fee").status_code == 200


def test_delete_formula_in_use_clears_all_versions_and_keeps_recipe_and_other_customers(client):
    cid = _new_customer(client)["id"]
    other_cid = _new_customer(client)["id"]
    url = f"/api/customers/{cid}/formulas"
    for customer_id in (cid, other_cid):
        assert client.post(f"/api/customers/{customer_id}/formulas", json=GOOD).status_code == 200
    assert client.post(url, json=_variant(intent="大额费用第二版")).json()["formula"]["version"] == 2
    recipe = Recipe(
        name="仍要保留的大额费用流程", customer_id=cid,
        inputs=[RecipeInput(alias="明细")],
        steps=[RecipeStep(id=1, name="筛大额", formula="big_fee", formula_kind="customer", output="大额",
                          params={"table": "明细", "amount_col": "金额", "threshold": 20})],
        output_spec=[OutputSheet(name="大额", source="大额")],
    )
    with session_scope() as db:
        recipe_service.save_recipe(db, recipe)
    before = client.get(f"/api/recipes/{recipe.id}").json()["recipe"]
    used = client.get(url).json()["formulas"][0]["used_by"]
    assert [item["name"] for item in used] == [recipe.name]

    deleted = client.delete(f"{url}/big_fee")
    assert deleted.status_code == 200, deleted.text
    assert client.get(url).json()["formulas"] == []
    assert client.get(f"/api/recipes/{recipe.id}").json()["recipe"] == before
    with session_scope() as db:
        assert recipe_service.latest_formula_records(db, cid) == []
        assert not recipe_service.library_for(db, cid).has("big_fee")
        assert recipe_service.library_for(db, other_cid).resolve("big_fee").spec.version == 1
        problems = check_recipe(recipe_service.get_recipe(db, recipe.id), recipe_service.library_for(db, cid))
        assert any("公式「big_fee」不存在" in problem for problem in problems)


def test_delete_formula_override_restores_builtin_without_changing_recipe(client):
    cid = _new_customer(client)["id"]
    url = f"/api/customers/{cid}/formulas"
    assert client.post(url, json=_variant(id="top_n")).json()["formula"]["overrides_builtin"] is True
    recipe = Recipe(name="使用客户覆盖公式", customer_id=cid,
                    inputs=[RecipeInput(alias="明细")],
                    steps=[RecipeStep(id=1, name="客户筛选", formula="top_n", formula_kind="customer", output="筛选",
                                      params={"table": "明细", "amount_col": "金额", "threshold": 20})])
    with session_scope() as db:
        recipe_service.save_recipe(db, recipe)
        assert recipe_service.library_for(db, cid).resolve("top_n").kind == "customer"
    assert client.delete(f"{url}/top_n").status_code == 200
    with session_scope() as db:
        assert recipe_service.library_for(db, cid).resolve("top_n").kind == "builtin"
        assert recipe_service.get_recipe(db, recipe.id).steps == recipe.steps


def test_delete_formula_keeps_customer_access_checks(client):
    cid = _new_customer(client)["id"]
    url = f"/api/customers/{cid}/formulas"
    assert client.post(url, json=GOOD).status_code == 200
    member, _ = _register_member()
    denied = member.delete(f"{url}/big_fee")
    assert denied.status_code == 403
    assert [item["id"] for item in client.get(url).json()["formulas"]] == ["big_fee"]


# ---------------------------------------------------------------------------
# 客户公式：试运行
# ---------------------------------------------------------------------------


def _trial(client, cid: str, formula: dict, params: dict, inputs: list[dict]) -> dict:
    r = client.post(f"/api/customers/{cid}/formulas/trial", json={"formula": formula, "params": params, "inputs": inputs})
    assert r.status_code == 200, r.text
    return r.json()["result"]


def test_trial_with_customer_file(client, samples):
    cid = _new_customer(client)["id"]
    file = _upload(client, cid, samples["fees_sep"])
    result = _trial(client, cid, GOOD, {"table": "运费明细", "amount_col": "金额", "threshold": 20},
                    [{"table": "运费明细", "file_id": file["file_id"], "sheet": "明细"}])
    assert result["status"] == "success", result
    names = [c["name"] for c in result["columns"]]
    assert names == ["运单号", "下单日期", "店铺名称", "费用类型", "金额", "重量(kg)"]
    amounts = [row[names.index("金额")] for row in result["preview"]["rows"]]
    assert amounts and all(a > 20 for a in amounts) and amounts == sorted(amounts, reverse=True)
    assert 0 < result["rows"] < result["inputs"][0]["rows"]
    assert 'WHERE "金额" > 20.0' in result["sql"] and result["inputs"][0]["filename"] == samples["fees_sep"].name
    # 试运行用的是临时工作区：客户那边什么都没多
    assert len(client.get(f"/api/customers/{cid}").json()["customer"]["files"]) == 1
    # 没填的参数用声明里的默认值，表里的数据也可以不选 sheet（取第一个）
    again = _trial(client, cid, GOOD, {"table": "运费明细", "amount_col": "金额"}, [{"table": "运费明细", "file_id": file["file_id"]}])
    assert again["status"] == "success" and again["params"]["threshold"] == 10.0 and again["rows"] > result["rows"]


def test_trial_applies_customer_field_mapping(client, samples):
    """试运行和真正执行流程一样，先按客户的字段映射把别名列统一成标准名。"""
    cid = _new_customer(client)["id"]
    assert client.put(f"/api/customers/{cid}/field-mapping", json={"mapping": {"运单号": ["单号"], "店铺名称": ["店铺"]}}).status_code == 200
    file = _upload(client, cid, samples["fees_oct"])  # 10 月的文件：列名是「单号」「店铺」
    pick = {"id": "pick_col", "name": "取一列", "sql": "SELECT {{col}} FROM {{table}}", "params": {
        "table": {"type": "table", "required": True}, "col": {"type": "column", "of": "table", "required": True}}}
    result = _trial(client, cid, pick, {"table": "运费明细", "col": "运单号"}, [{"table": "运费明细", "file_id": file["file_id"], "sheet": "明细"}])
    assert result["status"] == "success", result
    assert result["inputs"][0]["renamed"] == {"单号": "运单号", "店铺": "店铺名称"}
    assert [c["name"] for c in result["columns"]] == ["运单号"]


def test_trial_with_pasted_table(client):
    cid = _new_customer(client)["id"]
    total = {"id": "sum_col", "name": "求和", "sql": "SELECT sum({{col}}) AS 总额 FROM {{table}}", "params": {
        "table": {"type": "table", "required": True}, "col": {"type": "column", "of": "table", "required": True}}}
    text = "店铺\t金额\nA\t10\nB\t20.5\nC\t30\n"
    result = _trial(client, cid, total, {"table": "t", "col": "金额"}, [{"table": "t", "text": text}])
    assert result["status"] == "success", result
    assert result["preview"]["rows"] == [[60.5]] and result["rows"] == 1
    assert (result["inputs"][0]["source"], result["inputs"][0]["columns"], result["inputs"][0]["rows"]) == ("text", ["店铺", "金额"], 3)
    # 两张表一起用：参数里的表名对应 inputs 里的 table
    join = {"id": "join_owner", "name": "带出归属", "params": {
        "left": {"type": "table", "required": True}, "right": {"type": "table", "required": True}},
        "sql": "SELECT l.*, r.归属 FROM {{left}} l LEFT JOIN {{right}} r ON l.店铺 = r.店铺"}
    owners = "店铺\t归属\nA\t华东\nB\t华南"
    joined = _trial(client, cid, join, {"left": "t", "right": "o"}, [{"table": "t", "text": text}, {"table": "o", "text": owners}])
    assert joined["status"] == "success" and joined["rows"] == 3
    assert [c["name"] for c in joined["columns"]] == ["店铺", "金额", "归属"]
    assert joined["preview"]["rows"][2] == ["C", 30, None]


def test_trial_failures_are_reported_not_raised(client):
    cid = _new_customer(client)["id"]
    inputs = [{"table": "t", "text": "店铺\t金额\nA\t10\n"}]
    ok = {"table": "t", "amount_col": "金额", "threshold": 1}

    def failed(formula, params, data=inputs) -> dict:
        result = _trial(client, cid, formula, params, data)
        assert result["status"] == "failed", result
        return result

    # 公式本身有问题
    r = failed(_variant(sql="SELECT {{nope}}"), ok)
    assert r["stage"] == "definition" and "未声明" in r["error"]
    # 参数取值不对：列不在表里（会给出可用的列）、类型不对、缺必填、表不存在
    r = failed(GOOD, {**ok, "amount_col": "钱"})
    assert r["stage"] == "params" and "可用的列" in r["error"] and "金额" in r["error"]
    assert failed(GOOD, {**ok, "threshold": "很多"})["stage"] == "params"
    assert "类型不对" in failed(GOOD, {**ok, "threshold": "很多"})["error"]
    assert "缺少必填参数" in failed(GOOD, {"threshold": 1})["error"]
    assert "不存在" in failed(GOOD, {**ok, "table": "别的表"})["error"]
    # SQL 执行出错：连同实际执行的 SQL 一起返回，方便对照
    bad = {"id": "bad_col", "name": "坏列", "sql": "SELECT 不存在的列 FROM {{table}}", "params": {"table": {"type": "table", "required": True}}}
    r = failed(bad, {"table": "t"})
    assert r["stage"] == "run" and "不存在的列" in r["error"] and 'FROM "t"' in r["sql"] and r["inputs"][0]["rows"] == 1
    # 试运行的数据读不出来
    r = failed(GOOD, ok, [{"table": "t", "text": "  \n "}])
    assert r["stage"] == "data" and "没有读到数据" in r["error"]


def test_trial_input_errors_are_request_errors(client, samples):
    cid = _new_customer(client)["id"]
    base = {"formula": GOOD, "params": {}}
    url = f"/api/customers/{cid}/formulas/trial"
    assert client.post(url, json={**base, "inputs": [{"table": "t", "file_id": "f_x", "text": "a\n1"}]}).status_code == 400
    assert client.post(url, json={**base, "inputs": [{"table": "t"}]}).status_code == 400
    assert client.post(url, json={**base, "inputs": [{"table": "t", "file_id": "f_nope"}]}).status_code == 404
    twice = [{"table": "t", "text": "a\n1"}, {"table": "t", "text": "a\n2"}]
    assert client.post(url, json={**base, "inputs": twice}).status_code == 400
    file = _upload(client, cid, samples["owners"])
    wrong_sheet = client.post(url, json={**base, "inputs": [{"table": "t", "file_id": file["file_id"], "sheet": "没有这个"}]})
    assert wrong_sheet.status_code == 200 and wrong_sheet.json()["result"]["stage"] == "data"


def test_trial_is_stopped_after_timeout(client, monkeypatch):
    monkeypatch.setattr(formula_service, "TRIAL_TIMEOUT", 0.5)
    cid = _new_customer(client)["id"]
    heavy = {"id": "heavy", "name": "很慢", "params": {}, "sql": "SELECT count(*) AS n FROM range(100000000000) a, range(1000) b"}
    started = time.perf_counter()
    result = _trial(client, cid, heavy, {}, [])
    assert result["status"] == "failed" and result["stage"] == "run" and "已自动中止" in result["error"]
    assert time.perf_counter() - started < 10


def test_formula_endpoints_follow_customer_permissions(client, samples):
    mine, other = _new_customer(client)["id"], _new_customer(client)["id"]
    foreign = _upload(client, other, samples["fees_sep"])
    # 别的客户的文件不能拿来试运行
    r = client.post(f"/api/customers/{mine}/formulas/trial",
                    json={"formula": GOOD, "params": {}, "inputs": [{"table": "t", "file_id": foreign["file_id"]}]})
    assert r.status_code == 403
    # 没有这个客户权限的成员：公式的所有接口都不能用
    m, _ = _register_member()
    url = f"/api/customers/{mine}/formulas"
    calls = [("GET", url, None), ("POST", url, GOOD), ("POST", f"{url}/check", GOOD),
             ("POST", f"{url}/trial", {"formula": GOOD, "params": {}, "inputs": []}),
             ("PUT", f"{url}/big_fee/status", {"status": "active"}), ("DELETE", f"{url}/big_fee", None)]
    for method, path, body in calls:
        assert m.request(method, path, json=body).status_code == 403, (method, path)
    # 自己新建的客户：成员自己就能用（创建者自动有权限）
    own = _new_customer(m)["id"]
    own_url = f"/api/customers/{own}/formulas"
    assert m.post(f"{own_url}/check", json=GOOD).json()["problems"] == []
    assert m.post(own_url, json=GOOD).status_code == 200
    assert m.post(f"{own_url}/trial", json={"formula": GOOD, "params": {}, "inputs": []}).status_code == 200


# ---------------------------------------------------------------------------
# 客户访问规则（没有分配界面，规则本身不变）
# ---------------------------------------------------------------------------


def test_customer_access_rules(client):
    """管理员看全部；其他人只看自己新建的，或者 user_customers 里授权给自己的；创建者不依赖那张表。"""
    m, member_id = _register_member()
    mine = _new_customer(m)["id"]
    admins = _new_customer(client)["id"]
    assert mine in _customer_ids(m) and admins not in _customer_ids(m)
    assert m.get(f"/api/customers/{mine}").status_code == 200
    denied = m.get(f"/api/customers/{admins}")
    assert denied.status_code == 403 and denied.json()["error"]["message"] == "你没有权限访问这个客户"
    assert {mine, admins} <= set(_customer_ids(client))  # 管理员看全部
    # 创建者的权限来自「自己建的」，不是只靠 user_customers 那一行：删掉授权行以后仍然看得到
    with session_scope() as db:
        db.query(UserCustomer).filter_by(user_id=member_id, customer_id=mine).delete()
    assert m.get(f"/api/customers/{mine}").status_code == 200 and mine in _customer_ids(m)
    # 被授权的客户（数据库里有授权行）也能看到
    with session_scope() as db:
        db.add(UserCustomer(user_id=member_id, customer_id=admins))
    assert m.get(f"/api/customers/{admins}").status_code == 200 and admins in _customer_ids(m)
    # 成员管理 / 客户管理的后台接口已经撤掉：不会再返回接口数据（有前端构建产物时落到单页应用的兜底页面，是 HTML）
    for url in ("/api/admin/users", "/api/admin/customers"):
        r = client.get(url)
        assert r.status_code in {404, 405} or "application/json" not in r.headers.get("content-type", ""), url


def test_revoked_customer_grant_blocks_existing_conversation_context(client):
    member, member_id = _register_member()
    customer_id = _new_customer(client)["id"]
    with session_scope() as db:
        db.add(UserCustomer(user_id=member_id, customer_id=customer_id))
    response = member.post("/api/conversations", json={"customer_id": customer_id})
    assert response.status_code == 200, response.text
    conversation_id = response.json()["conversation"]["id"]
    assert member.get(f"/api/conversations/{conversation_id}").status_code == 200

    with session_scope() as db:
        db.query(UserCustomer).filter_by(user_id=member_id, customer_id=customer_id).delete()

    denied = member.get(f"/api/conversations/{conversation_id}")
    assert denied.status_code == 403
    # Agent context construction uses the same live authorization gate.
    from sheetmate.services.chat import build_context

    with pytest.raises(Forbidden, match="没有权限访问这个客户"):
        build_context(member_id, conversation_id, llm=None)
