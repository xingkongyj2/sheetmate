"""工作区里的数据一律是令牌版：载入时脱敏，交付时还原，展示给用户时还原。

- 载入：上传的文件载入工作区时，识别出的联系方式、地址列（和格子里夹着的号码、地址）换成令牌。AI 和所有工具、脚本、中间文件
  从头到尾只碰令牌；同一个真实值永远得到同一个令牌，所以分组、关联、去重照常能用。
- 交付：结果文件登记时，里面的令牌换回真实值（没有令牌就不动）。
- 展示：给用户看的表格分页、聊天里的事件，令牌也换回真实值（数据本身仍是令牌）。
"""

from __future__ import annotations

import dataclasses
import logging
import os
import re
import secrets
import shutil
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

import pandas as pd

from sheetmate.core.config.settings import settings
from sheetmate.engine.tables.workspace import Workspace
from sheetmate.privacy import TOKEN_RE, Stats, privacy
from sheetmate.services.privacy import masked_copy

logger = logging.getLogger(__name__)

_KEEP = {"name", "revision", "probe_memory", "columns", "rows"}
_XML_PARTS = re.compile(r"^xl/(sharedStrings\.xml|worksheets/[^/]+\.xml|comments[^/]*\.xml|drawings/[^/]+\.xml)$")


def protecting() -> bool:
    """设置页的脱敏总开关打开、模块可用时才在载入时脱敏。"""
    return bool(privacy.enabled and privacy.guard is not None and privacy.healthy)


def _kinds(frame: pd.DataFrame, flagged: dict[str, dict[str, Any]], reported: bool) -> list[str | None]:
    guard = privacy.require()
    kinds = masked_copy._kinds(frame, flagged)
    if reported:
        return kinds
    # 没有识别记录的文件（AI 生成的中间文件）：已知的列名之外，再按内容判断一次
    out = []
    for name, kind in zip(frame.columns, kinds):
        out.append(kind or guard._column_kind(name, frame[name].head(200).tolist()))
    return out


def mask_frame(file_id: str | None, sheet: str, frame: pd.DataFrame, stats: Stats | None = None) -> tuple[pd.DataFrame, dict[str, str | None]]:
    """一张表的令牌版，和每一列的类型。stats 传进来就能知道换了多少个值。"""
    found = masked_copy._fetch(file_id) if file_id else None
    reported = found is not None and found[2] == "ready"
    flagged = masked_copy._flagged(found[3], sheet) if reported else {}
    kinds = _kinds(frame, flagged, reported)
    return masked_copy._mask(frame, kinds, stats or Stats()), {str(name): kind.rstrip("!") if kind else None for name, kind in zip(frame.columns, kinds)}


