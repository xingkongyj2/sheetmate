"""脱敏副本：查看和下载「AI 实际看到的样子」。

和发给 AI 时用的是同一套处理（识别出的敏感列整列换成令牌，其余格子里夹着的联系方式和地址也会换），
所以用户能亲眼确认 AI 看到的是什么。副本只在本机生成，不经过任何模型；每次下载都会在脱敏日志里留一条记录。
生成后还会再检查一遍，里面如果还有联系方式或地址就拒绝，不会把没处理干净的内容交给用户当作"脱敏副本"。
"""

from __future__ import annotations

import asyncio
import io
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

import pandas as pd

from sheetmate.core.errors import ServiceError
from sheetmate.db.models import FileRecord, PrivacyFile, User
from sheetmate.db.session import session_scope
from sheetmate.engine.tables.sql import frame_rows
from sheetmate.engine.workbooks.reader import SheetData, read_workbook
from sheetmate.privacy.guard import FORCED
from sheetmate.privacy import ACTION_LABELS, ACTIONS, KIND_LABELS, TOKEN_RE, Scope, Stats, bind, privacy
from sheetmate.privacy.rules import cell_text, is_numeric_cell
from sheetmate.services.privacy import choices
from sheetmate.services.privacy import service as privacy_service

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_JOIN = "¦"   # 检查时把单元格连起来用的分隔符：不是空白、不是数字，不会让相邻两格拼出一个号码

# 翻页时不用每次重新读文件：最近读过的两个工作簿（原始数据，不是脱敏后的）放在内存里，文件一变就失效
_RAW: OrderedDict[tuple[str, int, int], list[SheetData]] = OrderedDict()
_RAW_LOCK = threading.Lock()
_RAW_MAX = 2


def _read(path: str) -> list[SheetData]:
    try:
        stat = Path(path).stat()
    except OSError as exc:
        raise ServiceError("文件已经不存在，无法生成脱敏副本。") from exc
    key = (path, stat.st_mtime_ns, stat.st_size)
    with _RAW_LOCK:
        hit = _RAW.get(key)
        if hit is not None:
            _RAW.move_to_end(key)
            return hit
    try:
        sheets = read_workbook(Path(path))
    except Exception as exc:  # 读取库的各种错误都归成一句人话
        raise ServiceError(f"文件无法读取，不能生成脱敏副本（{type(exc).__name__}）。") from exc
    if not sheets:
        raise ServiceError("这个文件里没有可显示的工作表。")
    with _RAW_LOCK:
        _RAW[key] = sheets
        while len(_RAW) > _RAW_MAX:
            _RAW.popitem(last=False)
    return sheets


def _fetch(file_id: str) -> tuple[str, str, str, list[dict[str, Any]]] | None:
    with session_scope() as db:
        record = db.get(FileRecord, file_id)
        row = db.get(PrivacyFile, file_id)
        if record is None or row is None:
            return None
        sheets = list(row.sheets or [])
        return record.filename, record.stored_path, row.status, choices.effective(file_id, sheets, for_masking=True)


async def _ready(user: User, file_id: str, conversation_id: str | None) -> tuple[str, str, list[dict[str, Any]]]:
    """文件的识别结果：已经有就直接用（翻页不重复识别），没有才识别一次（失败会带着原因停止）。"""
    privacy_service.start()
    found = _fetch(file_id)
    if found is None or found[2] != "ready":
        async with privacy_service.protecting([file_id], conversation_id=conversation_id, user_id=user.id):
            pass
        found = _fetch(file_id)
    if found is None or found[2] != "ready":
        raise ServiceError("这个文件不是表格，没有可查看的脱敏副本。")
    return found[0], found[1], found[3]


# ---------------------------------------------------------------------------
# 处理
# ---------------------------------------------------------------------------


