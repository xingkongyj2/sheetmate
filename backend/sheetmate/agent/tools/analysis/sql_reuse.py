"""Reuse verified empty SQL snapshots without multiplying derived tables."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sheetmate.engine.tables.workspace import TableInfo, Workspace


def empty_query_key(ws: Workspace, sql: str) -> str | None:
    # Share the engine's conservative AST allowlist and actual source versions.
    # Random, clock-dependent and unknown functions must always execute again.
    key = ws._query_cache_key(sql)
    if key is None:
        return None
    return hashlib.sha256(json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def reusable_empty_query(ctx: Any, key: str | None) -> TableInfo | None:
    if key is None:
        return None
    for info in ctx.visible_tables():
        snapshot = (info.step or {}).get("empty_query_snapshot") or {}
        if (isinstance(snapshot, dict) and info.rows == 0 and info.source == "computed" and info.created_by == "run_sql"
                and snapshot.get("key") == key and snapshot.get("revision") == info.revision):
            return info
    return None
