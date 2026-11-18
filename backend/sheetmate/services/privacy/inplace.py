"""把上传的原文件本身改成脱敏版（反过来也一样），之后就当原文件里的内容是脱敏后的值。

- 脱敏的列（自动识别的、用户在维护页里选的）：每一格换成令牌，写回原文件（xlsx 只改这些单元格，样式、合并、公式、图表都不动）；
- 没有脱敏的列：里面如果有令牌（之前脱敏过、现在关掉了），换回真实值；
- 设置页的总开关和规则开关只管「处理文件时要不要自动先识别、脱敏」，已经脱敏的文件不受影响，手动的选择也不受影响。
只处理上传的输入文件和期望结果；系统生成的结果文件存下来的本来就是令牌版，不用改。xls、格式没法原地改的文件不动原文件，AI 和查看用脱敏副本。
令牌和真实值的对应关系在本机加密的令牌库里，所以不需要另外留一份原文件。
下载时统一还原成真实内容（见 restore_types 和 workspace.restored_download）：存下来的、查看的都是令牌，只有下载给用户的才是真实值。
"""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
import threading
from pathlib import Path
from typing import Any, Callable

import pandas as pd
from sheetmate.core.config.privacy_switch import sync_privacy_settings
from sheetmate.core.config.settings import settings
from sheetmate.db.models import FileRecord, PrivacyFile
from sheetmate.db.session import session_scope
from sheetmate.engine.ooxml.errors import OoxmlUnsupported
from sheetmate.engine.workbooks.atomic import replace_file
from sheetmate.engine.workbooks.reader import _csv_delimiter, read_workbook
from sheetmate.privacy import TOKEN_RE, Stats, privacy
from sheetmate.privacy.guard import FORCED
from sheetmate.services.memory.table_profiles import FILE_MEMORY_KEY, FILE_VERSION_KEY
from sheetmate.services.privacy import choices, masked_copy, masked_files

logger = logging.getLogger(__name__)

KINDS = {"input", "expected"}
SUFFIXES = {".xlsx", ".xlsm", ".csv"}
_NUMBER = re.compile(r"-?\d+(\.\d+)?")
_REGISTER = {"address", "im"}        # 真实值要登记成已知敏感值的类型


def modifiable(kind: str, path: Path) -> bool:
    """这个文件能不能把原文件改成脱敏版：上传的输入文件、期望结果，且是 xlsx / xlsm / csv。"""
    return kind in KINDS and path.suffix.lower() in SUFFIXES


def in_place(applied: str | None) -> bool:
    return bool(applied) and not applied.startswith("copy")


def _signature(enabled: bool, report: list[dict[str, Any]]) -> str:
    guard = privacy.require()
    columns = sorted((sheet["sheet"], column["name"], column["kind"]) for sheet in report for column in sheet["columns"])
    kept = sorted((sheet["sheet"], name) for sheet in report for name in sheet.get("kept", []))
    material = repr([columns, kept, guard.registry.version if enabled else 0])     # 总开关关着时不按列名套用别的文件的识别结果
    return hashlib.sha1(material.encode()).hexdigest()[:20]


def _restore_cell(guard: Any, value: Any, number: bool) -> Any:
    if not isinstance(value, str) or "_" not in value or not TOKEN_RE.search(value):
        return value
    real = guard.restore_text(value)
    if number and _NUMBER.fullmatch(real):
        try:
            return int(real) if "." not in real and len(real.lstrip("-")) <= 15 else float(real)
        except ValueError:
            return real
    return real


