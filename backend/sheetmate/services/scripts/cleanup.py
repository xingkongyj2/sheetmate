"""在业务作用域删除后，按数据库引用延迟清理脚本文件。

事务提交前只登记待删路径，物理清理由文件服务的事务监听器完成。
底层脚本存储不需要知道 ORM 模型或会话生命周期。
"""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select

from sheetmate.core.config.settings import settings
from sheetmate.db.models import Message, RecipeRecord, Run

_SCRIPT_ID = re.compile(r"scr_[0-9a-f]{32}\Z")


def queue_unreferenced_scope_scripts(db: Any, *, customer_ids: set[str] | None = None,
                                     conversation_ids: set[str] | None = None) -> None:
    """Queue scripts whose original scope was deleted and which no remaining row uses."""
    customer_ids = customer_ids or set()
    conversation_ids = conversation_ids or set()
    if not customer_ids and not conversation_ids:
        return

    used: set[str] = set()
    for message in db.scalars(select(Message)):
        for script_id in _SCRIPT_ID.findall(json.dumps(message.events or {}, ensure_ascii=False)):
            used.add(script_id)
    for run in db.scalars(select(Run)):
        for value in (run.steps, run.validation):
            for script_id in _SCRIPT_ID.findall(json.dumps(value or {}, ensure_ascii=False)):
                used.add(script_id)
    for recipe in db.scalars(select(RecipeRecord)):
        for script_id in _SCRIPT_ID.findall(json.dumps(recipe.body or {}, ensure_ascii=False)):
            used.add(script_id)

    folder = settings.data_dir / "scripts"
    if not folder.is_dir():
        return
    for metadata_path in folder.glob("scr_*.json"):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            script_id = metadata.get("script_id")
            origin = metadata.get("origin") or {}
            scoped = (origin.get("customer_id") in customer_ids or
                      origin.get("conversation_id") in conversation_ids)
            if (not isinstance(script_id, str) or not _SCRIPT_ID.fullmatch(script_id) or
                    not scoped or origin.get("global_access") or script_id in used):
                continue
            db.info.setdefault("deleted_aux_paths", set()).update({
                str(metadata_path), str(folder / f"{script_id}.py")
            })
        except (OSError, ValueError, TypeError):
            continue

