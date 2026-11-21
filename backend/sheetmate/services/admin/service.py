"""管理后台服务：总览统计、成员管理（角色/密码/客户权限）、客户资源与成员分配。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from sheetmate.core.config.settings import PRIVACY_LLM_ROOT
from sheetmate.core.errors import Conflict, NotFound
from sheetmate.db.models import (
    AgentExecution,
    Conversation,
    Customer,
    FileRecord,
    FormulaRecord,
    RecipeRecord,
    Run,
    User,
    UserCustomer,
)
from sheetmate.services.identity import auth as auth_service
from sheetmate.services.observability.metrics import admin_execution_metrics
from sheetmate.services.settings import service as settings_service
from sheetmate.services.workflows import service as recipe_service

VALID_ROLES = {"admin", "member"}


def _iso(dt: datetime | None) -> str:
    return dt.isoformat() if dt else ""


def _admin_count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(User).where(User.role == "admin")) or 0)


def _format_user_row(
    user: User,
    *,
    granted_customers: list[dict[str, Any]],
    owned_customer_count: int,
    conversation_count: int,
    task_count: int,
    last_active_at: datetime | None,
) -> dict[str, Any]:
    info = auth_service.user_dict(user)
    return {
        "id": info["id"],
        "account": info["account"],
        "display_name": info["display_name"],
        "avatar": info["avatar"],
        "role": info["role"],
        "role_label": info["role_label"],
        "created_at": _iso(user.created_at),
        "last_active_at": _iso(last_active_at),
        "customer_ids": [c["id"] for c in granted_customers],
        "customers": granted_customers,
        "owned_customer_count": owned_customer_count,
        "conversation_count": conversation_count,
        "task_count": task_count,
    }


def list_users(db: Session) -> list[dict[str, Any]]:
    """列出全部成员及角色、授权客户、活跃统计。"""
    users = list(db.scalars(select(User).order_by(User.role.asc(), User.id.asc())))
    customers = {c.id: c for c in db.scalars(select(Customer))}

    grants_by_user: dict[int, list[dict[str, Any]]] = {}
    for link in db.scalars(select(UserCustomer)):
        cust = customers.get(link.customer_id)
        if cust:
            grants_by_user.setdefault(link.user_id, []).append({
                "id": cust.id,
                "name": cust.name,
                "mascot": cust.mascot,
            })

    owned_counts = dict(
        db.execute(
            select(Customer.created_by, func.count())
            .where(Customer.created_by.is_not(None))
            .group_by(Customer.created_by)
        ).all()
    )
    conv_stats = {
        uid: (int(cnt), latest)
        for uid, cnt, latest in db.execute(
            select(Conversation.user_id, func.count(), func.max(Conversation.updated_at))
            .group_by(Conversation.user_id)
        ).all()
    }
    task_stats = {
        uid: (int(cnt), latest)
        for uid, cnt, latest in db.execute(
            select(AgentExecution.user_id, func.count(), func.max(AgentExecution.created_at))
            .group_by(AgentExecution.user_id)
        ).all()
    }

    rows: list[dict[str, Any]] = []
    for u in users:
        conv_cnt, conv_latest = conv_stats.get(u.id, (0, None))
        task_cnt, task_latest = task_stats.get(u.id, (0, None))
        candidates = [dt for dt in (conv_latest, task_latest) if dt is not None]
        last_active = max(candidates) if candidates else None
        granted = sorted(grants_by_user.get(u.id, []), key=lambda item: item["name"])
        rows.append(
            _format_user_row(
                u,
                granted_customers=granted,
                owned_customer_count=int(owned_counts.get(u.id, 0)),
                conversation_count=conv_cnt,
                task_count=task_cnt,
                last_active_at=last_active,
            )
        )
    return rows


def _get_user_row(db: Session, user_id: int) -> dict[str, Any]:
    for row in list_users(db):
        if row["id"] == user_id:
            return row
    raise NotFound("成员不存在")


def _sync_user_customers(db: Session, user_id: int, customer_ids: list[str]) -> None:
    wanted = list(dict.fromkeys(cid.strip() for cid in customer_ids if cid and cid.strip()))
    if wanted:
        existing = set(db.scalars(select(Customer.id).where(Customer.id.in_(wanted))))
        missing = [cid for cid in wanted if cid not in existing]
        if missing:
            raise NotFound("指定的客户不存在")
    db.execute(delete(UserCustomer).where(UserCustomer.user_id == user_id))
    for cid in wanted:
        db.add(UserCustomer(user_id=user_id, customer_id=cid))
    db.flush()


def create_user(
    db: Session,
    *,
    account: str,
    password: str,
    display_name: str = "",
    role: str = "member",
    customer_ids: list[str] | None = None,
) -> dict[str, Any]:
    """管理员创建新成员账号，可直接指定角色和授权客户。"""
    clean_account = account.strip()
    if len(clean_account) < 3:
        raise Conflict("账号至少 3 个字符")
    if len(clean_account) > 64:
        raise Conflict("账号最多 64 个字符")
    if len(password) < 6:
        raise Conflict("密码至少 6 位")
    if role not in VALID_ROLES:
        raise Conflict("角色只能是管理员或成员")
    if db.scalar(select(User).where(User.account == clean_account)):
        raise Conflict("这个账号已经存在")

    name = display_name.strip()[:40] or clean_account
    user = User(
        account=clean_account,
        password_hash=auth_service.hash_password(password),
        display_name=name,
        role=role,
    )
    db.add(user)
    db.flush()

    if customer_ids is not None:
        _sync_user_customers(db, user.id, customer_ids)
    return _get_user_row(db, user.id)


def update_user(
    db: Session,
    actor: User,
    user_id: int,
    *,
    display_name: str | None = None,
    role: str | None = None,
    password: str | None = None,
    customer_ids: list[str] | None = None,
) -> dict[str, Any]:
    """管理员修改成员姓名、角色、重置密码或调整客户权限。"""
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("成员不存在")

    if role is not None:
        if role not in VALID_ROLES:
            raise Conflict("角色只能是管理员或成员")
        if user.id == actor.id and role != "admin":
            raise Conflict("不能取消自己的管理员权限")
        if user.role == "admin" and role != "admin" and _admin_count(db) <= 1:
            raise Conflict("系统至少保留一位管理员")
        user.role = role

    if display_name is not None:
        trimmed = display_name.strip()[:40]
        if not trimmed:
            raise Conflict("姓名不能为空")
        user.display_name = trimmed

    if password is not None and password != "":
        if len(password) < 6:
            raise Conflict("密码至少 6 位")
        user.password_hash = auth_service.hash_password(password)

    if customer_ids is not None:
        _sync_user_customers(db, user.id, customer_ids)

    db.flush()
    return _get_user_row(db, user.id)


def delete_user(db: Session, actor: User, user_id: int) -> None:
    """管理员删除成员账号（禁止删除自己或最后一位管理员）。"""
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("成员不存在")
    if user.id == actor.id:
        raise Conflict("不能删除当前登录账号")
    if user.role == "admin" and _admin_count(db) <= 1:
        raise Conflict("系统至少保留一位管理员")
    db.delete(user)
    db.flush()


def list_customers(db: Session) -> list[dict[str, Any]]:
    """全平台客户管理列表：附创建人、已授权成员、流程/公式/对话/文件与存储统计。"""
    customers = list(db.scalars(select(Customer).order_by(Customer.updated_at.desc())))
    users = {u.id: auth_service.user_dict(u) for u in db.scalars(select(User))}

    members_by_customer: dict[str, list[dict[str, Any]]] = {}
    for link in db.scalars(select(UserCustomer)):
        u = users.get(link.user_id)
        if u:
            members_by_customer.setdefault(link.customer_id, []).append({
                "id": u["id"],
                "account": u["account"],
                "display_name": u["display_name"],
                "avatar": u["avatar"],
                "role": u["role"],
            })

    recipe_counts = dict(
        db.execute(
            select(RecipeRecord.customer_id, func.count(func.distinct(RecipeRecord.recipe_id)))
            .where(RecipeRecord.customer_id.is_not(None))
            .group_by(RecipeRecord.customer_id)
        ).all()
    )
    formula_counts = dict(
        db.execute(
            select(FormulaRecord.customer_id, func.count(func.distinct(FormulaRecord.formula_id)))
            .where(FormulaRecord.scope == "customer", FormulaRecord.customer_id.is_not(None))
            .group_by(FormulaRecord.customer_id)
        ).all()
    )
    conv_stats = {
        cid: (int(cnt), latest)
        for cid, cnt, latest in db.execute(
            select(Conversation.customer_id, func.count(), func.max(Conversation.updated_at))
            .where(Conversation.customer_id.is_not(None))
            .group_by(Conversation.customer_id)
        ).all()
    }
    run_stats = {
        cid: (int(cnt), latest)
        for cid, cnt, latest in db.execute(
            select(Run.customer_id, func.count(), func.max(Run.created_at))
            .where(Run.customer_id.is_not(None))
            .group_by(Run.customer_id)
        ).all()
    }
    file_stats = {
        cid: (int(cnt), int(total_size or 0))
        for cid, cnt, total_size in db.execute(
            select(FileRecord.customer_id, func.count(), func.coalesce(func.sum(FileRecord.size), 0))
            .where(FileRecord.customer_id.is_not(None))
            .group_by(FileRecord.customer_id)
        ).all()
    }

    rows: list[dict[str, Any]] = []
    for c in customers:
        creator_raw = users.get(c.created_by) if c.created_by else None
        creator = (
            {"id": creator_raw["id"], "account": creator_raw["account"], "display_name": creator_raw["display_name"]}
            if creator_raw
            else None
        )
        conv_cnt, conv_latest = conv_stats.get(c.id, (0, None))
        run_cnt, run_latest = run_stats.get(c.id, (0, None))
        file_cnt, storage_bytes = file_stats.get(c.id, (0, 0))
        activity_candidates = [dt for dt in (c.updated_at, conv_latest, run_latest) if dt is not None]
        last_activity = max(activity_candidates) if activity_candidates else c.created_at
        members = sorted(members_by_customer.get(c.id, []), key=lambda item: item["id"])
        rows.append({
            "id": c.id,
            "name": c.name,
            "code": c.code,
            "description": c.description,
            "mascot": c.mascot,
            "creator": creator,
            "member_ids": [m["id"] for m in members],
            "members": members,
            "recipe_count": int(recipe_counts.get(c.id, 0)),
            "formula_count": int(formula_counts.get(c.id, 0)),
            "conversation_count": conv_cnt,
            "run_count": run_cnt,
            "file_count": file_cnt,
            "storage_bytes": storage_bytes,
            "created_at": _iso(c.created_at),
            "last_activity": _iso(last_activity),
        })
    return rows


def set_customer_members(db: Session, customer_id: str, user_ids: list[int]) -> dict[str, Any]:
    """为指定客户设置已授权成员列表（同步 user_customers 表）。"""
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise NotFound("客户不存在")
    wanted = list(dict.fromkeys(int(uid) for uid in user_ids))
    if wanted:
        existing = set(db.scalars(select(User.id).where(User.id.in_(wanted))))
        if any(uid not in existing for uid in wanted):
            raise NotFound("指定的成员不存在")
    db.execute(delete(UserCustomer).where(UserCustomer.customer_id == customer_id))
    for uid in wanted:
        db.add(UserCustomer(user_id=uid, customer_id=customer_id))
    db.flush()
    for row in list_customers(db):
        if row["id"] == customer_id:
            return row
    raise NotFound("客户不存在")


def overview(db: Session, actor: User) -> dict[str, Any]:
    """管理后台总览：核心资产计数、系统配置健康态、近 14 天趋势与最近活跃项。"""
    users_rows = list_users(db)
    customers_rows = list_customers(db)

    admins = sum(1 for u in users_rows if u["role"] == "admin")
    members = len(users_rows) - admins
    recipes_total = int(db.scalar(select(func.count(func.distinct(RecipeRecord.recipe_id)))) or 0)
    conversations_total = int(db.scalar(select(func.count()).select_from(Conversation)) or 0)
    files_total = int(db.scalar(select(func.count()).select_from(FileRecord)) or 0)
    storage_bytes = int(db.scalar(select(func.coalesce(func.sum(FileRecord.size), 0))) or 0)
    runs_total = int(db.scalar(select(func.count()).select_from(Run)) or 0)
    runs_success = int(db.scalar(select(func.count()).select_from(Run).where(Run.status == "success")) or 0)

    custom_formulas = int(
        db.scalar(
            select(func.count(func.distinct(FormulaRecord.formula_id))).where(FormulaRecord.scope == "customer")
        )
        or 0
    )
    catalog = recipe_service.formula_catalog(db, actor)
    builtin_formulas = sum(1 for f in catalog.get("builtin", []) if f.get("id") != "custom_sql")

    llm_cfg = settings_service.get_llm_config()
    provider_names = {p["id"]: p["name"] for p in llm_cfg.get("providers", [])}
    active_pid = llm_cfg.get("active_provider_id")
    privacy_cfg = settings_service.get_privacy_config()
    privacy_llm_cfg = settings_service.get_llm_config(PRIVACY_LLM_ROOT)

    recent_metrics = admin_execution_metrics(db, days=14)

    active_users = sorted(
        users_rows,
        key=lambda u: (u["last_active_at"] or "", u["task_count"], u["conversation_count"]),
        reverse=True,
    )[:6]
    active_customers = sorted(
        customers_rows,
        key=lambda c: (c["last_activity"] or "", c["conversation_count"], c["recipe_count"]),
        reverse=True,
    )[:6]

    return {
        "counts": {
            "users": len(users_rows),
            "admins": admins,
            "members": members,
            "customers": len(customers_rows),
            "recipes": recipes_total,
            "conversations": conversations_total,
            "files": files_total,
            "storage_bytes": storage_bytes,
            "formulas_custom": custom_formulas,
            "formulas_builtin": builtin_formulas,
            "runs_total": runs_total,
            "runs_success": runs_success,
        },
        "system": {
            "llm": {
                "configured": bool(llm_cfg.get("configured")),
                "provider_id": active_pid,
                "provider_name": provider_names.get(active_pid, "") if active_pid else "",
                "model": llm_cfg.get("model") or "",
                "thinking_enabled": bool(llm_cfg.get("thinking_enabled")),
            },
            "privacy": {
                "enabled": bool(privacy_cfg.get("enabled")),
                "enabled_rules": sum(1 for r in privacy_cfg.get("rules", []) if r.get("enabled")),
                "total_rules": len(privacy_cfg.get("rules", [])),
                "llm_configured": bool(privacy_llm_cfg.get("configured")),
                "llm_model": privacy_llm_cfg.get("model") or "",
            },
        },
        "recent_metrics": {
            "days": recent_metrics["days"],
            "tasks": recent_metrics["tasks"],
            "success_rate": recent_metrics["success_rate"],
            "latency": recent_metrics["latency"],
            "usage": recent_metrics["usage"],
            "daily": recent_metrics["daily"],
        },
        "recent_users": active_users,
        "recent_customers": active_customers,
    }