def _desired(sheets: list[Any], report: list[dict[str, Any]], enabled: bool, origin: dict[str, Any]) -> list[tuple[Any, pd.DataFrame]]:
    guard = privacy.require()
    stats = Stats()
    out = []
    for sheet in sheets:
        frame = sheet.df.copy()
        flagged = masked_copy._flagged(report, sheet.name)
        if enabled:
            kinds = masked_copy._kinds(sheet.df, flagged)
        else:         # 总开关关着：只看这个文件已有的识别结果和手动的选择，不按列名套用别的文件的结果
            kinds = [masked_copy.column_kind(flagged[str(name)]) if str(name) in flagged else None for name in sheet.df.columns]
        numbers = origin.setdefault(sheet.name, {})
        for position, kind in enumerate(kinds):
            name = str(sheet.df.columns[position])
            series = sheet.df.iloc[:, position]
            if kind:
                if name not in numbers:
                    numbers[name] = "number" if pd.api.types.is_numeric_dtype(series) else "text"
                # 先换回真实值再按现在的类型脱敏：手动改了类型（比如从「通用」改成「电话号码」）时，令牌的前缀才会跟着变
                real = series.map(lambda value, number=numbers[name] == "number": _restore_cell(guard, value, number)).astype(object)
                base = kind.rstrip(FORCED)
                if base in _REGISTER:
                    guard.register_values(base, real.dropna().drop_duplicates().head(100_000).tolist())
                frame.isetitem(position, real.map(lambda value, kind=kind: guard.protect_cell(kind, value, stats)).astype(object))
            else:
                number = numbers.get(name) == "number"
                frame.isetitem(position, series.map(lambda value, number=number: _restore_cell(guard, value, number)).astype(object))
        out.append((sheet, frame))
    return out


def restore_types(file_id: str, source: Path, target: Path) -> bool:
    """下载用：把原地改成脱敏版的 xlsx 里的令牌换回真实值，写到 target；原来是数字的列（比如存成数字的手机号）还原成数字。
    只改令牌所在的格子，其余原样。返回有没有改动（没有改动时 target 不会生成）。"""
    guard = privacy.require()
    with session_scope() as db:
        row = db.get(PrivacyFile, file_id)
        origin = {sheet: dict(columns) for sheet, columns in ((row.origin if row is not None else None) or {}).items()}
    desired = []
    for sheet in read_workbook(source):
        numbers, frame = origin.get(sheet.name, {}), sheet.df.copy()
        for position, name in enumerate(sheet.df.columns):
            number = numbers.get(str(name)) == "number"
            frame.isetitem(position, sheet.df.iloc[:, position].map(lambda value, number=number: _restore_cell(guard, value, number)).astype(object))
        desired.append((sheet, frame))
    if not any(masked_files._changed(sheet.df[column], frame[column]) for sheet, frame in desired for column in sheet.df.columns):
        return False
    masked_files._mask_package(source, target, desired, text_nodes=False)
    return True


def _write_csv(source: Path, target: Path, desired: list[tuple[Any, pd.DataFrame]]) -> None:
    sheet, frame = desired[0]
    rows, columns = sheet.header.row_index, sheet.header.col_index
    if rows is None or columns is None or len(rows) != len(sheet.df) or len(columns) != len(sheet.df.columns):
        raise OoxmlUnsupported("无法定位 CSV 里的数据单元格")
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            text = source.read_text(encoding=encoding, newline="")
            delimiter = _csv_delimiter(source, encoding) or ","
            break
        except UnicodeError:
            continue
    else:
        raise OoxmlUnsupported("无法识别 CSV 的编码")
    grid = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    for column_position, column in enumerate(sheet.df.columns):
        for row_position in masked_files._changed(sheet.df[column], frame[column]):
            row, col = int(rows[row_position]), int(columns[column_position])
            while len(grid[row]) <= col:
                grid[row].append("")
            grid[row][col] = str(frame[column].iloc[row_position])
    buffer = io.StringIO()
    csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n" if "\r\n" in text else "\n").writerows(grid)
    target.write_text(buffer.getvalue(), encoding=encoding, newline="")


_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def sync_file(file_id: str, fingerprint_of: Callable[[dict[str, Any]], str] | None = None) -> str:
    """同一个文件同一时间只改一次（上传后的后台识别和对话开始时的准备可能同时来）。"""
    with _LOCKS_GUARD:
        lock = _LOCKS.setdefault(file_id, threading.Lock())
    with lock:
        return _sync_file(file_id, fingerprint_of)