def _flagged(report: list[dict[str, Any]], sheet: str) -> dict[str, dict[str, Any]]:
    found = {column["name"]: column for entry in report if entry["sheet"] == sheet for column in entry["columns"]}
    for entry in report:
        if entry["sheet"] == sheet:
            for name in entry.get("kept", []):              # 用户选了「不脱敏」的列：不按整列处理，也不按列名被别处的结果带上
                found[name] = {"name": name, "kind": None}
    return found


def _kinds(frame: pd.DataFrame, flagged: dict[str, dict[str, Any]]) -> list[str | None]:
    """每一列的类型：识别结果里有的用识别结果，其余的看注册表（别的文件里识别出的同名列也算）。"""
    guard = privacy.require()
    return [column_kind(flagged[str(name)]) if str(name) in flagged else guard.registry.kind_for(str(name)) for name in frame.columns]


def column_kind(found: dict[str, Any]) -> str | None:
    """识别结果里一列的类型；手动选的整列后面带 FORCED：按选的类型把每一格都换成令牌。"""
    kind = found.get("kind")
    return kind + FORCED if kind and found.get("source") == "manual" else kind


SCAN_ROWS = 20_000      # 判断一列是不是真的被脱敏时最多看多少行


def really_masked(frame: pd.DataFrame, kinds: list[str | None]) -> list[bool]:
    """每一列是不是真的被脱敏了：里面已经是令牌，或者按这一列的类型处理会换掉某些格子。
    只是「按这个类型会处理」但没有任何格子真的被换（比如只写了城市的收货地）不算脱敏，界面上就显示成没脱敏。"""
    guard = privacy.require()
    head = frame.iloc[:SCAN_ROWS]
    out: list[bool] = []
    for position, kind in enumerate(kinds):
        if kind is None or guard._off(kind):
            out.append(False)
            continue
        forced, kind = kind.endswith(FORCED), kind.rstrip(FORCED)
        hit = False
        for value in head.iloc[:, position].dropna().drop_duplicates():
            if isinstance(value, str) and TOKEN_RE.search(value):
                hit = True
            elif kind == "custom" or forced:
                hit = bool(cell_text(value).strip())
            else:
                hit = guard.protect_cell(kind, value, Stats()) != value
            if hit:
                break
        out.append(hit)
    return out


def _meta(name: str, kind: str | None, info: dict[str, Any] | None) -> dict[str, Any]:
    if kind is None:
        return {"name": name, "protected": False, "kind_label": None, "action_label": None}
    return {"name": name, "protected": True,
            "kind_label": info["kind_label"] if info else KIND_LABELS[kind],
            "action_label": info["action_label"] if info else ACTION_LABELS[ACTIONS[kind]]}


def _may_hold_mobile(series: pd.Series) -> bool:
    """数字列里有没有可能是 11 位手机号（没有就整列跳过，不用逐格检查）。"""
    try:
        return bool(series.abs().between(1.3e10, 1.99e10).any())
    except (TypeError, ValueError):
        return True


def _mask(frame: pd.DataFrame, kinds: list[str | None], stats: Stats) -> pd.DataFrame:
    guard = privacy.require()
    out = frame.copy()
    for position, kind in enumerate(kinds):
        series = out.iloc[:, position]
        if kind is None and (pd.api.types.is_bool_dtype(series) or pd.api.types.is_datetime64_any_dtype(series)):
            continue
        if kind is None and pd.api.types.is_numeric_dtype(series) and not _may_hold_mobile(series):
            continue
        out.isetitem(position, series.map(lambda value, kind=kind: guard.protect_cell(kind, value, stats)).astype(object))
    return out


def _assert_clean(frame: pd.DataFrame) -> None:
    """生成之后再查一遍：还剩联系方式或地址就拒绝，不能把没处理干净的内容当作脱敏副本给用户。"""
    cells = frame.to_numpy().ravel()
    # 数字单元格也要查：手机号、座机常被表格存成数字
    text = _JOIN.join(cell_text(value) for value in cells if isinstance(value, str) or is_numeric_cell(value))
    remaining = privacy.require().leaks(text)
    if remaining:
        raise ServiceError(f"脱敏副本里仍有{'、'.join(remaining)}没能处理，为保护数据已停止。")


