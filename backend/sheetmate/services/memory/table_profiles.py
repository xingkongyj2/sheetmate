"""原件版本与表格探测记忆：同一文件跨会话复用，修改副本不回写原件记忆。"""

from __future__ import annotations

import hashlib
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from sheetmate.db.session import session_scope
from sheetmate.db.models import FileRecord
from sheetmate.engine.tables.memory import (
    MAX_OBSERVATIONS,
    MAX_TABLES_CONTEXT_CHARS,
    SCHEMA_VERSION,
    _context_projection,
    format_table_memory,
    portable_memory,
    restore_memory,
    seed_profile,
)
from sheetmate.engine.tables.workspace import TableInfo, Workspace

FILE_VERSION_KEY = "file_probe_version"
FILE_MEMORY_KEY = "table_probe_memory"


def content_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def file_version(path: Path, previous: dict[str, Any] | None = None) -> dict[str, Any]:
    """未改变原件仅做 stat；变化时重新计算内容摘要，避免同名同结构误命中。"""
    stat = path.stat()
    signature = {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns}
    if isinstance(previous, dict) and previous.get("sha256") and all(previous.get(k) == v for k, v in signature.items()):
        return {**signature, "sha256": previous["sha256"]}
    return {**signature, "sha256": content_digest(path)}


def file_memories_text(records: Iterable[FileRecord], loaded_file_ids: set[str] | None = None) -> str:
    """展示已授权但未加载文件的有效记忆，不读取工作簿、不创建工作区表。"""
    loaded = loaded_file_ids or set()
    blocks = []
    for record in records:
        if record.id in loaded:
            continue
        profile = record.profile or {}
        sheets = profile.get("sheets") or []
        if not sheets:
            continue
        heading = f"文件 file_id={record.id}「{record.filename}」（尚未加载到工作区）"
        previous = profile.get(FILE_VERSION_KEY) or {}
        source = Path(record.stored_path)
        if not source.is_file():
            blocks.append(heading + "：原件不可用，无法验证已有探测；不要引用旧结构或统计。")
            continue
        if not previous.get("sha256"):
            blocks.append(heading + "：历史探测缺少文件版本，需先调用 load_file 重新验证后使用。")
            continue
        current = file_version(source, previous)
        if current["sha256"] != previous["sha256"]:
            blocks.append(heading + "：原件已更新，旧探测失效；需先调用 load_file 读取当前版本。")
            continue
        if current != previous:
            record.profile = {**profile, FILE_VERSION_KEY: current}
        saved = profile.get(FILE_MEMORY_KEY) or {}
        memories = (saved.get("sheets") or {}) if saved.get("sha256") == current["sha256"] else {}
        descriptions = []
        for sheet in sheets:
            fields = sheet.get("columns") or []
            if not fields or not isinstance(sheet.get("rows"), int):
                continue
            name = sheet.get("sheet") or "未命名工作表"
            revision = f"file:{current['sha256']}:{name}"
            memory = memories.get(name) or {}
            columns = [{"name": field["name"], "type": field.get("dtype", "text")} for field in fields]
            probe = {"schema_version": SCHEMA_VERSION, "revision": revision,
                     "profile": {"columns": fields, "keys": sheet.get("keys", {}),
                                 "quality": sheet.get("quality", {})}, "observations": []}
            if (memory.get("schema_version") == SCHEMA_VERSION and memory.get("rows") == sheet["rows"]
                    and [column.get("name") for column in memory.get("columns", [])] == [field["name"] for field in fields]):
                columns = memory["columns"]
                probe.update({key: deepcopy(value) for key, value in memory.items()
                              if key not in {"schema_version", "revision", "rows", "columns"}})
            info = TableInfo(name=name, label=f"{record.filename} · {name}", source="uploaded",
                             rows=sheet["rows"], columns=columns, file_id=record.id, filename=record.filename,
                             sheet=name, header=sheet.get("header"), revision=revision, probe_memory=probe,
                             read_quality=dict(sheet.get("quality") or {}))
            descriptions.append(format_table_memory(info, heading=f"已验证工作表「{name}」：{info.rows} 行 × {len(columns)} 列"))
        if descriptions:
            blocks.append(heading + "\n" + "\n\n".join(descriptions))
    if not blocks:
        return ""
    return _context_projection(("以下是文件原件的结构化探测记忆，可复用与当前版本一致的事实，先核对读取质量警告。文件尚未加载；"
            "需要执行公式、SQL 或脚本时先调用 load_file(file_id)，使用返回的真实工作区表名。\n\n"
            + "\n\n".join(blocks)), MAX_TABLES_CONTEXT_CHARS)