def _sync_file(file_id: str, fingerprint_of: Callable[[dict[str, Any]], str] | None = None) -> str:
    """让原文件和这个文件的脱敏状态（自动识别的结果 + 用户手动的选择）一致：脱敏的列换成令牌，不脱敏的列里的令牌还原。
    返回 masked（已处理）/ copy（格式没法原地改）/ skip（不用处理）。总开关不影响这里：已有的识别结果和手动的选择始终有效。"""
    privacy.configure(settings.data_dir)       # 总开关关着时没有别的地方会启动脱敏模块（比如重启之后）
    enabled = sync_privacy_settings()          # 只决定要不要按列名套用别的文件的识别结果
    privacy.require()                          # 模块不可用就报错，不能悄悄什么都不做
    with session_scope() as db:
        record, row = db.get(FileRecord, file_id), db.get(PrivacyFile, file_id)
        if record is None or not modifiable(record.kind, Path(record.stored_path)):
            return "skip"
        if row is None:
            if not choices.load(file_id):
                return "skip"
            # 总开关关着、还没自动识别过的文件，用户手动选了列：先建一条空的识别记录（以后打开总开关时会重新自动识别）
            row = PrivacyFile(file_id=file_id, fingerprint="", status="ready", source="manual", sheets=[], origin={})
            db.add(row)
            db.flush()
        path = Path(record.stored_path)
        applied, origin = row.applied or "", {sheet: dict(columns) for sheet, columns in (row.origin or {}).items()}
        raw = list(row.sheets or [])
        profile = dict(record.profile or {})
    if not path.is_file():
        return "skip"
    report = choices.effective(file_id, raw, for_masking=True)
    signature = _signature(enabled, report)
    if applied.removeprefix("copy:") == signature:
        return "skip"
    if not enabled and not applied and not any(sheet["columns"] or sheet.get("kept") for sheet in report):
        return "skip"                                   # 总开关关着、也没有任何手动选择：本来就是真实内容，不处理
    sheets = read_workbook(path)
    desired = _desired(sheets, report, enabled, origin)
    changed = any(masked_files._changed(sheet.df[column], frame[column]) for sheet, frame in desired for column in sheet.df.columns)
    state = "masked"
    if changed:
        staging = path.with_name(f".{path.name}.part")
        try:
            if path.suffix.lower() == ".csv":
                _write_csv(path, staging, desired)
            else:
                masked_files._mask_package(path, staging, desired, text_nodes=False)
            replace_file(staging, path)
        except OoxmlUnsupported as exc:
            logger.warning("文件「%s」无法在原文件上改成脱敏版（%s），原文件保持不变，AI 和查看使用脱敏副本", path.name, exc)
            privacy.log.emit("warn", "识别", f"{record.filename}：无法直接修改原文件，改用脱敏副本（{exc}）",
                             file_id=file_id)
            state = "copy"
        except PermissionError:
            # Windows：原文件一直被别的程序占着（比如正在被读取、被杀毒软件扫描），等了也替换不了。
            # 不算失败：原文件保持不变，AI 和查看改用脱敏副本，保护照样有效
            logger.warning("文件「%s」被占用，无法直接修改原文件，AI 和查看使用脱敏副本", path.name, exc_info=True)
            privacy.log.emit("warn", "识别", f"{record.filename}：文件正被占用，暂时无法直接修改原文件，改用脱敏副本",
                             file_id=file_id)
            state = "copy"
        finally:
            staging.unlink(missing_ok=True)
    from sheetmate.services.files import service as file_service

    new_profile = profile
    if changed and state != "copy":
        fresh = file_service.profile_file(path)
        kept = {key: value for key, value in profile.items() if key not in {"sheets", "file_only", FILE_MEMORY_KEY, FILE_VERSION_KEY}}
        new_profile = {**kept, **fresh}
    with session_scope() as db:
        record, row = db.get(FileRecord, file_id), db.get(PrivacyFile, file_id)
        if state != "copy" and changed:
            record.profile, record.size = new_profile, path.stat().st_size
        row.origin = origin
        if state == "copy":
            row.applied = f"copy:{signature}"[:64]
        else:
            row.applied = signature
            if fingerprint_of is not None and row.source != "manual":
                row.fingerprint = fingerprint_of(new_profile)       # 内容变成令牌后指纹跟着换，不会被当成「文件变了」重新识别
    if changed and state != "copy":
        privacy.log.emit("info", "识别", f"{record.filename}：原文件已按脱敏结果更新", file_id=file_id)
    return state
