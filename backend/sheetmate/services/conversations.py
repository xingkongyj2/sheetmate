"""会话：新建（可选绑定客户）、列表、详情、改模式、绑定 / 保存到客户、删除，以及会话工作区。

- 绑定客户的会话：按客户的记忆、流程、文件来聊天
- 未绑定的会话：数据只属于这个会话；以后可以「保存到客户」（选已有客户或新建客户），
  这时会话里上传的文件、生成的流程、记下的记忆都会迁移到客户名下
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from sheetmate.config import settings
from sheetmate.db.models import Conversation, Customer, FileRecord, Message, RecipeRecord, Run, User
from sheetmate.engine.workspace import Workspace, WorkspaceManager
from sheetmate.engine.script_references import bind_script_references, externalize_script_references
from sheetmate.services import customers as customer_service
from sheetmate.services import files as file_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services import runs as run_service
from sheetmate.conversation_log import conversation_log_path
from sheetmate.engine.script_store import queue_unreferenced_scope_scripts
from sheetmate.services.auth import require_customer
from sheetmate.services.errors import Forbidden, NotFound, ServiceError

conversation_workspaces = WorkspaceManager(settings.workspaces_dir, max_open=24)

MODES = {"analyze": "分析", "modify": "修改", "create": "建表"}


def workspace(conversation_id: str) -> Workspace:
    return conversation_workspaces.get(conversation_id)


def get_conversation(db: Session, user: User, conversation_id: str) -> Conversation:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        raise NotFound("对话不存在")
    if conv.user_id != user.id and user.role != "admin":
        raise Forbidden("你没有权限访问这个对话")
    return conv


def create_conversation(db: Session, user: User, *, customer_id: str | None = None, mode: str = "analyze",
                        title: str = "", recipe_id: str | None = None) -> Conversation:
    if customer_id:
        require_customer(db, user, customer_id)
        selected = recipe_service.resolve_customer_recipe(db, customer_id, recipe_id)
        recipe_id = selected.recipe_id if selected else None
    elif recipe_id:
        raise ServiceError("请先选择流程所属客户")
    conv = Conversation(user_id=user.id, customer_id=customer_id, mode=mode if mode in MODES else "analyze",
                        title=title.strip() or "新对话", recipe_id=recipe_id)
    db.add(conv)
    db.flush()
    if customer_id:
        file_service.retain_customer_files(db, customer_id, conversation_id=conv.id)
    return conv


def list_conversations(db: Session, user: User, *, customer_id: str | None = None, limit: int = 100) -> list[Conversation]:
    stmt = select(Conversation).where(Conversation.user_id == user.id).order_by(Conversation.updated_at.desc()).limit(limit)
    if customer_id:
        stmt = stmt.where(Conversation.customer_id == customer_id)
    return list(db.scalars(stmt))


def conversation_dict(db: Session, conv: Conversation) -> dict[str, Any]:
    customer = db.get(Customer, conv.customer_id) if conv.customer_id else None
    return {
        "id": conv.id, "title": conv.title, "mode": conv.mode, "mode_label": MODES.get(conv.mode, conv.mode),
        "recipe_id": conv.recipe_id,
        "customer": {"id": customer.id, "name": customer.name, "mascot": customer.mascot} if customer else None,
        "created_at": conv.created_at.isoformat(), "updated_at": conv.updated_at.isoformat(),
    }


_RECIPE_FLOW_EVENTS = {"recipe_generated", "recipe_saved", "recipe_completed", "recipe_view"}


def _recipe_event_key(event: Any) -> tuple[str, int] | None:
    if not isinstance(event, dict) or event.get("type") not in _RECIPE_FLOW_EVENTS:
        return None
    recipe = event.get("recipe") if isinstance(event.get("recipe"), dict) else {}
    recipe_id = event.get("recipe_id") or recipe.get("id")
    version = event.get("version") or recipe.get("version")
    # 缺版本的历史事件不能取最新版，否则会把旧消息改画成后来修订的流程。
    if not isinstance(recipe_id, str) or not recipe_id or type(version) is not int or version < 1:
        return None
    return recipe_id, version


def _historical_recipe_flows(db: Session, conv: Conversation) -> dict[tuple[str, int], dict[str, Any]]:
    wanted = set()
    for message in conv.messages:
        for event in message.events or []:
            key = _recipe_event_key(event)
            flow = event.get("flow") if isinstance(event, dict) else None
            if key and (not isinstance(flow, dict) or "input_files" not in flow or "output_files" not in flow):
                wanted.add(key)
    if not wanted:
        return {}
    scope = [RecipeRecord.conversation_id == conv.id, RecipeRecord.scope == "global"]
    if conv.customer_id:
        scope.append(RecipeRecord.customer_id == conv.customer_id)
    records = db.scalars(select(RecipeRecord).where(
        RecipeRecord.recipe_id.in_({key[0] for key in wanted}), or_(*scope),
    ))
    flows = {}
    for record in records:
        key = record.recipe_id, record.version
        if key not in wanted:
            continue
        try:
            flows[key] = recipe_service.record_to_recipe(record).flow()
        except ValueError:
            # 个别损坏或已删除的旧版本保留原消息，不影响整个会话读取。
            continue
    return flows


def message_dict(m: Message, *, recipe_flows: dict[tuple[str, int], dict[str, Any]] | None = None,
                 file_records: dict[str, FileRecord] | None = None) -> dict[str, Any]:
    migrated = recipe_service.strip_recipe_statuses(externalize_script_references(m.events or []))
    if migrated != m.events:
        m.events = migrated
    bind_script_references(migrated, conversation_id=m.conversation_id)
    events = []
    for event in m.events or []:
        if isinstance(event, dict) and event.get("type") == "file" and event.get("file_id") in (file_records or {}):
            # Keep execution evidence intact while reflecting a later replacement in file cards.
            event = {**event, **file_service.file_dict(file_records[event["file_id"]])}
        key = _recipe_event_key(event)
        flow = event.get("flow") if isinstance(event, dict) else None
        if key in (recipe_flows or {}) and (not isinstance(flow, dict) or "input_files" not in flow or "output_files" not in flow):
            event = {**event, "flow": recipe_flows[key]}
        events.append(event)
    return {"id": m.id, "role": m.role, "content": m.content, "events": events, "meta": m.meta or {},
            "created_at": m.created_at.isoformat()}


def detail(db: Session, conv: Conversation) -> dict[str, Any]:
    # 展示范围跟客户绑定一致，但执行仍使用各会话独立的工作区，避免同名表互相覆盖。
    scope = {"customer_id": conv.customer_id} if conv.customer_id else {"conversation_id": conv.id}
    files = file_service.list_files(db, customer_id=conv.customer_id, conversation_id=conv.id)
    available_files = {record.id for record in files}
    recipes = recipe_service.latest_records(db, **scope)
    sources = list(db.scalars(select(Conversation).where(Conversation.customer_id == conv.customer_id)
                             .order_by(Conversation.created_at, Conversation.id))) if conv.customer_id else [conv]
    tables = []
    for source in sources:
        tables.extend(_table_brief(t, conversation=source) for t in conversation_workspaces.existing_tables(source.id)
                      if t.source != "uploaded" or not t.file_id or t.file_id in available_files)
    source_by_id = {source.id: source for source in sources}
    # 只读查询不会创建工作区表；保留已展示的有限预览，方便排查本次处理使用过的数据。
    snapshots = db.execute(select(Message.id, Message.conversation_id, Message.events, Message.created_at)
                           .where(Message.conversation_id.in_(source_by_id), Message.role == "assistant")
                           .order_by(Message.created_at, Message.id))
    for message_id, conversation_id, events, created_at in snapshots:
        source = source_by_id[conversation_id]
        for event_index, event in enumerate(events or []):
            if not isinstance(event, dict):
                continue
            table = event.get("table")
            if event.get("type") != "table" or not isinstance(table, dict):
                continue
            if table.get("name") and not table.get("ephemeral"):
                continue
            columns = table.get("columns") or []
            preview = (table.get("preview") or [])[:20]
            label = table.get("label") or ""
            tables.append({"name": label or "临时查询结果", "label": label,
                           "rows": table.get("rows", len(preview)), "cols": table.get("cols", len(columns)),
                           "columns": columns, "source": "computed", "role": "derived", "file_id": None,
                           "description": "处理过程中的临时查询预览，仅保留部分数据行。",
                           "preview_only": True, "preview": preview,
                           "snapshot_id": f"{message_id}:{event_index}",
                           "conversation_id": source.id, "conversation_title": source.title,
                           "created_at": created_at.isoformat()})
    run_condition = Run.customer_id == conv.customer_id if conv.customer_id else Run.conversation_id == conv.id
    resource_runs = list(db.scalars(select(Run).where(run_condition).order_by(Run.created_at, Run.id)))
    for run in resource_runs:
        for table in run_service.run_workspaces.existing_tables(run.id):
            if table.source != "computed":
                continue
            brief = _table_brief(table, conversation=source_by_id.get(run.conversation_id))
            brief.update({"run_id": run.id, "run_title": run.request_text[:120],
                          "conversation_id": run.conversation_id})
            tables.append(brief)
    tables.sort(key=lambda t: (t["created_at"], t.get("run_id", ""), t.get("conversation_id") or ""))
    runs = db.scalars(select(Run).where(Run.conversation_id == conv.id).order_by(Run.created_at.desc()).limit(20))
    recipe_flows = _historical_recipe_flows(db, conv)
    historical_file_ids = {event["file_id"] for message in conv.messages for event in message.events or []
                           if isinstance(event, dict) and event.get("type") == "file" and isinstance(event.get("file_id"), str)}
    historical_files = {record.id: record for record in db.scalars(select(FileRecord).where(
        FileRecord.id.in_(historical_file_ids)))} if historical_file_ids else {}
    return {
        **conversation_dict(db, conv),
        "messages": [message_dict(m, recipe_flows=recipe_flows, file_records=historical_files) for m in conv.messages],
        "tables": tables,
        "files": [file_service.file_dict(f) for f in files],
        "recipes": [recipe_service.recipe_brief(r) for r in recipes],
        "runs": [{"run_id": r.id, "kind": r.kind, "status": r.status, "created_at": r.created_at.isoformat()} for r in runs],
    }


def _table_brief(t, *, conversation: Conversation | None = None) -> dict[str, Any]:
    data = {"name": t.name, "label": t.label, "rows": t.rows, "cols": len(t.columns), "columns": t.column_names,
            "source": t.source, "role": t.role, "file_id": t.file_id, "description": t.description,
            "filename": t.filename, "sheet": t.sheet,
            "created_at": datetime.fromtimestamp(t.created_at).isoformat()}
    if conversation is not None:
        data.update({"conversation_id": conversation.id, "conversation_title": conversation.title})
    return data


def table_source(db: Session, user: User, conversation_id: str,
                 source_conversation_id: str | None = None) -> Conversation:
    """表名只在来源工作区中唯一；客户视图允许操作同一客户的其他会话表。"""
    conv = get_conversation(db, user, conversation_id)
    if conv.customer_id:
        require_customer(db, user, conv.customer_id)
    if not source_conversation_id or source_conversation_id == conv.id:
        return conv
    source = db.get(Conversation, source_conversation_id)
    if source is None or not conv.customer_id or source.customer_id != conv.customer_id:
        raise Forbidden("只能查看或清理本会话或当前客户的中间数据")
    return source


def update_conversation(db: Session, conv: Conversation, user: User, data: dict[str, Any]) -> Conversation:
    if data.get("title"):
        conv.title = str(data["title"]).strip()[:120]
    if data.get("mode"):
        if data["mode"] not in MODES:
            raise ServiceError("模式只能是 analyze、modify 或 create")
        conv.mode = data["mode"]
    if "customer_id" in data:
        new_customer = data["customer_id"] or None
        if new_customer and new_customer != conv.customer_id:
            bind_to_customer(db, conv, user, customer_id=new_customer)
        elif not new_customer and conv.customer_id:
            file_service.retain_customer_files(db, conv.customer_id, conversation_id=conv.id)
            conv.customer_id = None
            conv.recipe_id = None
    if "recipe_id" in data:
        if not conv.customer_id and data["recipe_id"]:
            raise ServiceError("请先选择流程所属客户")
        selected = recipe_service.resolve_customer_recipe(db, conv.customer_id, data["recipe_id"]) if conv.customer_id else None
        conv.recipe_id = selected.recipe_id if selected else None
    return conv


def bind_to_customer(db: Session, conv: Conversation, user: User, *, customer_id: str | None = None,
                     new_customer: dict[str, Any] | None = None) -> Customer:
    """把会话保存到客户：选择已有客户，或者顺手新建一个客户。会话里的文件、流程、记忆一起迁移过去。"""
    if new_customer:
        customer = customer_service.create_customer(db, user, new_customer.get("name", ""), new_customer.get("code", ""),
                                                    new_customer.get("description", ""))
    elif customer_id:
        customer = require_customer(db, user, customer_id)
    else:
        raise ServiceError("请选择一个客户，或者填写新客户的名称")
    previous_customer = conv.customer_id
    if previous_customer:
        file_service.retain_customer_files(db, previous_customer, conversation_id=conv.id)
    records = file_service.list_files(db, conversation_id=conv.id)
    conv.customer_id = customer.id
    for record in records:
        file_service.retain_for_customer(db, record, customer.id)
    file_service.retain_customer_files(db, customer.id, conversation_id=conv.id)
    for run in db.scalars(select(Run).where(Run.conversation_id == conv.id, Run.customer_id.is_(None))):
        run.customer_id = customer.id
    recipe_service.assign_customer(db, conversation_id=conv.id, customer_id=customer.id)
    selected = recipe_service.resolve_customer_recipe(db, customer.id)
    if previous_customer != customer.id or not conv.recipe_id:
        conv.recipe_id = selected.recipe_id if selected else None
    memory_service.move_conversation_memories(db, conv.id, customer.id, recipe_id=conv.recipe_id)
    db.flush()
    return customer


def _defer_owner_storage_cleanup(db: Session, roots: list[Path]) -> None:
    """After commit, remove unregistered leftovers while preserving referenced files."""
    db.flush()
    preserved = {Path(path).resolve() for path in db.scalars(select(FileRecord.stored_path)) if path}

    def cleanup() -> None:
        for root in roots:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*"), reverse=True):
                try:
                    if path.is_file() and path.resolve() not in preserved:
                        path.unlink(missing_ok=True)
                    elif path.is_dir():
                        path.rmdir()
                except OSError:
                    continue
            try:
                root.rmdir()
            except OSError:
                # Shared files can outlive the original conversation/customer.
                pass

    file_service.defer_after_commit(db, cleanup)


def _run_file_ids(run: Run) -> set[str]:
    ids = set(run.input_file_ids or [])
    if run.output_file_id:
        ids.add(run.output_file_id)

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            file_id = value.get("file_id")
            if isinstance(file_id, str):
                ids.add(file_id)
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(run.steps or [])
    walk(run.validation or {})
    return ids


def delete_conversation(db: Session, conv: Conversation) -> None:
    """Delete a conversation and every resource whose only scope is that conversation."""
    conversation_id = conv.id
    records = file_service.list_files(db, conversation_id=conversation_id, available_only=False)
    run_rows = list(db.scalars(select(Run).where(Run.conversation_id == conversation_id)))
    run_ids = [run.id for run in run_rows]
    run_file_ids = {file_id for run in run_rows for file_id in _run_file_ids(run)}
    run_records = list(db.scalars(select(FileRecord).where(
        or_(FileRecord.run_id.in_(run_ids), FileRecord.id.in_(run_file_ids))
    ))) if run_ids or run_file_ids else []
    all_records = {record.id: record for record in [*records, *run_records]}

    # 运行工作区不是外键，必须在删行前显式关闭并删除。
    for run in run_rows:
        file_service.defer_after_commit(db, lambda run_id=run.id: run_service.run_workspaces.delete(run_id))
    if run_ids:
        db.execute(delete(Run).where(Run.id.in_(run_ids)))

    # 未绑定客户的流程只属于本会话；绑定客户后的流程由客户资源生命周期管理。
    local_recipes = list(db.scalars(select(RecipeRecord).where(
        RecipeRecord.conversation_id == conversation_id, RecipeRecord.customer_id.is_(None)
    )))
    for record in local_recipes:
        db.delete(record)

    file_service.defer_after_commit(db, lambda: conversation_workspaces.delete(conversation_id))
    db.delete(conv)
    db.flush()
    for record in all_records.values():
        current = db.get(FileRecord, record.id)
        if current is not None:
            db.refresh(current)
            file_service.delete_if_unowned(db, current)

    # 日志和脚本文件没有数据库外键，跟随对应会话清理。
    db.info.setdefault("deleted_aux_paths", set()).add(str(conversation_log_path(conversation_id)))
    queue_unreferenced_scope_scripts(db, conversation_ids={conversation_id})
    roots = [settings.outputs_dir / f"conv_{conversation_id}", settings.uploads_dir / f"conv_{conversation_id}"]
    _defer_owner_storage_cleanup(db, roots)


def add_message(db: Session, conv: Conversation, role: str, content: str, *, events: list[dict[str, Any]] | None = None,
                meta: dict[str, Any] | None = None) -> Message:
    normalized = externalize_script_references(events or [])
    bind_script_references(normalized, user_id=conv.user_id, customer_id=conv.customer_id, conversation_id=conv.id)
    msg = Message(conversation_id=conv.id, role=role, content=content, events=normalized, meta=meta or {})
    db.add(msg)
    conv.updated_at = msg.created_at or conv.updated_at
    db.flush()
    return msg
