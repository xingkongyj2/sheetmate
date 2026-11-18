"""令牌版的文件副本：AI 能间接碰到原文件的地方（脚本读文件、流程在原文件上改写保留格式）一律给这份副本。

只改敏感单元格，其余原样保留（样式、合并、公式、图表、图片都不动）：
1. 数据区的格子：按列类型换成令牌（和载入工作区时是同一套处理，同一个值得到同一个令牌）；
2. 所有文字节点（共享字符串、内联字符串、批注）：里面夹着的手机号、地址、已知的敏感值换成令牌。
副本按「文件内容 + 脱敏设置 + 已知敏感值」缓存；其中任何一项变了就重新生成。
令牌只在下载时才换回真实值（见 workspace.restored_download），查看和中间环节用的都是令牌。
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, unescape
import re

import pandas as pd

from sheetmate.core.config.settings import settings
from sheetmate.engine.ooxml.errors import OoxmlUnsupported
from sheetmate.engine.ooxml.package import OoxmlPackage
from sheetmate.engine.ooxml.parts import drop_calc_chain
from sheetmate.engine.ooxml.sheetxml import SheetXml, cell_xml
from sheetmate.engine.workbooks.atomic import replace_file
from sheetmate.engine.workbooks.cache import read_cached
from sheetmate.engine.workbooks.reader import SheetData
from sheetmate.engine.workbooks.writer import SheetSpec, write_excel
from sheetmate.privacy import Stats, privacy
from sheetmate.services.privacy import choices, masked_copy
from sheetmate.services.privacy import workspace as pw

logger = logging.getLogger(__name__)

TABLE_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".csv"}
_TEXT_NODE = re.compile(r"(<t\b[^>]*>)(.*?)(</t>)", re.S)
_TEXT_PARTS = re.compile(r"^xl/(sharedStrings\.xml|worksheets/[^/]+\.xml|comments[^/]*\.xml)$")
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _directory() -> Path:
    path = settings.data_dir / "privacy" / "masked_files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _digest(path: Path) -> str:
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def _signature(sha: str, choice_signature: str, view: bool) -> str:
    guard = privacy.require()
    material = f"{sha}|{sorted(guard.active)}|{guard.registry.version}|{guard.vault.known_version}|{choice_signature}|{'view' if view else 'ai'}"
    return hashlib.sha1(material.encode()).hexdigest()[:20]


def masked_path(file_id: str, source: Path, *, view: bool = False) -> Path:
    """原文件的令牌版副本路径；不是表格文件、或脱敏没开时就是原文件本身。

    view：给用户查看、下载的版本——只有脱敏的列（自动识别的和手动选的）换成令牌，其余一律原样，关掉脱敏的列就是原文。
    默认是给 AI 读的版本：除了脱敏的列，其余格子里夹着的明确手机号、地址也会换。"""
    # 给用户看的版本（view）只取决于列的脱敏状态，不受总开关影响；给 AI 读的版本才看总开关
    if source.suffix.lower() not in TABLE_SUFFIXES or not (view or pw.protecting()):
        return source
    privacy.configure(settings.data_dir)
    key = f"{file_id}_{'v' if view else 'a'}{_signature(_digest(source), choices.signature(file_id), view)}"
    suffix = ".xlsx" if source.suffix.lower() == ".xls" else source.suffix.lower()
    target = _directory() / f"{key}{suffix}"
    if target.exists():
        return target
    with _locks_guard:
        lock = _locks.setdefault(key, threading.Lock())
    with lock:
        if target.exists():
            return target
        staging = target.with_name(f".{target.name}.part")
        try:
            _build(file_id, source, staging, view)
            replace_file(staging, target)
        finally:
            staging.unlink(missing_ok=True)
        for old in _directory().glob(f"{file_id}_{'v' if view else 'a'}*"):      # 同一个文件的旧版本副本用不到了
            if old != target:
                old.unlink(missing_ok=True)
    return target


# ---------------------------------------------------------------------------
# 生成
# ---------------------------------------------------------------------------


def _view_frame(file_id: str, sheet_name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """只处理脱敏的列：每一格按这一列的类型换成令牌；没有脱敏的列一格都不动。"""
    guard = privacy.require()
    found = masked_copy._fetch(file_id)
    kinds = masked_copy._kinds(frame, masked_copy._flagged(found[3], sheet_name) if found else {})
    out, stats = frame.copy(), Stats()
    for position, kind in enumerate(kinds):
        if kind:
            series = out.iloc[:, position]
            out.isetitem(position, pd.Series(guard.protect_values(kind, series.tolist(), stats), index=series.index, dtype=object))
    return out


def _build(file_id: str, source: Path, target: Path, view: bool = False) -> None:
    sheets = read_cached(source)
    memo: dict[Any, Any] = {}       # 表里的值和工作表 XML 里的文字大多是同一批：算过的不再算
    masked = [(sheet, _view_frame(file_id, sheet.name, sheet.df) if view else pw.mask_frame(file_id, sheet.name, sheet.df, memo=memo)[0])
              for sheet in sheets]
    suffix = source.suffix.lower()
    if suffix == ".csv":
        if not any(_changed(sheet.df[c], frame[c]) for sheet, frame in masked for c in sheet.df.columns):
            shutil.copyfile(source, target)
            return
        masked[0][1].to_csv(target, index=False, encoding="utf-8-sig")
        return
    if suffix in {".xlsx", ".xlsm"}:
        try:
            _mask_package(source, target, masked, text_nodes=not view, memo=memo)
            return
        except OoxmlUnsupported as exc:
            logger.warning("文件「%s」无法在原文件上换成令牌（%s），改用重新生成的令牌版", source.name, exc)
            privacy.log.emit("warn", "识别", f"{source.name}：无法保留原格式，已重新生成脱敏版（{exc}）")
    write_excel(target, [SheetSpec(name=sheet.name, df=frame) for sheet, frame in masked])


def _changed(original: pd.Series, masked: pd.Series) -> list[int]:
    return [position for position, (old, new) in enumerate(zip(original.tolist(), masked.tolist()))
            if not (old is new or (pd.isna(old) and pd.isna(new)) or str(old) == str(new))]


def _mask_text_nodes(xml: str, memo: dict[Any, Any] | None = None) -> str:
    guard = privacy.require()
    stats, memo = Stats(), {} if memo is None else memo

    def replace(match: re.Match[str]) -> str:
        text = unescape(match.group(2))
        masked = guard.protect_values(None, [text], stats, memo)[0] if len(text) >= 4 else text
        return match.group(0) if masked == text else f"{match.group(1)}{escape(str(masked))}{match.group(3)}"

    return _TEXT_NODE.sub(replace, xml)


def _mask_package(source: Path, target: Path, masked: list[tuple[SheetData, pd.DataFrame]], *, text_nodes: bool = True,
                  memo: dict[Any, Any] | None = None) -> None:
    removed_formula = False
    with OoxmlPackage(source) as package:
        parts = package.sheet_parts()
        for sheet, frame in masked:
            part = parts.get(sheet.name)
            rows, columns = sheet.header.row_index, sheet.header.col_index
            if part is None or rows is None or columns is None or len(rows) != len(sheet.df) or len(columns) != len(sheet.df.columns):
                raise OoxmlUnsupported(f"无法定位「{sheet.name}」里的数据单元格")
            sheet_xml = SheetXml.parse(package.read_text(part))
            touched = False
            for column_position, column in enumerate(sheet.df.columns):
                for row_position in _changed(sheet.df[column], frame[column]):
                    touched = True
                    number, col = int(rows[row_position]) + 1, int(columns[column_position]) + 1
                    record = sheet_xml.row(number)
                    if record is None:
                        raise OoxmlUnsupported(f"第 {number} 行在工作表 XML 中不存在")
                    reference = f"{_letter(col)}{number}"
                    existing = record.cell(col)
                    value = frame[column].iloc[row_position]
                    style = existing.attrs.get("s") if existing else None
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value:
                        try:
                            replacement = cell_xml(reference, style, value)         # 还原成数字的列写回数字
                        except OoxmlUnsupported:
                            replacement = cell_xml(reference, style, str(value))
                    else:
                        replacement = cell_xml(reference, style, str(value))
                    if existing is not None:
                        removed_formula = removed_formula or existing.has_formula
                        existing.attrs, existing.inner = replacement.attrs, replacement.inner
                    else:
                        record.cells.append(replacement)
                        record.cells.sort(key=lambda cell: cell.col)
                    record.touch()
            if touched:
                package.write(part, sheet_xml.serialize())
        for name in package.names() if text_nodes else ():
            if _TEXT_PARTS.match(name):
                text = package.read_text(name)
                if "<t" in text:
                    updated = _mask_text_nodes(text, memo)
                    if updated != text:
                        package.write(name, updated)
        if removed_formula:
            drop_calc_chain(package)
        if package.changed_parts():
            package.save(target)
        else:
            shutil.copyfile(source, target)      # 里面没有敏感内容：副本和原文件一个字节都不差


def _letter(index: int) -> str:
    from openpyxl.utils import get_column_letter

    return get_column_letter(index)
