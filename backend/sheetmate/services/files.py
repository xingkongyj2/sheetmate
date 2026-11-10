"""文件：上传、画像、预览、加载、导出，以及独立的客户/会话引用。

路径仅代表最初存放位置，不决定归属。客户和会话共享原件，最后一个引用解除后才回收。
"""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any

import openpyxl
from sqlalchemy import event, func, or_, select
from sqlalchemy.orm import Session

from sheetmate.config import settings
from sheetmate.db.models import Conversation, Customer, FileConversation, FileCustomer, FileRecipe, FileRecord, RecipeRecord, Run, User
from sheetmate.engine.file_copies import temporary_file_copies
from sheetmate.engine.images import IMAGE_SUFFIXES, image_profile
from sheetmate.engine.profile import profile_workbook
from sheetmate.engine.reader import SUPPORTED_SUFFIXES, ReadError, SheetData, read_workbook
from sheetmate.engine.sqlutil import frame_rows, sanitize_table_name
from sheetmate.engine.table_memory import restore_memory, seed_profile
from sheetmate.engine.workspace import TableInfo, Workspace
from sheetmate.engine.writer import SheetSpec, write_excel
from sheetmate.recipe.schema import file_pattern_from_name
from sheetmate.services.auth import can_access_customer, require_customer
from sheetmate.services.errors import Forbidden, NotFound, ServiceError
from sheetmate.services.table_memory import FILE_MEMORY_KEY, FILE_VERSION_KEY, content_digest, file_version

logger = logging.getLogger(__name__)


@event.listens_for(Session, "after_commit")
def _remove_deleted_files(db: Session) -> None:
    paths = set(db.info.pop("deleted_file_paths", set())) | set(db.info.pop("deleted_aux_paths", set()))
    for path in paths:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("无法清理已解除所有关联的文件 %s", path, exc_info=True)
    for callback in db.info.pop("after_commit_cleanup", []):
        try:
            callback()
        except Exception:
            logger.warning("提交后清理数据失败", exc_info=True)


@event.listens_for(Session, "after_rollback")
def _cancel_deleted_files(db: Session) -> None:
    db.info.pop("deleted_file_paths", None)
    db.info.pop("deleted_aux_paths", None)
    db.info.pop("after_commit_cleanup", None)


def defer_after_commit(db: Session, callback) -> None:
    """Run non-transactional storage cleanup only after the owning DB transaction commits."""
    db.info.setdefault("after_commit_cleanup", []).append(callback)

KIND_LABELS = {"input": "输入文件", "expected": "期望结果", "output": "生成结果", "doc": "处理说明"}
_UNSAFE = re.compile(r"[^\w一-鿿.\-()（） ]+")
_COPY_SUFFIX = re.compile(r"^(.*?)[（(]([1-9]\d*)[）)]$")


def safe_filename(name: str) -> str:
    """去掉路径和危险字符，只保留文件名本身。"""
    base = Path(str(name or "file")).name
    base = _UNSAFE.sub("_", base).strip(" .") or "file"
    return base[-120:]


def current_file_condition():
    """已被修订结果替代的旧输出只供历史追溯，不占用当前文件列表和名称。"""
    return func.coalesce(FileRecord.profile["delivery_status"].as_string(), "") != "superseded"


