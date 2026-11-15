"""建议整理成内置公式：在所有客户的流程里找「做法一样」的自定义 SQL。

只看「自定义 SQL」步骤（内置公式本身就是 SQL 模板，Python 脚本没法直接变成公式）。
比较的是 SQL 的「结构」：忽略大小写、空白、注释、具体的取值（字符串、数字），也忽略表名、列名、别名，
只看用了哪些关键字和函数、按什么顺序组合。所以不同客户列名不同、但同样是「按金额分档」的 SQL 会归到一起。
同一种结构出现在至少 2 个不同流程里才算；太简单的（SELECT … FROM …）不算。
这里只做发现，不会自动保存任何公式：用户点「整理」，由 AI 把表名、列名、取值抽成参数，确认后才入库。
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sheetmate.db.models import Customer, FormulaSuggestionDismissal
from sheetmate.services.workflows import service as recipe_service

MIN_FLOWS = 2
MAX_SUGGESTIONS = 20
MAX_VARIANTS = 3  # 给 AI 看几种写法，让它知道哪些部分会变

_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_STRING = re.compile(r"'(?:[^']|'')*'")
_QUOTED = re.compile(r'"(?:[^"]|"")*"')
_NUMBER = re.compile(r"(?<![\w])\d+(?:\.\d+)?(?![\w])")
_WORD = re.compile(r"[^\W\d]\w*", re.U)

# 结构里要保留的关键字；其余没有括号的词（表名、列名、别名）都当成「会变的名字」
_KEYWORDS = frozenset("""
select from where group by order having limit offset with as join left right inner outer full cross on using union all
distinct and or not in is null like ilike between case when then else end over partition rows range preceding following
current row asc desc true false exists any some filter nulls first last interval date timestamp varchar double integer
bigint boolean cast
""".split())
# 出现这些才说明有「做法」，光是 SELECT 几列 FROM 一张表不值得做成公式
_FEATURES = frozenset("case join group having union over with where".split())


def normalize(sql: str) -> str:
    text = _COMMENT.sub(" ", sql)
    text = _STRING.sub("?", text)
    text = _QUOTED.sub("?", text)
    text = _NUMBER.sub("?", text)

    def word(match: re.Match[str]) -> str:
        name = match.group(0).lower()
        follows_paren = text[match.end():match.end() + 1] == "("
        return name if name in _KEYWORDS or follows_paren else "?"

    text = _WORD.sub(word, text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(";").strip()
    text = re.sub(r" ?([,()]) ?", r"\1", text)
    text = re.sub(r" ?([<>=!+\-*/|%]+) ?", r"\1", text)
    text = re.sub(r"(\?\.)+\?", "?", text)  # a.b 之类的限定名合成一个名字
    return re.sub(r"\?( \?)+", "?", text)  # 表名后面不带 AS 的别名合并到名字里


def fingerprint(sql: str) -> str:
    return hashlib.sha1(normalize(sql).encode("utf-8")).hexdigest()[:16]


def _worth_suggesting(sql: str) -> bool:
    words = set(re.findall(r"[a-z]+", normalize(sql)))
    return len(normalize(sql)) >= 30 and bool(words & _FEATURES)


def suggestions(db: Session) -> list[dict[str, Any]]:
    """按出现的流程数从多到少；已忽略 / 已整理的不再出现。"""
    dismissed = set(db.scalars(select(FormulaSuggestionDismissal.fingerprint)))
    names = {c.id: c.name for c in db.scalars(select(Customer))}
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in recipe_service.latest_records(db):
        recipe = recipe_service.record_to_recipe(record)
        for step in recipe.ordered_steps():
            sql = str((step.params or {}).get("sql") or "") if step.kind == "sql" and step.enabled else ""
            if not _worth_suggesting(sql):
                continue
            groups.setdefault(fingerprint(sql), []).append({
                "recipe_id": record.recipe_id, "flow": record.name, "customer": names.get(record.customer_id or "", ""),
                "step": step.name, "description": step.description, "sql": sql})
    found = []
    for key, items in groups.items():
        flows = {item["recipe_id"] for item in items}
        if key in dismissed or len(flows) < MIN_FLOWS:
            continue
        step_name = Counter(item["step"] for item in items).most_common(1)[0][0]
        description = next((item["description"] for item in items if item["description"]), "")
        found.append({
            "id": key, "name": step_name, "description": description, "count": len(flows),
            "customers": sorted({item["customer"] for item in items if item["customer"]}),
            "flows": sorted({item["flow"] for item in items})[:6], "sql": items[0]["sql"],
            "variants": _variants(items),
        })
    found.sort(key=lambda item: (-item["count"], item["name"]))
    return found[:MAX_SUGGESTIONS]


def _variants(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    """不同写法各取一个（同一流程、同样写法的不重复），最多 MAX_VARIANTS 个。"""
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for item in items:
        key = re.sub(r"\s+", " ", item["sql"]).lower()
        if key in seen:
            continue
        seen.add(key)
        out.append({"customer": item["customer"], "flow": item["flow"], "sql": item["sql"]})
        if len(out) >= MAX_VARIANTS:
            break
    return out


def dismiss(db: Session, key: str) -> None:
    if db.get(FormulaSuggestionDismissal, key) is None:
        db.add(FormulaSuggestionDismissal(fingerprint=key[:32]))
        db.flush()
