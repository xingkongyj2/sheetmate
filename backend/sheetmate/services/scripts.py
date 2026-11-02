"""Shared script access checks for viewing, recipe edits and replay inputs."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from sheetmate.db.models import Conversation, Message, RecipeRecord, Run, User
from sheetmate.engine.script_store import script_origin
from sheetmate.services.auth import can_access_customer
from sheetmate.services.errors import Forbidden


def _references(value: Any, script_id: str) -> bool:
    if isinstance(value, dict):
        return value.get("script_id") == script_id or any(_references(item, script_id) for item in value.values())
    return isinstance(value, list) and any(_references(item, script_id) for item in value)


def _can_view(db: Session, user: User, record) -> bool:
    if user.role == "admin" or getattr(record, "user_id", None) == user.id:
        return True
    if record.customer_id and can_access_customer(db, user, record.customer_id):
        return True
    if record.conversation_id:
        conversation = db.get(Conversation, record.conversation_id)
        return conversation is not None and conversation.user_id == user.id
    return isinstance(record, RecipeRecord) and record.scope == "global" and not record.customer_id


def require_script_access(db: Session, user: User, script_id: str) -> None:
    try:
        origin = script_origin(script_id)
    except (OSError, ValueError, KeyError):
        raise Forbidden("你没有权限查看这个脚本") from None
    if origin:
        if user.role == "admin" or origin.get("global_access") or origin.get("user_id") == user.id:
            return
        customer_id, conversation_id = origin.get("customer_id"), origin.get("conversation_id")
        conversation = db.get(Conversation, conversation_id) if conversation_id else None
        customer_allowed = bool(customer_id and can_access_customer(db, user, customer_id))
        conversation_allowed = conversation is not None and (conversation.user_id == user.id or bool(
            conversation.customer_id and can_access_customer(db, user, conversation.customer_id)))
        if customer_allowed or conversation_allowed:
            return
        raise Forbidden("你没有权限查看这个脚本")
    for run in db.scalars(select(Run)):
        if _references([run.steps, run.validation], script_id) and _can_view(db, user, run):
            return
    for recipe in db.scalars(select(RecipeRecord)):
        if _references(recipe.body, script_id) and _can_view(db, user, recipe):
            return
    for message, conversation in db.execute(select(Message, Conversation).join(Conversation, Message.conversation_id == Conversation.id)):
        if _references(message.events, script_id) and (user.role == "admin" or conversation.user_id == user.id or
                bool(conversation.customer_id and can_access_customer(db, user, conversation.customer_id))):
            return
    raise Forbidden("你没有权限查看这个脚本")



def require_script_references(db: Session, user: User, value: Any) -> None:
    """Validate references before model validation can load their source files."""
    checked: set[str] = set()

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            if "script_id" in item and item["script_id"] is not None:
                script_id = item["script_id"]
                if not isinstance(script_id, str):
                    raise Forbidden("你没有权限查看这个脚本")
                if script_id not in checked:
                    require_script_access(db, user, script_id)
                    checked.add(script_id)
            for nested in item.values():
                walk(nested)
        elif isinstance(item, list):
            for nested in item:
                walk(nested)

    walk(value)
