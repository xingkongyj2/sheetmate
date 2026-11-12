"""Reuse proven deterministic reads without turning reuse into new evidence."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sheetmate.engine.tables.sql import assert_single_select

_READ_QUALITY_ERRORS = ("truncated", "missing_formula_cache", "error_cells", "formula_error_cells")


def read_key(ctx: Any, name: str, args: dict[str, Any] | None,
             sources: list[dict[str, Any]]) -> str | None:
    """Identify a safe SELECT with its exact input facts and materialization intent.

    The engine's AST allowlist decides determinism and source identity. An
    unknown source, changed read quality or inaccessible table is always a miss.
    Descriptions are presentation; a requested result name is execution intent.
    """
    if name != "run_sql" or not isinstance(args, dict) or not isinstance(args.get("sql"), str) or not sources:
        return None
    try:
        sql = assert_single_select(args["sql"])
        with ctx.ws.locked():
            engine_key = ctx.ws._query_cache_key(sql)
            if engine_key is None:
                return None
            versions = dict(engine_key[1])
            supplied = {source.get("table"): source for source in sources if isinstance(source, dict)}
            if not versions or len(supplied) != len(sources) or supplied.keys() != versions.keys():
                return None
            for table, revision in versions.items():
                if ctx.table_filter is not None and table not in ctx.table_filter:
                    return None
                source = supplied[table]
                current = ctx.ws.info(table)
                if current is None or not revision or source.get("revision") != revision:
                    return None
                quality = current.read_quality or {}
                if any(quality.get(key) for key in _READ_QUALITY_ERRORS):
                    return None
                if source.get("read_quality", {}) != quality:
                    return None
                if "file_id" in source and source["file_id"] != current.file_id:
                    return None
                if "file_version" in source and source["file_version"] != current.file_version:
                    return None
            payload = {"tool": name, "engine_key": engine_key,
                       "args": {**{key: value for key, value in args.items() if key != "description"}, "sql": sql},
                       "sources": sorted(sources, key=lambda source: source["table"])}
            encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    except (ValueError, TypeError, KeyError):
        return None


def reusable_read(ctx: Any, results: dict[str, dict[str, Any]], key: str | None, *,
                  scope: str) -> tuple[str, dict[str, Any]] | None:
    """Find an original successful read whose current outputs are still usable.

    Results are already durable loop evidence; this adds no second payload
    cache. Older checkpoints have no read key and naturally execute as usual.
    """
    if key is None:
        return None
    for evidence_id, item in reversed(list(results.items())):
        if (item.get("read_key") != key or item.get("tool") != "run_sql" or item.get("scope") != scope
                or item.get("branch_id") != ctx.branch_id or item.get("ok") is not True or item.get("invalid")):
            continue
        evidence = item.get("evidence") or {}
        if (evidence.get("executed") is not True or evidence.get("status") != "success"
                or evidence.get("reused") or evidence.get("invalid") or item.get("executed") is False):
            continue
        if not isinstance(item.get("result"), str) or not item["result"]:
            continue
        # The key was made from current source facts, but visibility can change
        # between computing it and consulting a saved receipt.
        sources = evidence.get("source_tables") or []
        if not sources or any(not _current_table(ctx, source) for source in sources):
            continue
        artifacts = evidence.get("artifacts") or []
        if any(artifact.get("type") != "table" or not _current_table(ctx, artifact) for artifact in artifacts):
            continue
        return evidence_id, item
    return None


def _current_table(ctx: Any, saved: dict[str, Any]) -> bool:
    table = saved.get("table")
    if not isinstance(table, str) or not saved.get("revision"):
        return False
    if ctx.table_filter is not None and table not in ctx.table_filter:
        return False
    current = ctx.ws.info(table)
    if current is None or current.revision != saved["revision"]:
        return False
    return (saved.get("read_quality", {}) == (current.read_quality or {})
            and ("file_id" not in saved or saved["file_id"] == current.file_id)
            and ("file_version" not in saved or saved["file_version"] == current.file_version))
