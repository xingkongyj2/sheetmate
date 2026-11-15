"""建议整理成内置公式：在所有客户的流程里找反复出现的同一段 SQL。

只看「自定义 SQL」步骤（内置公式本身就是 SQL 模板，Python 脚本没法直接变成公式）。
同一段 SQL 在至少 2 个不同流程里出现，才算重复；比较时忽略大小写、空白、注释和具体的取值（字符串、数字），
表名和列名保留，所以只有真的写法一样的才会归到一起。这里只做发现，不会自动保存任何公式：
用户点「整理」，由 AI 把表名、列名、取值抽成参数，用户确认后才入库。
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
MIN_SQL_CHARS = 40  # 太短的（SELECT * FROM t）不值得做成公式
MAX_SUGGESTIONS = 20

_COMMENT = re.compile(r"--[^\n]*|/\*.*?\*/", re.S)
_STRING = re.compile(r"'(?:[^']|'')*'")
_NUMBER = re.compile(r"(?<![\w\"])\d+(?:\.\d+)?(?![\w\"])")


def normalize(sql: str) -> str:
    text = _COMMENT.sub(" ", sql)
    text = _STRING.sub("?", text)
    text = _NUMBER.sub("?", text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(";").strip().lower()
    return re.sub(r" ?([,()]) ?", r"\1", text)


def fingerprint(sql: str) -> str:
    return hashlib.sha1(normalize(sql).encode("utf-8")).hexdigest()[:16]


def suggestions(db: Session) -> list[dict[str, Any]]:
    """按出现的流程数从多到少；已忽略 / 已整理的不再出现。"""
    dismissed = set(db.scalars(select(FormulaSuggestionDismissal.fingerprint)))
    names = {c.id: c.name for c in db.scalars(select(Customer))}
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in recipe_service.latest_records(db):
        recipe = recipe_service.record_to_recipe(record)
        for step in recipe.ordered_steps():
            sql = str((step.params or {}).get("sql") or "") if step.kind == "sql" and step.enabled else ""
            if len(normalize(sql)) < MIN_SQL_CHARS:
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
        })
    found.sort(key=lambda item: (-item["count"], item["name"]))
    return found[:MAX_SUGGESTIONS]


def dismiss(db: Session, key: str) -> None:
    if db.get(FormulaSuggestionDismissal, key) is None:
        db.add(FormulaSuggestionDismissal(fingerprint=key[:32]))
        db.flush()