def mask_profile(sheet_profile: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    """上传时算出的画像里有每列的样例、范围、合计：按令牌版的表重新算，AI 读到的画像里不能有真实值（手机号存成数字时，范围和合计也是真实值）。"""
    from sheetmate.engine.workbooks.profile import detect_keys, profile_columns

    columns = profile_columns(frame)
    return {**sheet_profile, "columns": columns, "keys": detect_keys(frame, columns)}


def _resync_uploaded(ws: Workspace) -> int:
    """用户在脱敏维护页里改了某个文件的列选择：这个文件在工作区里的表按新的选择重新生成（从原文件重读再脱敏）。
    重新生成过的话，别的表（计算出来的）也重新检查一遍：新选了脱敏的列，它的真实值已经登记成已知值，会在这些表里被换掉。"""
    from sheetmate.db.models import FileRecord
    from sheetmate.db.session import session_scope
    from sheetmate.engine.workbooks.reader import read_workbook
    from sheetmate.services.privacy import choices

    rebuilt, books = 0, {}
    for info in ws.list_tables():
        if info.source != "uploaded" or not info.file_id or not info.privacy_masked:
            continue
        signature = choices.signature(info.file_id)
        if info.privacy_sig == signature:
            continue
        if info.file_id not in books:
            with session_scope() as db:
                record = db.get(FileRecord, info.file_id)
                path = Path(record.stored_path) if record is not None else None
            try:
                books[info.file_id] = (path, {sheet.name: sheet for sheet in read_workbook(path)} if path and path.is_file() else {})
            except Exception:
                logger.exception("按新的脱敏选择重新生成「%s」时读不了原文件", info.name)
                books[info.file_id] = (path, {})
        path, sheets = books[info.file_id]
        sheet = sheets.get(info.sheet or "")
        if sheet is None:
            ws.update_meta(info.name, privacy_sig=signature)         # 原文件不在了：保持现状，不反复重试
            continue
        masked, _ = mask_frame(info.file_id, info.sheet or "", sheet.df)
        meta = {key: value for key, value in dataclasses.asdict(info).items() if key not in _KEEP}
        meta.update(privacy_masked=True, privacy_sig=signature, file_version=_sha(path) if info.file_version else "")
        ws.put_dataframe(info.name, masked, **meta)
        rebuilt += 1
    if rebuilt:
        for info in ws.list_tables():
            if info.source != "uploaded":
                ws.update_meta(info.name, privacy_masked=False)
    return rebuilt


def _sha(path: Path | None) -> str:
    from sheetmate.services.privacy.masked_files import _digest

    return _digest(path) if path is not None else ""


def protect_workspace(ws: Workspace) -> int:
    """对话每一轮开头：还没换成令牌的上传表（上传时识别还没完成，载入的是原文）现在换掉。返回处理了几张表。"""
    if not protecting():
        return 0
    done = _resync_uploaded(ws)
    for info in ws.list_tables():
        if info.privacy_masked:
            continue
        stats = Stats()
        masked, _ = mask_frame(info.file_id if info.source == "uploaded" else None, info.sheet or "", ws.df(info.name), stats)
        if info.source != "uploaded" and not stats.total:
            ws.update_meta(info.name, privacy_masked=True)    # 计算出来的表里本来就没有敏感内容：只做个标记，不重建
            continue
        meta = {key: value for key, value in dataclasses.asdict(info).items() if key not in _KEEP}
        meta["privacy_masked"] = True
        if info.source == "uploaded" and info.file_id:
            from sheetmate.services.privacy import choices

            meta["privacy_sig"] = choices.signature(info.file_id)
        ws.put_dataframe(info.name, masked, **meta)
        done += 1
    return done


# ---------------------------------------------------------------------------
# 还原
# ---------------------------------------------------------------------------


def restore_for_display(value: Any) -> Any:
    """给用户看的内容（事件、表格分页）：令牌换回真实值。模块没准备好时原样返回。"""
    guard = privacy.guard
    if guard is None:
        return value
    return guard.restore_object(value)


def _restore_xml(guard: Any, text: str) -> str:
    return TOKEN_RE.sub(lambda match: escape(guard.restore_text(match.group(0))), text)


def restore_file(path: Path) -> bool:
    """结果文件里的令牌换回真实值，原地替换；没有令牌就一个字节都不动。返回有没有改动。"""
    guard = privacy.guard
    suffix = path.suffix.lower()
    if guard is None or suffix not in {".xlsx", ".xlsm", ".csv", ".txt"}:
        return False
    try:
        if suffix in {".csv", ".txt"}:
            raw = path.read_bytes()
            text = raw.decode("utf-8-sig", errors="strict")
            restored = guard.restore_text(text)
            if restored == text:
                return False
            path.write_bytes(restored.encode("utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"))
            return True
        return _restore_workbook(guard, path)
    except Exception:  # 还原失败不能毁掉结果文件：保留原文件，并留下记录（令牌留在文件里，用户一眼能看出来）
        logger.exception("结果文件还原令牌失败：%s", path)
        privacy.log.emit("warn", "还原", f"结果文件「{path.name}」里的令牌没能还原，文件保持原样")
        return False


def _has_tokens(path: Path) -> bool:
    suffix = path.suffix.lower()
    try:
        if suffix in {".csv", ".txt"}:
            return bool(TOKEN_RE.search(path.read_text("utf-8-sig", errors="ignore")))
        if suffix in {".xlsx", ".xlsm"} and zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                return any(_XML_PARTS.match(item.filename) and TOKEN_RE.search(archive.read(item.filename).decode("utf-8", "ignore"))
                           for item in archive.infolist())
    except OSError:
        return False
    return False


def restored_download(path: Path) -> tuple[Path, bool]:
    """下载给用户的文件：里面还有令牌（还没确认成正式结果的文件）就给一份换回真实值的临时副本。返回 (路径, 是不是临时副本)。"""
    if privacy.guard is None or not _has_tokens(path):
        return path, False
    folder = settings.data_dir / "privacy" / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / f"{secrets.token_hex(8)}{path.suffix}"
    shutil.copyfile(path, copy)
    restore_file(copy)
    return copy, True


def _restore_workbook(guard: Any, path: Path) -> bool:
    if not zipfile.is_zipfile(path):
        return False
    with zipfile.ZipFile(path) as source:
        changed: dict[str, bytes] = {}
        for item in source.infolist():
            if not _XML_PARTS.match(item.filename):
                continue
            data = source.read(item.filename)
            if b"_" not in data:
                continue
            text = data.decode("utf-8")
            restored = _restore_xml(guard, text)
            if restored != text:
                changed[item.filename] = restored.encode("utf-8")
        if not changed:
            return False
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
        try:
            with zipfile.ZipFile(temporary, "w") as target:
                for item in source.infolist():
                    payload = changed.get(item.filename)
                    target.writestr(item, payload if payload is not None else source.read(item.filename),
                                    compress_type=item.compress_type)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    os.replace(temporary, path)
    privacy.log.emit("info", "还原", f"结果文件「{path.name}」里的令牌已换回真实值", file=path.name)
    return True