def unique_filename(db: Session, filename: str, *, customer_id: str | None = None,
                    conversation_id: str | None = None, user_id: int | None = None,
                    exclude_file_id: str | None = None) -> str:
    """当前可见范围内重名时，在扩展名前依次追加（1）、（2），不覆盖原文件。"""
    name = safe_filename(filename)
    customer_ids = {customer_id} - {None}
    conversation_ids = {conversation_id} - {None}
    if exclude_file_id:
        existing = db.get(FileRecord, exclude_file_id)
        if existing is not None:
            customer_ids.update(db.scalars(select(FileCustomer.customer_id).where(FileCustomer.file_id == existing.id)))
            conversation_ids.update(db.scalars(select(FileConversation.conversation_id).where(FileConversation.file_id == existing.id)))
            if existing.customer_id:
                customer_ids.add(existing.customer_id)
            if existing.conversation_id:
                conversation_ids.add(existing.conversation_id)
    if conversation_ids:
        customer_ids.update(owner for owner in db.scalars(select(Conversation.customer_id).where(
            Conversation.id.in_(conversation_ids))) if owner)
    if customer_ids:
        # 客户移除过的文件可能仍在旧会话中；新客户文件也会进入这些会话的可见范围。
        conversation_ids.update(db.scalars(select(Conversation.id).where(Conversation.customer_id.in_(customer_ids))))
    conditions = [customer_file_condition(owner) for owner in customer_ids]
    if conversation_ids:
        linked = select(FileConversation.file_id).where(FileConversation.conversation_id.in_(conversation_ids))
        conditions.extend([FileRecord.conversation_id.in_(conversation_ids), FileRecord.id.in_(linked)])
    if not conditions:
        conditions.append(FileRecord.created_by == user_id)
    query = select(FileRecord.filename).where(or_(*conditions), current_file_condition())
    if exclude_file_id:
        query = query.where(FileRecord.id != exclude_file_id)
    # 会话在同一个事务里可登记多个结果，先刷入前面的记录，后面的文件名才能正确顺延。
    db.flush()
    occupied = {item.casefold() for item in db.scalars(query)}
    if name.casefold() not in occupied:
        return name
    suffix = Path(name).suffix
    stem = name[:-len(suffix)] if suffix else name
    numbered = _COPY_SUFFIX.fullmatch(stem)
    number = int(numbered[2]) + 1 if numbered else 1
    stem = numbered[1] if numbered else stem
    while True:
        addition = f"（{number}）"
        candidate = f"{stem[:120 - len(addition) - len(suffix)]}{addition}{suffix}"
        if candidate.casefold() not in occupied:
            return candidate
        number += 1


def owner_dir(customer_id: str | None, conversation_id: str | None) -> str:
    if customer_id:
        return customer_id
    if conversation_id:
        return f"conv_{conversation_id}"
    return "shared"


