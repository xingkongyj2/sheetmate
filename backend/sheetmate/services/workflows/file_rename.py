"""修改文件名：输入文件、期望结果文件、输出和过程文件，并同步绑定了这些文件的流程。

文件只改显示名，原件内容、存放位置和 ID 都不变。流程通过输入绑定记着文件 ID 和当时的文件名，
文件改名后默认同步流程里的来源文件名、说明和已经匹配不上新名字的文件规则（保存为新版本）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from sheetmate.core.errors import Forbidden, ServiceError
from sheetmate.db.models import FileConversation, FileCustomer, FileRecord, RecipeRecord, User
from sheetmate.recipe.editing import sync_renamed_files
from sheetmate.services.files import service as file_service
from sheetmate.services.workflows import service as recipe_service

MAX_RENAMES = 50
_SHEET_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xls", ".csv"})
_KINDS = frozenset({"input", "expected", "output"})


def save_edited_version(db: Session, user: User, record: RecipeRecord, body: dict[str, Any], note: str) -> RecipeRecord:
    """把直接编辑后的流程保存为新版本，并保留原流程的会话归属。

    update_recipe 保存时不带会话；只属于某个会话（未绑定客户）的流程若丢了归属，
    之后在这个会话里就再也改不了它。
    """
    body.setdefault("metadata", {})["version_note"] = note[:200]
    if record.conversation_id:
        body.setdefault("learned_from", {}).setdefault("conversation_id", record.conversation_id)
    return recipe_service.update_recipe(db, user, record.recipe_id, body)


def requested_filename(old: str, requested: Any) -> str:
    """用户给出的新文件名：必须是单纯的文件名，扩展名不能变（没写扩展名就沿用原来的）。"""
    name = str(requested or "").strip()
    if not name:
        raise ValueError("新文件名不能为空。")
    if "/" in name or "\\" in name:
        raise ValueError(f"新文件名「{name}」不能包含路径分隔符。")
    suffix, old_suffix = Path(name).suffix.lower(), Path(old).suffix.lower()
    if suffix in _SHEET_SUFFIXES and suffix != old_suffix:
        raise ValueError(f"不能修改文件扩展名：「{old}」只能保持 {old_suffix or '无扩展名'}。")
    if suffix not in _SHEET_SUFFIXES:
        name += Path(old).suffix
    return file_service.safe_filename(name)


def _scope(db: Session, records: list[FileRecord], conversation_id: str | None, customer_id: str | None) -> tuple[set[str], set[str]]:
    """这些文件可见的客户和会话；用来找出需要同步的流程。"""
    customers: set[str] = {customer_id} if customer_id else set()
    conversations: set[str] = {conversation_id} if conversation_id else set()
    for record in records:
        if record.customer_id:
            customers.add(record.customer_id)
        if record.conversation_id:
            conversations.add(record.conversation_id)
        customers.update(db.scalars(select(FileCustomer.customer_id).where(FileCustomer.file_id == record.id)))
        conversations.update(db.scalars(select(FileConversation.conversation_id).where(FileConversation.file_id == record.id)))
    return customers, conversations


def rename_files(db: Session, user: User, renames: list[dict[str, Any]], *, accessible: Callable[[FileRecord], bool],
                 conversation_id: str | None = None, customer_id: str | None = None,
                 sync_recipes: bool = True) -> dict[str, Any]:
    """批量改文件名；任何一项不合法时整体不改。返回每个文件的结果和被同步的流程。"""
    if not isinstance(renames, list) or not renames:
        raise ServiceError("renames 至少要有一项：{file_id, new_name}。")
    if len(renames) > MAX_RENAMES:
        raise ServiceError(f"一次最多修改 {MAX_RENAMES} 个文件名。")
    ids = [str(item.get("file_id") or "").strip() if isinstance(item, dict) else "" for item in renames]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ServiceError("每项 renames 都需要真实且不重复的 file_id（可用 list_files 查看）。")
    planned: list[tuple[FileRecord, str]] = []
    for file_id, item in zip(ids, renames):
        record = file_service.get_file(db, file_id)
        if not accessible(record):
            raise Forbidden(f"文件「{record.filename}」不属于当前客户或会话。")
        if record.kind not in _KINDS:
            raise ServiceError(f"文件「{record.filename}」的类型不支持改名。")
        try:
            planned.append((record, requested_filename(record.filename, item.get("new_name"))))
        except ValueError as exc:
            raise ServiceError(f"文件「{record.filename}」：{exc}") from exc
    targets = [name.casefold() for _, name in planned]
    if len(set(targets)) != len(targets):
        raise ServiceError("多个文件被改成了同一个名字，请给每个文件不同的新名字。")

    customers, conversations = _scope(db, [record for record, _ in planned], conversation_id, customer_id)
    results: list[dict[str, Any]] = []
    for record, name in planned:
        old = record.filename
        if name != old:
            # 与同一客户/会话里其他文件重名时自动追加（1）、（2），不覆盖，也不拒绝。
            record.filename = file_service.unique_filename(
                db, name, customer_id=record.customer_id or customer_id, conversation_id=record.conversation_id or conversation_id,
                exclude_file_id=record.id)
            db.flush()
        results.append({"file_id": record.id, "kind": record.kind, "old": old, "new": record.filename,
                        "changed": record.filename != old, "requested": name})
    renamed = {item["file_id"]: (item["old"], item["new"]) for item in results if item["changed"]}

    synced: list[dict[str, Any]] = []
    if sync_recipes and renamed:
        seen: set[str] = set()
        records = [record for owner in customers for record in recipe_service.latest_records(db, customer_id=owner)]
        records += [record for owner in conversations for record in recipe_service.latest_records(db, conversation_id=owner)]
        for record in records:
            if record.recipe_id in seen:
                continue
            seen.add(record.recipe_id)
            recipe = recipe_service.record_to_recipe(record)
            changes = sync_renamed_files(recipe, renamed)
            if not changes:
                continue
            try:
                recipe_service.require_recipe_access(db, user, recipe)
            except ServiceError:
                continue  # 用户无权修改的流程不动
            saved = save_edited_version(db, user, record, recipe.to_dict(), "文件改名同步：" + "；".join(changes))
            synced.append({"recipe_id": saved.recipe_id, "name": saved.name, "version": saved.version, "changes": changes,
                           "customer_id": saved.customer_id, "description": recipe.description, "flow": recipe.flow()})
    return {"files": results, "recipes": synced}
