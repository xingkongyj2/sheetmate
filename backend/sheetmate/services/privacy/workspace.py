"""工作区里的数据一律是令牌版：上传就脱敏，中间所有环节都是令牌，只有下载时才还原。

- 载入：上传的文件载入工作区时，识别出的联系方式、地址列（和格子里夹着的号码、地址）换成令牌。AI 和所有工具、脚本、中间文件
  从头到尾只碰令牌；同一个真实值永远得到同一个令牌，所以分组、关联、去重照常能用。
- 结果文件：登记和交付时不还原，存下来的、聊天页和文件页里查看的都是令牌版，和上传的文件一致。
- 下载：统一在下载时把令牌换回真实值（见 restored_download）。
- 聊天里的事件文字、对话里的表格分页仍按展示需要把令牌换回真实值（数据本身仍是令牌）。
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


MIN_LINK_CHARS = 4      # 太短的值（几个字、数字）不按值对应，避免把普通内容当成同一个值


def _reconcile_derived(ws: Workspace) -> int:
    """计算出来的表和上传的表保持一致：同一个值在上传的表里脱敏了，计算出来的表里也是令牌；上传的表里不脱敏了，这里也是真实值。
    用户在维护页里把某一列开了又关（或关了又开）之后，之前算出来的表里留着旧状态，和上传的表对不上，关联、核对都会失败。
    只按整格相同的字符串值对应，数字和很短的值不动。"""
    guard = privacy.require()
    tables = ws.list_tables()
    derived = [info for info in tables if info.source != "uploaded"]
    if not derived or len(derived) == len(tables):
        return 0
    token_of: dict[str, str] = {}      # 脱敏列里的真实值 -> 令牌
    plain: set[str] = set()            # 没脱敏的列里出现的值
    for info in tables:
        if info.source != "uploaded":
            continue
        frame = ws.df(info.name)
        for name in frame.columns:
            series = frame[name]
            if series.dtype != object and not pd.api.types.is_string_dtype(series):
                continue
            cells = series.dropna().astype(str).drop_duplicates().head(200_000).tolist()
            tokens = [cell for cell in cells if TOKEN_RE.fullmatch(cell)]
            if tokens:
                for cell in tokens:
                    token_of[guard.restore_text(cell)] = cell
            else:
                plain.update(cell for cell in cells if len(cell) >= MIN_LINK_CHARS)
    plain -= set(token_of)             # 同一个值既在脱敏的列又在没脱敏的列里：不动，宁可保持原样
    if not token_of and not plain:
        return 0
    fixed = 0
    for info in derived:
        frame = ws.df(info.name)
        changed = False
        for position, name in enumerate(frame.columns):
            series = frame.iloc[:, position]
            if series.dtype != object and not pd.api.types.is_string_dtype(series):
                continue

            def convert(cell: Any) -> Any:
                if not isinstance(cell, str):
                    return cell
                if TOKEN_RE.fullmatch(cell):
                    real = guard.restore_text(cell)
                    return real if real in plain else cell
                return token_of.get(cell, cell) if len(cell) >= MIN_LINK_CHARS else cell

            converted = series.map(convert).astype(object)
            if not converted.equals(series.astype(object)):
                frame.isetitem(position, converted)
                changed = True
        if changed:
            meta = {key: value for key, value in dataclasses.asdict(info).items() if key not in _KEEP}
            meta["privacy_masked"] = True
            ws.put_dataframe(info.name, frame, **meta)
            fixed += 1
    return fixed


def _sha(path: Path | None) -> str:
    from sheetmate.services.privacy.masked_files import _digest

    return _digest(path) if path is not None else ""


def protect_workspace(ws: Workspace) -> int:
    """对话每一轮开头：还没换成令牌的上传表（上传时识别还没完成，载入的是原文）现在换掉。返回处理了几张表。"""
    if not protecting():
        return 0
    done = _resync_uploaded(ws)
    done += _reconcile_derived(ws)
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
        privacy.log.emit("warn", "还原", f"{path.name}：令牌还原失败，保持原样")
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


def restored_download(path: Path, file_id: str | None = None) -> tuple[Path, bool]:
    """下载给用户的文件：里面的令牌（上传时脱敏的、系统生成的结果文件）统一换回真实值，给一份临时副本。
    返回 (路径, 是不是临时副本)；没有令牌就直接给原文件。file_id 用来把原本是数字的列还原成数字。"""
    if privacy.guard is None or not _has_tokens(path):
        return path, False
    folder = settings.data_dir / "privacy" / "downloads"
    folder.mkdir(parents=True, exist_ok=True)
    copy = folder / f"{secrets.token_hex(8)}{path.suffix}"
    typed = False
    if file_id and path.suffix.lower() in {".xlsx", ".xlsm"}:
        from sheetmate.services.privacy import inplace

        try:
            typed = inplace.restore_types(file_id, path, copy)
        except Exception:      # 按类型还原不了就退回逐个令牌替换（文字照样还原，只是数字列变成文本）
            logger.exception("按列类型还原下载文件失败，改为直接替换令牌：%s", path)
            copy.unlink(missing_ok=True)
    if not typed:
        shutil.copyfile(path, copy)
    restore_file(copy)          # 剩下的令牌（批注、内联文字里的）也换回来
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
    privacy.log.emit("info", "还原", f"{path.name}：令牌已还原", file=path.name)
    return True