def save_upload(
    db: Session,
    *,
    data: bytes,
    filename: str,
    user: User,
    customer_id: str | None = None,
    conversation_id: str | None = None,
    kind: str = "input",
) -> FileRecord:
    original_name = safe_filename(filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES | IMAGE_SUFFIXES:
        raise ServiceError(f"不支持的文件类型 {suffix or '(无扩展名)'}，请上传 xlsx / xlsm / xls / csv 或 PNG / JPG / JPEG / WebP / GIF 图片")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise ServiceError(f"文件超过 {settings.max_upload_mb} MB")
    if not data:
        raise ServiceError("文件是空的")
    name = unique_filename(db, original_name, customer_id=customer_id, conversation_id=conversation_id, user_id=user.id)
    file_id = f"f_{uuid.uuid4().hex[:10]}"
    folder = settings.uploads_dir / owner_dir(customer_id, conversation_id)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{file_id}_{name}"
    path.write_bytes(data)
    try:
        profile = profile_file(path)
    except ReadError as exc:
        path.unlink(missing_ok=True)
        raise ServiceError(str(exc)) from exc
    profile["original_filename"] = filename
    record = FileRecord(
        id=file_id, customer_id=customer_id, conversation_id=conversation_id, filename=name, stored_path=str(path),
        size=len(data), kind=kind if kind in KIND_LABELS else "input", profile=profile, created_by=user.id,
    )
    db.add(record)
    db.flush()
    if customer_id:
        retain_for_customer(db, record, customer_id)
    if conversation_id:
        retain_for_conversation(db, record, conversation_id)
    return record


def register_output(db: Session, *, path: Path, filename: str, customer_id: str | None, conversation_id: str | None,
                    run_id: str | None, user_id: int | None, delivery_status: str = "final") -> FileRecord:
    """登记系统生成的结果文件。"""
    try:
        profile = profile_file(path)
    except ReadError:
        profile = {}
    profile["original_filename"] = filename
    profile["delivery_status"] = delivery_status
    profile["intermediate"] = delivery_status == "intermediate"
    name = unique_filename(db, filename, customer_id=customer_id, conversation_id=conversation_id, user_id=user_id)
    record = FileRecord(
        id=f"f_{uuid.uuid4().hex[:10]}", customer_id=customer_id, conversation_id=conversation_id, run_id=run_id,
        filename=name, stored_path=str(path), size=path.stat().st_size, kind="output",
        profile=profile, created_by=user_id,
    )
    db.add(record)
    db.flush()
    if customer_id:
        retain_for_customer(db, record, customer_id)
    if conversation_id:
        retain_for_conversation(db, record, conversation_id)
    return record


def profile_file(path: Path) -> dict[str, Any]:
    """文件画像（不含数据预览，预览按需读取）。"""
    if path.suffix.lower() in IMAGE_SUFFIXES:
        return {**image_profile(path), FILE_VERSION_KEY: file_version(path)}
    try:
        sheets = _read_file_copy(path)
    except ReadError as exc:
        # 空白或只含样式的合法工作簿仍能交给脚本处理，损坏文件不能借此绕过校验。
        if path.suffix.lower() not in {".xlsx", ".xlsm"} or "没有可读取的数据" not in str(exc):
            raise
        try:
            with temporary_file_copies() as copies:
                workbook = openpyxl.load_workbook(copies.file_copy(path), read_only=True, data_only=False)
                workbook.close()
        except Exception as invalid:
            raise exc from invalid
        return {"sheets": [], "file_only": True, FILE_VERSION_KEY: file_version(path)}
    profile = profile_workbook(sheets, preview_rows=0)
    for sheet in profile["sheets"]:
        sheet.pop("preview", None)
    profile[FILE_VERSION_KEY] = file_version(path)
    return profile


def _read_file_copy(path: Path) -> list[SheetData]:
    with temporary_file_copies() as files:
        return read_workbook(files.file_copy(path))


def file_dict(record: FileRecord) -> dict[str, Any]:
    sheets = (record.profile or {}).get("sheets", [])
    image_type = (record.profile or {}).get("media_type")
    return {
        "file_id": record.id,
        "filename": record.filename,
        "kind": record.kind,
        "kind_label": KIND_LABELS.get(record.kind, record.kind),
        "size": record.size,
        "customer_id": record.customer_id,
        "conversation_id": record.conversation_id,
        "run_id": record.run_id,
        "created_at": record.created_at.isoformat() if record.created_at else None,
        "sheets": [
            {"sheet": s["sheet"], "rows": s["rows"], "cols": s["cols"], "header": s.get("header"),
             "columns": [{"name": c["name"], "dtype": c["dtype"]} for c in s.get("columns", [])]}
            for s in sheets
        ],
        "download_url": f"/api/files/{record.id}/download",
        "media_type": file_media_type(record),
        "preview_url": f"/api/files/{record.id}/image" if image_type in {
            "image/png", "image/jpeg", "image/webp", "image/gif",
        } else None,
        "intermediate": bool((record.profile or {}).get("intermediate")),
        "delivery_status": (record.profile or {}).get("delivery_status"),
        "superseded_by_file_ids": (record.profile or {}).get("superseded_by_file_ids", []),
        "step_id": (record.profile or {}).get("step_id"),
        "step_name": (record.profile or {}).get("step_name"),
    }


def finalize_delivery(db: Session, *, conversation_id: str, candidate_file_ids: list[str],
                      final_file_ids: list[str], summary: str,
                      replace_file_ids: list[str] | None = None) -> list[str]:
    """确认交付后替换指定旧结果；独立文件和执行记录保留供历史追溯。"""
    candidates: dict[str, FileRecord] = {}
    finals = list(dict.fromkeys(final_file_ids))
    replacements = list(dict.fromkeys(replace_file_ids or []))
    if not set(finals) <= set(candidate_file_ids) or (replacements and not finals):
        raise ServiceError("尚未确认新的正式结果，不能替换已有文件。")
    for file_id in dict.fromkeys(candidate_file_ids):
        record = db.get(FileRecord, file_id)
        if record is None or record.kind != "output" or record.conversation_id != conversation_id:
            raise ServiceError("无法确认本轮生成文件的归属，尚未交付结果。")
        if file_id in finals and not Path(record.stored_path).is_file():
            raise ServiceError("新的正式结果文件不存在，不能替换已有文件。")
        candidates[file_id] = record
    conversation = db.get(Conversation, conversation_id) if replacements else None
    replacements_to_check: list[FileRecord] = []
    for file_id in replacements:
        record = db.get(FileRecord, file_id)
        profile = (record.profile or {}) if record else {}
        if (record is None or conversation is None or record.kind != "output" or file_id in candidates
                or profile.get("intermediate") or profile.get("delivery_status") not in {None, "final"}
                or not can_access_in_conversation(db, record, conversation_id=conversation_id,
                                                  customer_id=conversation.customer_id)
                or not Path(record.stored_path).is_file()):
            raise ServiceError("只能替换当前会话或客户可访问的已交付结果文件。")
        replacements_to_check.append(record)
    retired = [record for record in replacements_to_check
               if _replacement_visible_to_all_references(db, record, [candidates[file_id] for file_id in finals])]
    # 所有归属与文件状态检查通过后再修改，避免某一目标无效时部分退休。
    for file_id, record in candidates.items():
        record.profile = {**(record.profile or {}), "delivery_status": "final" if file_id in finals else "intermediate",
                          "intermediate": file_id not in finals, "delivery_summary": summary[:1000]}
    for record in retired:
        record.profile = {**(record.profile or {}), "delivery_status": "superseded", "intermediate": True,
                          "superseded_by_file_ids": finals}
    if retired:
        # 旧结果退出名称占用后恢复工具声明的文件名；输入原件及其他独立结果仍参与避重。
        db.flush()
        for file_id in finals:
            record = candidates[file_id]
            record.filename = unique_filename(db, (record.profile or {}).get("original_filename") or record.filename,
                customer_id=record.customer_id, conversation_id=conversation_id, user_id=record.created_by,
                exclude_file_id=record.id)
    return [record.id for record in retired]


def _replacement_visible_to_all_references(db: Session, record: FileRecord, finals: list[FileRecord]) -> bool:
    """不向其他范围传播本轮数据；无法访问新结果的引用方继续保留其当前结果。"""
    customer_ids = {record.customer_id} - {None}
    customer_ids.update(db.scalars(select(FileCustomer.customer_id).where(FileCustomer.file_id == record.id)))
    customer_ids.update(db.scalars(select(FileRecipe.customer_id).where(
        FileRecipe.file_id == record.id, FileRecipe.excluded.is_(False))))
    if any(not can_access_in_customer(db, final, customer_id)
           for customer_id in customer_ids if db.get(Customer, customer_id) is not None for final in finals):
        return False
    conversation_ids = {record.conversation_id} - {None}
    conversation_ids.update(db.scalars(select(FileConversation.conversation_id).where(
        FileConversation.file_id == record.id)))
    for conversation_id in conversation_ids:
        conversation = db.get(Conversation, conversation_id)
        if conversation is not None and any(not can_access_in_conversation(db, final,
                conversation_id=conversation_id, customer_id=conversation.customer_id) for final in finals):
            return False
    return True


def file_media_type(record: FileRecord) -> str:
    media_type = (record.profile or {}).get("media_type")
    if media_type in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
        return media_type
    return {
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
        ".xls": "application/vnd.ms-excel",
        ".csv": "text/csv",
    }.get(Path(record.filename).suffix.lower(), "application/octet-stream")


def get_file(db: Session, file_id: str) -> FileRecord:
    record = db.get(FileRecord, file_id)
    if record is None or not Path(record.stored_path).is_file():
        raise NotFound("文件不存在或已被删除")
    return record


def retain_for_conversation(db: Session, record: FileRecord, conversation_id: str) -> None:
    """在已完成范围检查后保存引用，不复制文件，也不更换文件 ID。"""
    key = (record.id, conversation_id)
    if db.get(FileConversation, key) is None:
        db.add(FileConversation(file_id=record.id, conversation_id=conversation_id))
        db.flush()


def retain_for_customer(db: Session, record: FileRecord, customer_id: str) -> None:
    if not can_access_in_customer(db, record, customer_id):
        name = unique_filename(db, record.filename, customer_id=customer_id, exclude_file_id=record.id)
        if name != record.filename:
            record.profile = {"original_filename": record.filename, **(record.profile or {})}
            record.filename = name
    # 升级前的首选客户也必须先保存引用，后续单侧删除才能独立。
    for owner_id in {record.customer_id, customer_id} - {None}:
        if db.get(FileCustomer, (record.id, owner_id)) is None:
            db.add(FileCustomer(file_id=record.id, customer_id=owner_id))
            db.flush()
    if record.customer_id is None:
        record.customer_id = customer_id
        db.flush()


def retain_for_recipe(db: Session, record: FileRecord, customer_id: str, recipe_id: str) -> None:
    """在完成归属校验后添加流程引用，不复制文件。"""
    retain_for_customer(db, record, customer_id)
    link = db.get(FileRecipe, (record.id, recipe_id))
    if link is None:
        db.add(FileRecipe(file_id=record.id, recipe_id=recipe_id, customer_id=customer_id, excluded=False))
    else:
        link.excluded = False
    db.flush()


def _recipe_file_ids(db: Session, customer_id: str, recipe_id: str) -> set[str]:
    ids: set[str] = set()
    exact_inputs: set[str] = set(db.scalars(select(FileRecipe.file_id).where(
        FileRecipe.customer_id == customer_id, FileRecipe.recipe_id == recipe_id)))
    resolved_aliases: set[str] = set()
    records = list(db.scalars(select(RecipeRecord).where(RecipeRecord.recipe_id == recipe_id,
                                                        RecipeRecord.customer_id == customer_id)))
    for record in records:
        bindings = (record.body or {}).get("learned_from", {}).get("input_bindings", {})
        for alias, value in bindings.items() if isinstance(bindings, dict) else []:
            file_id = value if isinstance(value, str) else value.get("file_id") if isinstance(value, dict) else None
            if file_id:
                exact_inputs.add(str(file_id))
                resolved_aliases.add(alias)
    runs = list(db.scalars(select(Run).where(Run.recipe_id == recipe_id, Run.customer_id == customer_id)))
    for run in runs:
        exact_inputs.update(run.input_file_ids or [])
        for item in (run.validation or {}).get("inputs", []):
            file_id = item.get("file_id") or (item.get("source") or {}).get("file_id") if isinstance(item, dict) else None
            if file_id:
                exact_inputs.add(str(file_id))
                resolved_aliases.add(item.get("alias"))
        if run.output_file_id:
            ids.add(run.output_file_id)
    ids.update(exact_inputs)
    exact_names = set(db.scalars(select(FileRecord.filename).where(
        FileRecord.id.in_(exact_inputs), FileRecord.kind != "output"))) if exact_inputs else set()
    # 旧流程缺少原件 ID 时才回退到名称；同名文件只选一个，优先流程来源会话。
    for record in records:
        inputs = (record.body or {}).get("inputs", [])
        origin = record.conversation_id or (record.body or {}).get("learned_from", {}).get("conversation_id")
        for item in inputs:
            if not isinstance(item, dict) or not item.get("source_file"):
                continue
            if item.get("alias") in resolved_aliases or item["source_file"] in exact_names:
                continue
            if len(inputs) == 1 and any(run.input_file_ids for run in runs):
                continue
            candidates = list(db.scalars(select(FileRecord).where(customer_file_condition(customer_id),
                                                                 FileRecord.filename == item["source_file"],
                                                                 FileRecord.kind != "output")))
            candidates = [candidate for candidate in candidates if Path(candidate.stored_path).is_file()]
            linked = set(db.scalars(select(FileConversation.file_id).where(
                FileConversation.conversation_id == origin))) if origin else set()
            candidates.sort(key=lambda candidate: (0 if origin and candidate.conversation_id == origin
                                                    else 1 if candidate.id in linked else 2,
                                                    candidate.created_at, candidate.id))
            if candidates:
                ids.add(candidates[0].id)
                resolved_aliases.add(item.get("alias"))
                exact_names.add(item["source_file"])
    if runs:
        ids.update(db.scalars(select(FileRecord.id).where(FileRecord.run_id.in_([run.id for run in runs]))))
    return ids


def recipe_files(db: Session, customer_id: str, recipe_id: str, *, primary_recipe_id: str | None = None) -> list[FileRecord]:
    """用流程引用、执行记录和原始输入绑定还原完整资料；旧公共资料只归主流程。"""
    available = list_files(db, customer_id=customer_id)
    links = list(db.scalars(select(FileRecipe).where(FileRecipe.customer_id == customer_id)))
    own = {link.file_id: link for link in links if link.recipe_id == recipe_id}
    ids = _recipe_file_ids(db, customer_id, recipe_id) | {file_id for file_id, link in own.items() if not link.excluded}
    if recipe_id == primary_recipe_id:
        attributed = {link.file_id for link in links}
        recipe_ids = set(db.scalars(select(RecipeRecord.recipe_id).where(RecipeRecord.customer_id == customer_id)))
        for other in recipe_ids:
            attributed.update(_recipe_file_ids(db, customer_id, other))
        ids.update(record.id for record in available if record.id not in attributed)
    # 固定旧资料的归属，之后更换主流程也不会把这些文件搬到另一个流程。
    for record in available:
        if record.id in ids and record.id not in own:
            db.add(FileRecipe(file_id=record.id, recipe_id=recipe_id, customer_id=customer_id, excluded=False))
    db.flush()
    return [record for record in available if record.id in ids and not (own.get(record.id) and own[record.id].excluded)]


def remove_from_recipe(db: Session, record: FileRecord, user: User, customer_id: str, recipe_id: str) -> bool:
    from sheetmate.services import recipes as recipe_service
    require_customer(db, user, customer_id)
    recipe_service.resolve_customer_recipe(db, customer_id, recipe_id)
    if not can_access_in_customer(db, record, customer_id):
        raise ServiceError("这个文件不属于指定客户")
    link = db.get(FileRecipe, (record.id, recipe_id))
    if link is None:
        db.add(FileRecipe(file_id=record.id, recipe_id=recipe_id, customer_id=customer_id, excluded=True))
    else:
        link.excluded = True
    db.flush()
    return False


def customer_file_condition(customer_id: str):
    linked = select(FileCustomer.file_id).where(FileCustomer.customer_id == customer_id)
    return or_(FileRecord.customer_id == customer_id, FileRecord.id.in_(linked))


def can_access_in_customer(db: Session, record: FileRecord, customer_id: str) -> bool:
    return record.customer_id == customer_id or db.get(FileCustomer, (record.id, customer_id)) is not None


def can_access_in_conversation(db: Session, record: FileRecord, *, conversation_id: str,
                               customer_id: str | None = None) -> bool:
    return (record.conversation_id == conversation_id
            or db.get(FileConversation, (record.id, conversation_id)) is not None
            or bool(customer_id and can_access_in_customer(db, record, customer_id)))


def can_access_file(db: Session, user: User, record: FileRecord) -> bool:
    if user.role == "admin" or record.created_by == user.id:
        return True
    if record.customer_id and can_access_customer(db, user, record.customer_id):
        return True
    if any(can_access_customer(db, user, customer_id) for customer_id in db.scalars(
        select(FileCustomer.customer_id).where(FileCustomer.file_id == record.id)
    )):
        return True
    linked = select(FileConversation.conversation_id).where(FileConversation.file_id == record.id)
    return db.scalar(select(Conversation.id).where(
        Conversation.user_id == user.id,
        or_(Conversation.id == record.conversation_id, Conversation.id.in_(linked)),
    ).limit(1)) is not None


def retain_customer_files(db: Session, customer_id: str, *, conversation_id: str | None = None) -> None:
    """在解绑/删除前保留各现存会话能使用的客户资料，兼容升级前的单归属记录。"""
    conversation_ids = ([conversation_id] if conversation_id else list(db.scalars(
        select(Conversation.id).where(Conversation.customer_id == customer_id))))
    if not conversation_ids:
        return
    file_ids = list(db.scalars(select(FileRecord.id).where(customer_file_condition(customer_id))))
    if not file_ids:
        return
    existing = set(db.execute(select(FileConversation.file_id, FileConversation.conversation_id).where(
        FileConversation.file_id.in_(file_ids), FileConversation.conversation_id.in_(conversation_ids),
    )))
    db.add_all([FileConversation(file_id=file_id, conversation_id=conv_id)
                for file_id in file_ids for conv_id in conversation_ids if (file_id, conv_id) not in existing])
    db.flush()


def delete_if_unowned(db: Session, record: FileRecord) -> bool:
    """最后一个客户/会话引用释放后才回收原件；回滚事务时原件仍在。"""
    if record.customer_id or record.conversation_id:
        return False
    if db.scalar(select(FileCustomer.file_id).where(FileCustomer.file_id == record.id).limit(1)):
        return False
    if db.scalar(select(FileConversation.file_id).where(FileConversation.file_id == record.id).limit(1)):
        return False
    # 流程输入是独立引用；流程仍存在时，不能回收它使用的原件。
    if db.scalar(select(FileRecipe.file_id).where(FileRecipe.file_id == record.id).limit(1)):
        return False
    if record.run_id and db.scalar(select(Run.id).where(Run.id == record.run_id)) is not None:
        return False

    def contains_file_id(value: Any) -> bool:
        if isinstance(value, dict):
            return value.get("file_id") == record.id or any(contains_file_id(item) for item in value.values())
        if isinstance(value, (list, tuple)):
            return any(contains_file_id(item) for item in value)
        return value == record.id

    # Legacy runs/recipes may hold file IDs in JSON without a normalized link row.
    if any(record.id in (run.input_file_ids or []) or run.output_file_id == record.id
           or contains_file_id(run.steps or []) or contains_file_id(run.validation or {})
           for run in db.scalars(select(Run))):
        return False
    if any(contains_file_id(recipe.body or {}) for recipe in db.scalars(select(RecipeRecord))):
        return False
    path = record.stored_path
    db.delete(record)
    db.flush()
    # 老版本可能登记过指向同一路径的多个结果，其他记录仍持有时不能删原件。
    if db.scalar(select(FileRecord.id).where(FileRecord.stored_path == path).limit(1)) is None:
        db.info.setdefault("deleted_file_paths", set()).add(path)
    return True


def remove_from_customer(db: Session, record: FileRecord, user: User, customer_id: str) -> bool:
    require_customer(db, user, customer_id)
    if not can_access_in_customer(db, record, customer_id):
        raise ServiceError("这个文件不属于指定客户")
    # 兼容已有会话尚未显式加载的客户文件：客户删除不能破坏它们原有的输入范围。
    for conv_id in db.scalars(select(Conversation.id).where(Conversation.customer_id == customer_id)):
        retain_for_conversation(db, record, conv_id)
    release_customer_reference(db, record, customer_id)
    db.flush()
    return delete_if_unowned(db, record)


def release_customer_reference(db: Session, record: FileRecord, customer_id: str) -> None:
    link = db.get(FileCustomer, (record.id, customer_id))
    if link is not None:
        db.delete(link)
    db.flush()
    if record.customer_id == customer_id:
        record.customer_id = db.scalar(select(FileCustomer.customer_id).where(
            FileCustomer.file_id == record.id, FileCustomer.customer_id != customer_id,
        ).order_by(FileCustomer.customer_id).limit(1))
        db.flush()


def remove_from_conversation(db: Session, record: FileRecord, user: User, conversation_id: str) -> bool:
    conv = db.get(Conversation, conversation_id)
    if conv is None or (conv.user_id != user.id and user.role != "admin"):
        raise Forbidden("你没有权限修改这个会话的文件")
    if record.conversation_id != conversation_id and db.get(FileConversation, (record.id, conversation_id)) is None:
        raise ServiceError("这个文件不属于指定会话")
    link = db.get(FileConversation, (record.id, conversation_id))
    if link is not None:
        db.delete(link)
    if record.conversation_id == conversation_id:
        record.conversation_id = None
    db.flush()
    return delete_if_unowned(db, record)


def preview(record: FileRecord, sheet: str | None = None, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    if Path(record.stored_path).suffix.lower() in IMAGE_SUFFIXES:
        raise ServiceError("图片不支持表格预览，请使用图片预览")
    sheets = _read_file_copy(Path(record.stored_path))
    chosen = next((s for s in sheets if s.name == sheet), sheets[0]) if sheet else sheets[0]
    part = chosen.df.iloc[offset : offset + limit]
    return {
        "file_id": record.id, "filename": record.filename, "sheet": chosen.name, "sheets": [s.name for s in sheets],
        "total": int(len(chosen.df)), "offset": offset, "limit": limit,
        "columns": [str(c) for c in chosen.df.columns], "rows": frame_rows(part), "header": chosen.header.to_dict(),
    }


def list_files(db: Session, *, customer_id: str | None = None, conversation_id: str | None = None,
               kinds: tuple[str, ...] | None = None, available_only: bool = True,
               include_superseded: bool = False) -> list[FileRecord]:
    conds = []
    if customer_id:
        conds.append(customer_file_condition(customer_id))
    if conversation_id:
        linked = select(FileConversation.file_id).where(FileConversation.conversation_id == conversation_id)
        conds.extend([FileRecord.conversation_id == conversation_id, FileRecord.id.in_(linked)])
    if not conds:
        return []
    stmt = select(FileRecord).where(or_(*conds)).order_by(FileRecord.created_at.desc())
    if not include_superseded:
        stmt = stmt.where(current_file_condition())
    if kinds:
        stmt = stmt.where(FileRecord.kind.in_(kinds))
    records = [record for record in db.scalars(stmt) if not available_only or Path(record.stored_path).is_file()]
    if conversation_id:
        for record in records:
            retain_for_conversation(db, record, conversation_id)
    return records


# ---------------------------------------------------------------------------
# 工作区
# ---------------------------------------------------------------------------


def table_base_name(filename: str) -> str:
    """表名去掉日期月份，让每个月的同类文件得到同一个表名（Recipe 的输入名因此稳定）：运费明细_2026-09.xlsx → 运费明细"""
    stem = file_pattern_from_name(filename).replace("*", "").strip(" _-")
    return stem or Path(filename).stem


def load_into_workspace(ws: Workspace, record: FileRecord, *, path: Path | None = None) -> list[TableInfo]:
    """把文件的每个 sheet 存成工作区里的一张表。期望结果文件的表名用「期望_sheet名」。"""
    source = Path(record.stored_path)
    profile = dict(getattr(record, "profile", None) or {})
    version = file_version(source, profile.get(FILE_VERSION_KEY))
    same_version = (profile.get(FILE_VERSION_KEY) or {}).get("sha256") == version["sha256"]
    original_bytes = path is None or content_digest(path) == version["sha256"]
    if profile.get("file_only") and same_version and original_bytes:
        return []
    sheets = read_workbook(path) if path is not None else _read_file_copy(source)
    if original_bytes:
        if not same_version:
            # 旧画像没有可验证的版本，或原件已更新：仅对本次实际读取的数据重新画像。
            refreshed = profile_workbook(sheets, preview_rows=0)
            for sheet_profile in refreshed["sheets"]:
                sheet_profile.pop("preview", None)
            profile = {key: value for key, value in profile.items()
                       if key not in {"sheets", "file_only", FILE_MEMORY_KEY}}
            profile.update(refreshed)
        else:
            quality_by_sheet = {sheet.name: dict(sheet.quality or {}) for sheet in sheets}
            profile["sheets"] = [{**saved, "quality": quality_by_sheet.get(saved.get("sheet"), saved.get("quality", {}))}
                                 for saved in profile.get("sheets", [])]
        profile[FILE_VERSION_KEY] = version
        record.profile = profile
    for info in ws.list_tables():  # 同一个文件重复加载时先清掉旧表
        if info.file_id == record.id:
            ws.drop(info.name)
    base = table_base_name(record.filename)
    if ws.exists(sanitize_table_name(base)):
        # 工作区里已经有别的文件占了这个名字（例如同时上传了 9 月和 10 月）：用带日期的完整文件名，方便区分
        base = Path(record.filename).stem
    tables = []
    for sheet in sheets:
        if record.kind == "expected":
            name = f"期望_{sheet.name}"
        else:
            name = base if len(sheets) == 1 else f"{base}_{sheet.name}"
        name = ws.unique_name(name)
        info = ws.put_dataframe(
            name, sheet.df, label=f"{record.filename} · {sheet.name}", source="uploaded",
            role="expected" if record.kind == "expected" else "input", file_id=record.id, filename=record.filename,
            sheet=sheet.name, header=sheet.header.to_dict(), file_version=version["sha256"] if original_bytes else "",
            read_quality=dict(sheet.quality or {}),
            description=("人工做好的期望结果" if record.kind == "expected" else
                         "系统生成的工作簿，可继续作为输入处理" if record.kind == "output" else "上传的原始数据")
                        + (f"，标题：{sheet.header.title}" if sheet.header.title else ""),
        )
        if original_bytes:
            saved = profile.get(FILE_MEMORY_KEY) or {}
            restored = saved.get("sha256") == version["sha256"] and restore_memory(
                ws, name, (saved.get("sheets") or {}).get(sheet.name, {}),
            )
            if not restored:
                sheet_profile = next((item for item in profile.get("sheets", []) if item.get("sheet") == sheet.name), None)
                if sheet_profile:
                    seed_profile(ws, name, {**sheet_profile, "preview": {
                        "columns": info.column_names, "rows": frame_rows(sheet.df, 5),
                    }})
        tables.append(ws.require(name))
    return tables


def export_tables(ws: Workspace, sheets: list[tuple[str, str]], path: Path) -> list[dict[str, Any]]:
    specs = [SheetSpec(name=sheet_name or table, df=ws.df(table)) for table, sheet_name in sheets]
    return write_excel(path, specs)
