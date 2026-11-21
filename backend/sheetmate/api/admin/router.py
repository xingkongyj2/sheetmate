"""管理后台路由：仅管理员可访问。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from sheetmate.api.admin.schemas import CreateUserBody, CustomerMembersBody, UpdateUserBody
from sheetmate.api.deps import admin_user
from sheetmate.db.models import User
from sheetmate.db.session import get_db
from sheetmate.services.admin import service as admin_service
from sheetmate.services.observability.metrics import admin_execution_metrics

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/overview")
def get_overview(
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(admin_user),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, **admin_service.overview(db, user)}


@router.get("/stats")
def get_stats(
    response: Response,
    days: int = Query(default=30, ge=1, le=90),
    user_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, **admin_execution_metrics(db, days=days, user_id=user_id)}


@router.get("/users")
def list_users(
    response: Response,
    db: Session = Depends(get_db),
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "users": admin_service.list_users(db)}


@router.post("/users")
def create_user(
    body: CreateUserBody,
    db: Session = Depends(get_db),
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    created = admin_service.create_user(
        db,
        account=body.account,
        password=body.password,
        display_name=body.display_name,
        role=body.role,
        customer_ids=body.customer_ids,
    )
    return {"ok": True, "user": created}


@router.put("/users/{user_id}")
def update_user(
    user_id: int,
    body: UpdateUserBody,
    db: Session = Depends(get_db),
    actor: User = Depends(admin_user),
) -> dict[str, Any]:
    updated = admin_service.update_user(
        db,
        actor,
        user_id,
        display_name=body.display_name,
        role=body.role,
        password=body.password,
        customer_ids=body.customer_ids,
    )
    return {"ok": True, "user": updated}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(admin_user),
) -> dict[str, Any]:
    admin_service.delete_user(db, actor, user_id)
    return {"ok": True}


@router.get("/customers")
def list_customers(
    response: Response,
    db: Session = Depends(get_db),
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return {"ok": True, "customers": admin_service.list_customers(db)}


@router.put("/customers/{customer_id}/members")
def set_customer_members(
    customer_id: str,
    body: CustomerMembersBody,
    db: Session = Depends(get_db),
    _: User = Depends(admin_user),
) -> dict[str, Any]:
    updated = admin_service.set_customer_members(db, customer_id, body.user_ids)
    return {"ok": True, "customer": updated}
