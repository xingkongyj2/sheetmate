"""会话：新建（可选绑定客户）、列表、详情、改模式、绑定 / 保存到客户、删除，以及会话工作区。

- 绑定客户的会话：按客户的记忆、流程、文件来聊天
- 未绑定的会话：数据只属于这个会话；以后可以「保存到客户」（选已有客户或新建客户），
  这时会话里上传的文件、生成的流程、记下的记忆都会迁移到客户名下
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sheetmate.config import settings
from sheetmate.db.models import Conversation, Customer, FileRecord, Message, Run, User
from sheetmate.engine.workspace import Workspace, WorkspaceManager
from sheetmate.services import customers as customer_service
from sheetmate.services import files as file_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services.auth import require_customer
from sheetmate.services.errors import Forbidden, NotFound, ServiceError

conversation_workspaces = WorkspaceManager(settings.workspaces_dir, max_open=24)

MODES = {"analyze": "分析", "modify": "修改"}


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
                        title: str = "") -> Conversation:
    if customer_id:
        require_customer(db, user, customer_id)
    conv = Conversation(user_id=user.id, customer_id=customer_id, mode=mode if mode in MODES else "analyze",
                        title=title.strip() or "新对话")
    db.add(conv)
    db.flush()
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
        "customer": {"id": customer.id, "name": customer.name, "mascot": customer.mascot} if customer else None,
        "created_at": conv.created_at.isoformat(), "updated_at": conv.updated_at.isoformat(),
    }


def message_dict(m: Message) -> dict[str, Any]:
    return {"id": m.id, "role": m.role, "content": m.content, "events": m.events or [], "meta": m.meta or {},
            "created_at": m.created_at.isoformat()}


def detail(db: Session, conv: Conversation) -> dict[str, Any]:
    ws = workspace(conv.id)
    files = file_service.list_files(db, conversation_id=conv.id)
    recipes = recipe_service.latest_records(db, conversation_id=conv.id, include_deprecated=True)
    runs = db.scalars(select(Run).where(Run.conversation_id == conv.id).order_by(Run.created_at.desc()).limit(20))
    return {
        **conversation_dict(db, conv),
        "messages": [message_dict(m) for m in conv.messages],
        "tables": [_table_brief(t) for t in ws.list_tables()],
        "files": [file_service.file_dict(f) for f in files],
        "recipes": [recipe_service.recipe_brief(r) for r in recipes],
        "runs": [{"run_id": r.id, "kind": r.kind, "status": r.status, "created_at": r.created_at.isoformat()} for r in runs],
    }


def _table_brief(t) -> dict[str, Any]:
    return {"name": t.name, "label": t.label, "rows": t.rows, "cols": len(t.columns), "columns": t.column_names,
            "source": t.source, "role": t.role, "file_id": t.file_id, "description": t.description}


def update_conversation(db: Session, conv: Conversation, user: User, data: dict[str, Any]) -> Conversation:
    if data.get("title"):
        conv.title = str(data["title"]).strip()[:120]
    if data.get("mode"):
        if data["mode"] not in MODES:
            raise ServiceError("模式只能是 analyze 或 modify")
        conv.mode = data["mode"]
    if "customer_id" in data:
        new_customer = data["customer_id"] or None
        if new_customer and new_customer != conv.customer_id:
            bind_to_customer(db, conv, user, customer_id=new_customer)
        elif not new_customer and conv.customer_id:
            conv.customer_id = None
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
    conv.customer_id = customer.id
    for record in db.scalars(select(FileRecord).where(FileRecord.conversation_id == conv.id, FileRecord.customer_id.is_(None))):
        record.customer_id = customer.id
    for run in db.scalars(select(Run).where(Run.conversation_id == conv.id, Run.customer_id.is_(None))):
        run.customer_id = customer.id
    recipe_service.assign_customer(db, conversation_id=conv.id, customer_id=customer.id)
    memory_service.move_conversation_memories(db, conv.id, customer.id)
    db.flush()
    return customer


def delete_conversation(db: Session, conv: Conversation) -> None:
    conversation_workspaces.delete(conv.id)
    db.delete(conv)


def add_message(db: Session, conv: Conversation, role: str, content: str, *, events: list[dict[str, Any]] | None = None,
                meta: dict[str, Any] | None = None) -> Message:
    msg = Message(conversation_id=conv.id, role=role, content=content, events=events or [], meta=meta or {})
    db.add(msg)
    conv.updated_at = msg.created_at or conv.updated_at
    db.flush()
    return msg
