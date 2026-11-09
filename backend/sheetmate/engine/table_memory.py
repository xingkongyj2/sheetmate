"""表格探测技能的版本化记忆：确定性事实直接存储，不经过模型摘要。

每份记忆绑定工作区表的内容版本；对话压缩、重启与元数据改名不会改变这些事实。
复制与文件级保存使用显式导入接口，普通重建表不会继承旧记忆。
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
from copy import deepcopy
from typing import Any

import pandas as pd

from sheetmate.engine.sqlutil import frame_to_text, to_jsonable
from sheetmate.engine.workspace import TableInfo, Workspace

SCHEMA_VERSION = 1
MAX_OBSERVATIONS = 32


def _memory(info: TableInfo) -> dict[str, Any]:
    memory = info.probe_memory or {}
    if memory.get("schema_version") != SCHEMA_VERSION or memory.get("revision") != info.revision:
        return {"schema_version": SCHEMA_VERSION, "revision": info.revision, "observations": []}
    return deepcopy(memory)


def cached_profile(info: TableInfo) -> dict[str, Any] | None:
    profile = _memory(info).get("profile")
    return profile if isinstance(profile, dict) else None


def save_profile(ws: Workspace, name: str, profile: dict[str, Any], *, expected_revision: str) -> bool:
    with ws.locked():
        info = ws.require(name)
        if info.revision != expected_revision:
            return False
        memory = _memory(info)
        memory["profile"] = deepcopy(profile)
        return ws.save_probe_memory(name, memory, expected_revision=expected_revision)


def seed_profile(ws: Workspace, name: str, sheet_profile: dict[str, Any]) -> bool:
    """导入上传时已算出的画像，调用方须保证来自本次实际读取的同一工作表。"""
    with ws.locked():
        info = ws.require(name)
        columns = sheet_profile.get("columns", [])
        if (sheet_profile.get("rows") != info.rows or sheet_profile.get("cols") != len(info.columns)
                or [c.get("name") for c in columns] != info.column_names):
            return False
        if any(not {"name", "dtype", "null_rate", "unique", "samples"} <= c.keys() for c in columns):
            return False
        preview = sheet_profile.get("sample") or sheet_profile.get("preview") or {}
        profile = {"columns": deepcopy(columns), "keys": deepcopy(sheet_profile.get("keys", {})),
                   "sample": {"columns": info.column_names, "rows": deepcopy(preview.get("rows", []))}}
        return save_profile(ws, name, profile, expected_revision=info.revision)


def _observation_key(item: dict[str, Any]) -> str:
    return json.dumps([item.get("kind"), item.get("params", {})], ensure_ascii=False, sort_keys=True)


def cached_observation(info: TableInfo, kind: str, params: dict[str, Any]) -> pd.DataFrame | None:
    key = _observation_key({"kind": kind, "params": params})
    for item in reversed(_memory(info).get("observations", [])):
        if _observation_key(item) == key:
            return _frame_from_record(item["result"])
    return None


def save_observation(ws: Workspace, name: str, kind: str, params: dict[str, Any], result: pd.DataFrame,
                     *, expected_revision: str) -> bool:
    with ws.locked():
        info = ws.require(name)
        if info.revision != expected_revision:
            return False
        memory = _memory(info)
        item = {"kind": kind, "params": deepcopy(params), "result": _frame_record(result)}
        key = _observation_key(item)
        observations = [old for old in memory.get("observations", []) if _observation_key(old) != key]
        memory["observations"] = [*observations, item][-MAX_OBSERVATIONS:]
        return ws.save_probe_memory(name, memory, expected_revision=expected_revision)


def portable_memory(info: TableInfo) -> dict[str, Any]:
    """生成可存于 FileRecord.profile 的与工作区无关的 JSON 记忆。"""
    memory = _memory(info)
    if not memory.get("profile") and not memory.get("observations"):
        return {}
    memory.pop("revision", None)
    memory.update(rows=info.rows, columns=deepcopy(info.columns))
    return memory


def restore_memory(ws: Workspace, name: str, memory: dict[str, Any]) -> bool:
    """将已确认属于同一文件版本的便携记忆重新绑定到当前表版本。"""
    with ws.locked():
        info = ws.require(name)
        if (memory.get("schema_version") != SCHEMA_VERSION or memory.get("rows") != info.rows
                or memory.get("columns") != info.columns):
            return False
        restored = {k: deepcopy(v) for k, v in memory.items() if k not in {"rows", "columns"}}
        restored["revision"] = info.revision
        current = _memory(info)
        if current.get("profile") and not restored.get("profile"):
            restored["profile"] = current["profile"]
        restored["observations"] = _merge_observations(current.get("observations", []), restored.get("observations", []))
        return ws.save_probe_memory(name, restored, expected_revision=info.revision)


def copy_table_memory(source_info: TableInfo, target_ws: Workspace, target_name: str) -> bool:
    """在完整复制数据之后复制探测事实；不得用于仅仅具有相同结构的不同表。"""
    memory = portable_memory(source_info)
    return bool(memory) and restore_memory(target_ws, target_name, memory)


def _merge_observations(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = {_observation_key(item): deepcopy(item) for item in old}
    for item in new:
        key = _observation_key(item)
        merged.pop(key, None)
        merged[key] = deepcopy(item)
    return list(merged.values())[-MAX_OBSERVATIONS:]


def merge_table_memory(target_ws: Workspace, observed_ws: Workspace,
                       expected_revisions: dict[str, tuple[str, str]]) -> int:
    """将分析副本的新事实回写原表；原表和副本都必须仍是复制时的版本。"""
    merged = 0
    for name, (original_revision, copied_revision) in expected_revisions.items():
        with target_ws.locked(), observed_ws.locked():
            target, observed = target_ws.info(name), observed_ws.info(name)
            if (target is None or observed is None or target.revision != original_revision
                    or observed.revision != copied_revision):
                continue
            if copy_table_memory(observed, target_ws, name):
                merged += 1
    return merged


def table_memory_text(ws: Workspace, tables: list[TableInfo] | None = None) -> str:
    """直接读取所有字段与已探测事实，不读取数据表，也不截断结构。"""
    blocks = []
    for supplied in tables if tables is not None else ws.list_tables():
        info = ws.info(supplied.name)
        if info is None:
            continue
        blocks.append(format_table_memory(info))
    return "\n\n".join(blocks)


def format_table_memory(info: TableInfo, *, heading: str | None = None) -> str:
    """格式化已验证的表元数据快照；文件记忆无需为展示而打开工作簿或工作区。"""
    lines = [heading or f"表「{info.name}」（{info.label}）：{info.rows} 行 × {len(info.columns)} 列，内容版本 {info.revision}"]
    if heading:
        lines.append(f"内容版本：{info.revision}")
    if info.filename or info.sheet:
        lines.append(f"来源文件：{info.filename or '未命名'}；工作表：{info.sheet or info.name}")
    if info.header:
        header = info.header
        row_index = header.get("row_index") or []
        data_start = row_index[0] + 1 if row_index else int(header.get("header_row", 1)) + 1
        lines.append(f"表头：第 {header.get('header_row', 1)} 行，共 {header.get('header_rows', 1)} 行；"
                     f"数据起始行：{data_start}")
        if header.get("title"):
            lines.append(f"标题：{header['title']}")
        column_indexes = header.get("col_index") or []
        if column_indexes:
            lines.append("原工作表列位置：" + "；".join(
                f"{column}→第 {index + 1} 列" for column, index in zip(info.column_names, column_indexes)))
    profile = cached_profile(info)
    if profile is None:
        lines.append("已登记字段（尚无详细画像）：" + "；".join(f"{c['name']}（{c['type']}）" for c in info.columns))
    else:
        lines.append("已探测字段画像（本版本直接复用）：")
        for col in profile.get("columns", []):
            extra = (f"，范围 {col.get('min')}~{col.get('max')}，合计 {col.get('sum')}" if "sum" in col else "")
            lines.append(f"- {col['name']}：{col['dtype']}，空值 {col['null_rate']:.0%}，"
                         f"{col['unique']} 个不同值{extra}，样本 {col.get('samples', [])}")
        keys = profile.get("keys", {})
        if keys.get("candidates"):
            lines.append(f"候选主键：{keys['candidates']}")
        for duplicate in keys.get("duplicated", []):
            lines.append(f"重复键：{duplicate['column']}，{duplicate['duplicate_values']} 个值重复，"
                         f"最多 {duplicate['max_repeat']} 次，示例 {duplicate.get('examples', [])}")
    for observation in _memory(info).get("observations", []):
        kind = {"value_counts": "取值分布", "column_stats": "列统计"}.get(observation["kind"], observation["kind"])
        frame = _frame_from_record(observation["result"])
        lines.append(f"已完成{kind} {json.dumps(observation['params'], ensure_ascii=False)}：\n"
                     + frame_to_text(frame, limit=len(frame)))
    return "\n".join(lines)


def _pack_value(value: Any) -> Any:
    # DuckDB 结果中的日期、Decimal 保持类型，避免缓存命中前后显示口径改变。
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, dt.datetime):
        return {"__sm_type": "datetime", "value": value.isoformat()}
    if isinstance(value, dt.date):
        return {"__sm_type": "date", "value": value.isoformat()}
    if isinstance(value, dt.time):
        return {"__sm_type": "time", "value": value.isoformat()}
    if isinstance(value, decimal.Decimal):
        return {"__sm_type": "decimal", "value": str(value)}
    if isinstance(value, dict):
        return {key: _pack_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_pack_value(item) for item in value]
    return to_jsonable(value)


def _unpack_value(value: Any) -> Any:
    if isinstance(value, dict):
        converters = {"datetime": dt.datetime.fromisoformat, "date": dt.date.fromisoformat,
                      "time": dt.time.fromisoformat, "decimal": decimal.Decimal}
        if set(value) == {"__sm_type", "value"} and value["__sm_type"] in converters:
            return converters[value["__sm_type"]](value["value"])
        return {key: _unpack_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_unpack_value(item) for item in value]
    return value


def _frame_record(frame: pd.DataFrame) -> dict[str, Any]:
    return {"columns": list(frame.columns), "dtypes": [str(dtype) for dtype in frame.dtypes],
            "rows": [[_pack_value(value) for value in row] for row in frame.itertuples(index=False, name=None)]}


def _frame_from_record(record: dict[str, Any]) -> pd.DataFrame:
    frame = pd.DataFrame([[_unpack_value(value) for value in row] for row in record["rows"]], columns=record["columns"])
    for column, dtype in zip(frame.columns, record.get("dtypes", [])):
        try:
            frame[column] = frame[column].astype(dtype)
        except (TypeError, ValueError):
            pass
    return frame