def restore_workspace_memories(ws: Workspace, records: Iterable[FileRecord]) -> None:
    """仅恢复已授权的文件；旧工作区或原件变化时重新加载，不能给旧数据贴新画像。"""
    from sheetmate.services.files.service import load_into_workspace

    available = {record.id: record for record in records}
    reloaded: set[str] = set()
    for info in ws.list_tables():
        if info.source != "uploaded" or not info.file_id or info.file_id in reloaded:
            continue
        record = available.get(info.file_id)
        if record is None:
            continue
        source = Path(record.stored_path)
        if not source.is_file():
            ws.drop(info.name)
            continue
        profile = dict(record.profile or {})
        version = file_version(source, profile.get(FILE_VERSION_KEY))
        if not info.file_version or info.file_version != version["sha256"]:
            load_into_workspace(ws, record)
            reloaded.add(record.id)
            continue
        saved = profile.get(FILE_MEMORY_KEY) or {}
        restored = saved.get("sha256") == info.file_version and restore_memory(
            ws, info.name, (saved.get("sheets") or {}).get(info.sheet, {}),
        )
        # 导入其他会话的新探测时，restore_memory 会与当前版本的已有观察合并。
        if restored or portable_memory(ws.require(info.name)):
            continue
        if (profile.get(FILE_VERSION_KEY) or {}).get("sha256") == info.file_version:
            sheet = next((sheet for sheet in profile.get("sheets", []) if sheet.get("sheet") == info.sheet), None)
            if sheet:
                seed_profile(ws, info.name, sheet)


def _merge_portable(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """不同会话探测了不同字段时保留双方的已验证结果。"""
    import json

    if (previous.get("schema_version") != current.get("schema_version")
            or previous.get("rows") != current.get("rows") or previous.get("columns") != current.get("columns")):
        return deepcopy(current)
    merged = deepcopy(current)
    observations = {}
    for item in [*(previous.get("observations") or []), *(current.get("observations") or [])]:
        key = json.dumps([item.get("kind"), item.get("params")], ensure_ascii=False, sort_keys=True, default=str)
        observations.pop(key, None)
        observations[key] = item
    merged["observations"] = list(observations.values())[-MAX_OBSERVATIONS:]
    return merged


def persist_workspace_memories(ws: Workspace) -> None:
    """保存仍对应原件的探测；计算表、重写后的表及修改过的文件副本均不冒充原件。"""
    grouped: dict[str, list] = {}
    for info in ws.list_tables():
        if info.source == "uploaded" and info.file_id and info.file_version and portable_memory(info):
            grouped.setdefault(info.file_id, []).append(info)
    if not grouped:
        return
    with session_scope() as db:
        for file_id, tables in grouped.items():
            record = db.get(FileRecord, file_id)
            if record is None or not Path(record.stored_path).is_file():
                continue
            profile = dict(record.profile or {})
            version = file_version(Path(record.stored_path), profile.get(FILE_VERSION_KEY))
            saved = profile.get(FILE_MEMORY_KEY) or {}
            memories = deepcopy(saved.get("sheets") or {}) if saved.get("sha256") == version["sha256"] else {}
            changed = False
            for info in tables:
                if info.file_version != version["sha256"] or not info.sheet:
                    continue
                current = portable_memory(info)
                if current:
                    memories[info.sheet] = _merge_portable(memories.get(info.sheet, {}), current)
                    changed = True
            if changed:
                record.profile = {**profile, FILE_VERSION_KEY: version,
                                  FILE_MEMORY_KEY: {"schema_version": 1, "sha256": version["sha256"], "sheets": memories}}