# ---------------------------------------------------------------------------
# 查看
# ---------------------------------------------------------------------------


def _preview(filename: str, path: str, report: list[dict[str, Any]], sheet: str | None, offset: int, limit: int) -> dict[str, Any]:
    sheets = _read(path)
    chosen = next((item for item in sheets if item.name == sheet), None) if sheet else sheets[0]
    if chosen is None:
        raise ServiceError(f"没有工作表「{sheet}」。")
    flagged = _flagged(report, chosen.name)
    kinds = _kinds(chosen.df, flagged)
    masked = _mask(chosen.df.iloc[offset:offset + limit], kinds, Stats())   # 只处理这一页，翻页才不慢
    shown = [kind.rstrip(FORCED) if kind and really else None for kind, really in zip(kinds, really_masked(chosen.df, kinds))]   # 标记的只有真的被脱敏的列
    _assert_clean(masked)
    return {
        "filename": filename,
        "sheets": [{"sheet": item.name, "rows": int(len(item.df))} for item in sheets],
        "sheet": chosen.name, "total": int(len(chosen.df)), "offset": offset, "limit": limit,
        "columns": [_meta(str(name), shown[index], flagged.get(str(name))) for index, name in enumerate(chosen.df.columns)],
        "rows": frame_rows(masked),
    }


async def masked_preview(user: User, file_id: str, *, sheet: str | None = None, offset: int = 0, limit: int = 50,
                         conversation_id: str | None = None) -> dict[str, Any]:
    filename, path, report = await _ready(user, file_id, conversation_id)
    return await asyncio.to_thread(_preview, filename, path, report, sheet, offset, limit)


# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------


def _sheet_title(name: str, used: set[str]) -> str:
    """Excel 工作表名：不超过 31 个字符，不能含 []:*?/\\，也不能重名。"""
    base = re.sub(r"[\[\]:*?/\\]", "_", name).strip()[:31] or "Sheet"
    title, number = base, 2
    while title.casefold() in used:
        suffix = f"_{number}"
        title, number = base[:31 - len(suffix)] + suffix, number + 1
    used.add(title.casefold())
    return title


def _build(path: str, report: list[dict[str, Any]]) -> tuple[bytes, Stats]:
    stats, used, buffer = Stats(), set(), io.BytesIO()
    # 先把所有工作表处理完并检查干净，再开始写文件：检查不通过时要抛出清楚的原因，
    # 不能让「一个工作表都没写」的写入器关闭错误把它盖住
    frames: list[tuple[str, pd.DataFrame]] = []
    for sheet in _read(path):
        kinds = _kinds(sheet.df, _flagged(report, sheet.name))
        masked = _mask(sheet.df, kinds, stats)
        _assert_clean(masked)
        frames.append((_sheet_title(sheet.name, used), masked))
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        for title, masked in frames:
            masked.to_excel(writer, sheet_name=title, index=False)
    return buffer.getvalue(), stats


async def masked_xlsx(user: User, file_id: str, *, conversation_id: str | None = None) -> tuple[str, bytes]:
    """生成脱敏副本（xlsx），返回 (下载文件名, 内容)，并在脱敏日志里记一条。"""
    filename, path, report = await _ready(user, file_id, conversation_id)
    content, stats = await asyncio.to_thread(_build, path, report)
    with bind(Scope(conversation_id=conversation_id, user_id=user.id)):
        privacy.log.emit("info", "导出", f"{filename}：下载了脱敏副本，{stats.total} 个联系方式/地址已替换为令牌（副本里没有真实内容）",
                         file_id=file_id, values=stats.total, kinds=dict(stats.values))
    return f"{Path(filename).stem}_脱敏.xlsx", content
