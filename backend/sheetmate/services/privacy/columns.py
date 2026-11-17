"""文件的脱敏维护页：列出这个文件每一列现在是不是脱敏、为什么，用户可以手动改（脱敏 / 不脱敏 / 恢复自动）。

列表用的是自动识别的结果（规则 + 脱敏模型看列头和内容，文件还没识别过会先识别一次）；手动选择记在文件上（见 choices）。
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select

from sheetmate.core.errors import ServiceError
from sheetmate.db.models import FileRecord, PrivacyFile, User
from sheetmate.db.session import session_scope
from sheetmate.privacy import KIND_LABELS, Scope, bind, privacy
from sheetmate.services.privacy import choices, masked_copy
from sheetmate.services.privacy import service as privacy_service

logger = logging.getLogger(__name__)
VALUE_LIMIT = 100_000      # 手动脱敏一列时，登记成已知敏感值的不同取值上限


def _describe(file_id: str) -> dict[str, Any]:
    with session_scope() as db:
        record = db.get(FileRecord, file_id)
        row = db.get(PrivacyFile, file_id)
        if record is None:
            raise ServiceError("文件不存在。")
        profile_sheets = list((record.profile or {}).get("sheets") or [])
        auto = {(sheet["sheet"], column["name"]): column for sheet in (row.sheets if row is not None else None) or []
                for column in sheet["columns"]}
        filename, path = record.filename, record.stored_path
    picks = choices.load(file_id)
    guard = privacy.guard
    try:
        frames = {item.name: item.df for item in masked_copy._read(path)}
    except ServiceError:
        frames = {}
    sheets = []
    for sheet in profile_sheets:
        name = sheet.get("sheet", "")
        candidates = []
        for column in sheet.get("columns", []):
            found = auto.get((name, column["name"]))
            kind, source, reason = (found["kind"], found["source"], found["reason"]) if found else (None, None, None)
            if found is None and guard is not None and privacy.enabled and (named := guard.registry.kind_for(column["name"])):
                kind, source, reason = named, "rules", "其他文件里同名的列已识别为敏感"       # 按列名也会被处理
            candidates.append((kind, source, reason))
        # 开关只有开和关：识别成敏感、但没有任何格子真的被换成令牌的列（比如只写了城市的收货地、没有联系方式的备注），显示成没脱敏
        frame, really = frames.get(name), {}
        if frame is not None and guard is not None:
            by_name = {column["name"]: item[0] for column, item in zip(sheet.get("columns", []), candidates)}
            really = dict(zip((str(column) for column in frame.columns),
                              masked_copy.really_masked(frame, [by_name.get(str(column)) for column in frame.columns])))
        columns = []
        for column, (kind, source, reason) in zip(sheet.get("columns", []), candidates):
            key, pick = (name, column["name"]), picks.get((name, column["name"]))
            if kind is not None and frame is not None and not really.get(column["name"], False):
                kind, source, reason = None, None, None
            choice, auto_kind = (pick[0] if pick else None), kind
            if choice == "mask":
                masked, kind = True, pick[1] or choices.DEFAULT_KIND
            elif choice == "keep":
                masked = False
            else:
                masked = kind is not None
            columns.append({"name": column["name"], "masked": masked, "choice": choice, "auto_kind": auto_kind,
                            "kind": kind if masked else None, "kind_label": KIND_LABELS.get(kind) if masked and kind else None,
                            "source": source, "reason": reason})
        sheets.append({"sheet": name, "rows": int(sheet.get("rows") or 0), "columns": columns})
    # 手动脱敏时可选的类型；对应的规则在设置页里关掉了就不能选（关掉的规则不会脱敏任何东西）
    types = [{"id": "phone", "label": "电话号码", "enabled": guard is not None and not guard._off("phone")},
             {"id": "address", "label": "详细地址", "enabled": guard is not None and not guard._off("address")},
             {"id": "custom", "label": KIND_LABELS["custom"], "enabled": True}]
    return {"enabled": True, "file_id": file_id, "filename": filename, "sheets": sheets, "types": types}


async def list_columns(user: User, file_id: str, conversation_id: str | None = None) -> dict[str, Any]:
    from sheetmate.core.config.privacy_switch import sync_privacy_settings

    privacy_service.start()                                       # 总开关关着时也要把脱敏模块准备好

    if sync_privacy_settings():
        await masked_copy._ready(user, file_id, conversation_id)  # 没识别过的文件先识别（失败会带着原因停下）；总开关关着就不自动识别
    return await asyncio.to_thread(_describe, file_id)


async def save_columns(user: User, file_id: str, changes: list[dict[str, Any]], conversation_id: str | None = None) -> dict[str, Any]:
    state = await list_columns(user, file_id, conversation_id)
    with session_scope() as db:
        record = db.get(FileRecord, file_id)
        path = record.stored_path
    # 每一列自动识别的结果：用户选的和自动结果一样时，不留手动记录
    base = {(entry["sheet"], item["name"]): item["auto_kind"] for entry in state["sheets"] for item in entry["columns"]}
    rows = {entry["sheet"]: entry["rows"] for entry in state["sheets"]}
    saved, masked_now = [], []
    for change in changes:
        key = (str(change.get("sheet", "")), str(change.get("column", "")))
        if key not in base:
            raise ServiceError(f"没有找到「{key[0]}」里的列「{key[1]}」。")
        choice = change.get("choice")
        if choice not in {"mask", "keep", None}:
            raise ServiceError("选择只能是脱敏、不脱敏或恢复自动。")
        automatic, kind = base[key], change.get("kind")
        if kind is not None and kind not in choices.MANUAL_KINDS:
            raise ServiceError("脱敏类型只能是电话号码、详细地址或通用。")
        if choice == "mask" and automatic is not None and kind in (None, automatic, "phone" if automatic == "landline" else automatic):
            choice = None                                        # 和自动识别的结果一样，不留手动记录
        elif choice == "keep" and automatic is None:
            choice = None
        kind = (kind or choices.DEFAULT_KIND) if choice == "mask" else None
        saved.append({"sheet": key[0], "column": key[1], "choice": choice, "kind": kind, "values": rows.get(key[0], 0)})
        if choice == "mask":
            masked_now.append((key, kind))
    previous = await asyncio.to_thread(choices.load, file_id)
    await asyncio.to_thread(choices.save, file_id, saved)
    if masked_now:
        await asyncio.to_thread(_register_values, path, masked_now)
    # 选了「不脱敏」、或把手动脱敏的列恢复成自动识别：之前登记的已知值去掉，不然这些值在别处还是会被换成令牌
    kept_now = [(item["sheet"], item["column"]) for item in saved
                if item["choice"] == "keep" or (item["choice"] is None and (previous.get((item["sheet"], item["column"])) or ("",))[0] == "mask")]
    if kept_now:
        await asyncio.to_thread(_forget_values, path, kept_now)
    await privacy_service.apply_masking(file_id)             # 原文件马上按新的选择改成脱敏版 / 还原
    await asyncio.to_thread(_check_applied, file_id)
    await asyncio.to_thread(_refresh_workspaces, file_id)
    with bind(Scope(conversation_id=conversation_id, user_id=user.id)):
        for item in saved:
            what = {"mask": "手动设为脱敏", "keep": "手动设为不脱敏", None: "恢复为自动识别"}[item["choice"]]
            privacy.log.emit("info", "设置", f"{state['filename']}：「{item['column']}」{what}", file_id=file_id,
                             sheet=item["sheet"], column=item["column"])
    return await asyncio.to_thread(_describe, file_id)


def _register_values(path: str, targets: list[tuple[tuple[str, str], str]]) -> None:
    """新选为脱敏的列：整列的真实值登记成已知敏感值，它们出现在任何地方（别的表、AI 的回复里）都会被换成令牌。"""
    guard = privacy.require()
    sheets = {sheet.name: sheet for sheet in masked_copy._read(path)}
    for (sheet_name, column), kind in targets:
        sheet = sheets.get(sheet_name)
        if sheet is None or column not in sheet.df.columns:
            continue
        values = sheet.df[column].dropna().drop_duplicates().head(VALUE_LIMIT).tolist()
        guard.register_values(kind, values)


def _forget_values(path: str, targets: list[tuple[str, str]]) -> None:
    """选了「不脱敏」的列：之前因为它登记成已知敏感值的内容去掉，不然这些值在别处还是会被换成令牌。"""
    guard = privacy.require()
    sheets = {sheet.name: sheet for sheet in masked_copy._read(path)}
    for sheet_name, column in targets:
        sheet = sheets.get(sheet_name)
        if sheet is not None and column in sheet.df.columns:
            values = sheet.df[column].dropna().drop_duplicates().head(VALUE_LIMIT).tolist()
            # 文件里脱敏的列已经是令牌：先换回真实值，登记的已知值才对得上
            guard.vault.forget([guard.restore_text(value) if isinstance(value, str) else value for value in values])


TABLE_SUFFIXES = {".xlsx", ".xlsm", ".xls", ".csv"}


def _check_applied(file_id: str) -> None:
    """手动改完之后确认原文件真的改好了；没改好要告诉用户，不能显示成已经脱敏。"""
    from sheetmate.services.privacy import inplace

    with session_scope() as db:
        record, row = db.get(FileRecord, file_id), db.get(PrivacyFile, file_id)
        if record is None or not inplace.modifiable(record.kind, Path(record.stored_path)):
            return
        applied = (row.applied or "") if row is not None else ""
    if not applied:
        raise ServiceError("没能把这个文件改成脱敏版，请稍后重试；原文件没有改动。")


def _refresh_workspaces(file_id: str) -> None:
    """用到这个文件的对话：工作区里的表马上按新的选择重新生成（不用等下一轮对话）。"""
    from sheetmate.db.models import FileConversation
    from sheetmate.services.conversations import service as conversation_service
    from sheetmate.services.privacy import workspace as privacy_workspace

    with session_scope() as db:
        record = db.get(FileRecord, file_id)
        ids = {record.conversation_id} if record is not None and record.conversation_id else set()
        ids.update(db.scalars(select(FileConversation.conversation_id).where(FileConversation.file_id == file_id)))
    for conversation_id in ids:
        try:
            privacy_workspace.protect_workspace(conversation_service.workspace(conversation_id))
        except Exception:
            logger.exception("更新对话 %s 里的表失败", conversation_id)


async def viewable_path(user: User, record: FileRecord) -> tuple[Path, str]:
    """用户查看、下载这个文件时给的内容。返回 (路径, 方式)：

    original：原文件（没有脱敏，或交付给用户的结果文件）；
    inplace：原文件本身（已经按脱敏设置改成了脱敏版，里面脱敏的列就是令牌）；
    copy：文件格式没法原地改，给一份只有脱敏的列换成令牌的副本。
    总开关关着时不自动识别，已经脱敏的文件和手动的选择照常生效。交付给用户的结果文件不按自动识别处理（里面要的是真实内容），只有用户在维护页里手动选过的才按选择来。"""
    from sheetmate.core.config.privacy_switch import sync_privacy_settings
    from sheetmate.services.privacy import inplace, masked_files

    path = Path(record.stored_path)
    if path.suffix.lower() not in TABLE_SUFFIXES or not (record.profile or {}).get("sheets"):
        return path, "original"
    privacy_service.start()
    enabled = sync_privacy_settings()
    manual = bool(await asyncio.to_thread(choices.load, record.id))
    if record.kind == "output" and not manual:
        return path, "original"
    if enabled:
        await masked_copy._ready(user, record.id, None)          # 总开关关着时不自动识别，按已有的结果和手动的选择给
    if inplace.modifiable(record.kind, path):
        def applied_state() -> str:
            with session_scope() as db:
                row = db.get(PrivacyFile, record.id)
                return (row.applied or "") if row is not None else ""

        if not applied_state() and (enabled or manual):       # 识别过了但原文件还没改成脱敏版（比如旧文件）：现在改
            await privacy_service.apply_masking(record.id)
        if inplace.in_place(applied_state()):
            return path, "inplace"
    state = await asyncio.to_thread(_describe, record.id)
    if not any(column["masked"] for sheet in state["sheets"] for column in sheet["columns"]):
        return path, "original"
    return await asyncio.to_thread(masked_files.masked_path, record.id, path, view=True), "copy"
