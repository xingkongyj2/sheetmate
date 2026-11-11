"""客户：列表卡片、详情（文件 / 流程 / 记忆 / 字段映射 / 运行记录 / 整体处理逻辑总结）、新建、编辑。"""

from __future__ import annotations

import asyncio
import random
import re
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from sheetmate.agent.memory.short_term.summarization import customer_summary, fallback_customer_summary
from sheetmate.db.session import session_scope
from sheetmate.core.config.settings import settings
from sheetmate.db.models import (
    Conversation,
    Customer,
    FileRecord,
    RecipeProfile,
    RecipeRecord,
    Run,
    User,
    UserCustomer,
    now,
)
from sheetmate.services.files import service as file_service
from sheetmate.services.formulas import service as formula_service
from sheetmate.services.memory import long_term as memory_service
from sheetmate.services.workflows import service as recipe_service
from sheetmate.services.workflows import runs as run_service
from sheetmate.core.errors import ServiceError

MASCOTS = ["blob", "rocket", "briefcase", "megaphone", "folder", "puzzle", "crown", "book", "beret"]
SUMMARY_POLICY_VERSION = 1  # 用户可见说明只使用流程资料和用户维护的记忆。


def accessible_customers(db: Session, user: User) -> list[Customer]:
    stmt = select(Customer).order_by(Customer.updated_at.desc())
    if user.role != "admin":
        linked = select(UserCustomer.customer_id).where(UserCustomer.user_id == user.id)
        stmt = stmt.where(or_(Customer.id.in_(linked), Customer.created_by == user.id))
    return list(db.scalars(stmt))


def create_customer(db: Session, user: User, name: str, code: str = "", description: str = "") -> Customer:
    name = name.strip()
    if not name:
        raise ServiceError("客户名称不能为空")
    customer = Customer(name=name[:120], code=code.strip()[:60], description=description.strip(),
                        mascot=random.choice(MASCOTS), created_by=user.id)
    db.add(customer)
    db.flush()
    db.add(UserCustomer(user_id=user.id, customer_id=customer.id))
    db.flush()
    return customer


def update_customer(customer: Customer, data: dict[str, Any], db: Session | None = None) -> Customer:
    for key in ("name", "code", "description", "mascot"):
        if key in data and data[key] is not None:
            setattr(customer, key, str(data[key]).strip())
    if not customer.name:
        raise ServiceError("客户名称不能为空")
    if "primary_recipe_id" in data and db is not None:
        old_primary = recipe_service.ensure_primary_recipe(db, customer)
        if old_primary:
            file_service.recipe_files(db, customer.id, old_primary, primary_recipe_id=old_primary)
        record = recipe_service.resolve_customer_recipe(db, customer.id, data["primary_recipe_id"])
        customer.primary_recipe_id = record.recipe_id if record else None
    return customer


def delete_customer(db: Session, customer: Customer) -> None:
    """Delete the customer and all conversations/resources owned by it."""
    # 先删除客户会话，确保消息、会话工作区、会话运行记录都不会悬空。
    from sheetmate.services.conversations import service as conversation_service
    conversations = list(db.scalars(select(Conversation).where(Conversation.customer_id == customer.id)))
    conversation_ids = {conversation.id for conversation in conversations}
    from sheetmate.services.conversations import service as conversation_service
    for conversation in conversations:
        conversation_service.delete_conversation(db, conversation)

    records = list(db.scalars(select(FileRecord).where(file_service.customer_file_condition(customer.id))))
    for record in records:
        # 释放客户侧引用；其他客户/会话引用会继续保留原件。
        file_service.release_customer_reference(db, record, customer.id)

    # 客户级流程记录的 customer_id 是 SET NULL，必须显式删除，避免孤儿版本继续占据 data/app.db。
    db.execute(delete(RecipeRecord).where(RecipeRecord.customer_id == customer.id))

    # 没有会话归属的客户运行也必须清理运行工作区和运行文件。
    standalone_runs = list(db.scalars(select(Run).where(Run.customer_id == customer.id)))
    standalone_run_ids = [run.id for run in standalone_runs]
    standalone_file_ids = {file_id for run in standalone_runs for file_id in conversation_service._run_file_ids(run)}
    standalone_run_files = list(db.scalars(select(FileRecord).where(or_(
        FileRecord.run_id.in_(standalone_run_ids), FileRecord.id.in_(standalone_file_ids)
    )))) if standalone_run_ids or standalone_file_ids else []
    for run in standalone_runs:
        file_service.defer_after_commit(db, lambda run_id=run.id: run_service.run_workspaces.delete(run_id))
    if standalone_run_ids:
        db.execute(delete(Run).where(Run.id.in_(standalone_run_ids)))

    db.delete(customer)
    db.flush()
    for record in {item.id: item for item in [*records, *standalone_run_files]}.values():
        current = db.get(FileRecord, record.id)
        if current is not None:
            db.refresh(current)
            file_service.delete_if_unowned(db, current)

    from sheetmate.services.scripts.cleanup import queue_unreferenced_scope_scripts
    queue_unreferenced_scope_scripts(db, customer_ids={customer.id}, conversation_ids=conversation_ids)
    roots = [settings.outputs_dir / customer.id, settings.uploads_dir / customer.id]
    # 会话目录由 delete_conversation 处理；客户目录在最后一个引用释放后再收缩。
    conversation_service._defer_owner_storage_cleanup(db, roots)


def card(db: Session, customer: Customer) -> dict[str, Any]:
    """客户卡片：名称、IP 形象、文件数、流程数、最近活动、处理逻辑摘要。"""
    file_count = db.scalar(select(func.count()).select_from(FileRecord).where(
        file_service.customer_file_condition(customer.id), file_service.current_file_condition(),
        FileRecord.kind != "output")) or 0
    recipes = recipe_service.latest_records(db, customer_id=customer.id)
    primary_id = recipe_service.ensure_primary_recipe(db, customer)
    conv_count = db.scalar(select(func.count()).select_from(Conversation).where(Conversation.customer_id == customer.id)) or 0
    last_run = db.scalars(select(Run).where(Run.customer_id == customer.id).order_by(Run.created_at.desc()).limit(1)).first()
    primary = next((record for record in recipes if record.recipe_id == primary_id), None)
    target = _summary_for_display(db, customer, primary)
    summary = (target.summary or "").strip()
    excerpt = _first_paragraph(summary) if summary else (customer.description or "")
    return {
        "id": customer.id,
        "name": customer.name,
        "code": customer.code,
        "mascot": customer.mascot or "blob",
        "description": customer.description,
        "primary_recipe_id": primary_id,
        "excerpt": excerpt[:120],
        "file_count": int(file_count),
        "recipe_count": len(recipes),
        "conversation_count": int(conv_count),
        "recipes": [{"recipe_id": r.recipe_id, "name": r.name, "version": r.version,
                     "steps": len((r.body or {}).get("steps", []))} for r in recipes[:3]],
        "last_activity": (last_run.created_at if last_run else customer.updated_at).isoformat(),
        "has_summary": bool(summary),
    }


def _first_paragraph(markdown: str) -> str:
    """卡片摘要：优先取「整体处理逻辑」小节的第一段，去掉 Markdown 符号。"""
    lines = markdown.splitlines()
    start = next((i for i, line in enumerate(lines) if line.lstrip().startswith("#") and "处理逻辑" in line), None)
    candidates = lines[start + 1 :] if start is not None else lines
    for line in candidates:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            continue
        text = re.sub(r"[*_`>#]+", "", raw).lstrip("-• ").strip()
        text = re.sub(r"^\d+\.\s*", "", text)
        if text:
            return text
    return ""


def detail(db: Session, customer: Customer) -> dict[str, Any]:
    primary_id = recipe_service.ensure_primary_recipe(db, customer)
    files = file_service.list_files(db, customer_id=customer.id)
    output_count = db.scalar(select(func.count()).select_from(FileRecord).where(
        file_service.customer_file_condition(customer.id), file_service.current_file_condition(),
        FileRecord.kind == "output")) or 0
    records = recipe_service.latest_records(db, customer_id=customer.id)
    recipes = []
    for record in records:
        recipe = recipe_service.record_to_recipe(record)
        profile = _summary_for_display(db, customer, record)
        resources = file_service.recipe_files(db, customer.id, record.recipe_id, primary_recipe_id=primary_id)
        if not profile.summary:
            inputs = [file_service.file_dict(item) for item in resources if item.kind != "output"]
            _, _, context = _summary_inputs(db, customer, record.recipe_id)
            profile.summary = fallback_customer_summary(recipe.name, inputs, [recipe], context)
            profile.summary_updated_at = now()
            profile.summary_policy_version = SUMMARY_POLICY_VERSION
        recipes.append({**recipe_service.recipe_brief(record), "flow": recipe.flow(), "recipe": recipe.to_dict(),
                        "summary": profile.summary, "summary_by": profile.summary_by,
                        "summary_updated_at": profile.summary_updated_at.isoformat() if profile.summary_updated_at else None,
                        "files": [file_service.file_dict(item) for item in resources if item.kind != "output"],
                        "output_files": [file_service.file_dict(item) for item in resources if item.kind == "output"],
                        "memories": memory_service.customer_visible_memories(db, customer.id, recipe_id=record.recipe_id),
                        "field_mapping": memory_service.field_mapping_for(db, customer.id, recipe_id=record.recipe_id),
                        "field_mapping_entries": memory_service.field_mapping_entries(db, customer.id, recipe_id=record.recipe_id),
                        "runs": [run_service.run_dict(run) for run in db.scalars(select(Run).where(
                            Run.customer_id == customer.id, Run.recipe_id == record.recipe_id).order_by(Run.created_at.desc()))]})
    conversations = db.scalars(select(Conversation).where(Conversation.customer_id == customer.id)
                               .order_by(Conversation.updated_at.desc()))
    primary = next((item for item in recipes if item["recipe_id"] == primary_id), None)
    if primary is None:
        _summary_for_display(db, customer)
    return {
        **card(db, customer),
        "summary": primary["summary"] if primary else customer.summary,
        "summary_by": primary["summary_by"] if primary else customer.summary_by,
        "summary_updated_at": primary["summary_updated_at"] if primary else customer.summary_updated_at.isoformat() if customer.summary_updated_at else None,
        "output_count": int(output_count),
        "files": [file_service.file_dict(f) for f in files if f.kind != "output"],
        "outputs": [file_service.file_dict(f) for f in files if f.kind == "output"][:20],
        "recipes": recipes,
        "memories": memory_service.customer_visible_memories(db, customer.id, recipe_id=primary_id),
        "field_mapping": memory_service.field_mapping_for(db, customer.id, recipe_id=primary_id),
        "field_mapping_entries": memory_service.field_mapping_entries(db, customer.id, recipe_id=primary_id),
        "formulas": formula_service.list_with_usage(db, customer.id),
        "runs": [run_service.run_dict(r, with_steps=False) for r in run_service.list_runs(db, customer_id=customer.id, limit=20)],
        "conversations": [{"id": c.id, "title": c.title, "mode": c.mode, "updated_at": c.updated_at.isoformat()} for c in conversations],
    }


def _summary_inputs(db: Session, customer: Customer, recipe_id: str | None = None) -> tuple[list[dict[str, Any]], list[Any], str]:
    """面向用户的说明只使用流程资料和人工规则；AI 辅助记忆留在执行上下文。"""
    if recipe_id:
        record = recipe_service.resolve_customer_recipe(db, customer.id, recipe_id)
        resources = file_service.recipe_files(db, customer.id, recipe_id, primary_recipe_id=customer.primary_recipe_id)
        files = [file_service.file_dict(f) for f in resources if f.kind != "output"]
        recipes = [recipe_service.record_to_recipe(record)]
    else:
        files = [file_service.file_dict(f) for f in file_service.list_files(db, customer_id=customer.id) if f.kind != "output"]
        recipes = [recipe_service.record_to_recipe(r) for r in recipe_service.latest_records(db, customer_id=customer.id)]
    memories = memory_service.customer_memories(db, customer.id, recipe_id=recipe_id, source="human")
    memory_text = "\n".join(f"- {item['value']}" for item in memories)
    return files, recipes, memory_text


def _summary_for_display(db: Session, customer: Customer, record: RecipeRecord | None = None) -> Customer | RecipeProfile:
    """旧自动总结可能混入 AI 习惯；展示前从允许公开的资料重建一次。"""
    target = recipe_service.ensure_profile(db, record) if record else customer
    if (target.summary and target.summary_by != "human"
            and (target.summary_policy_version or 0) < SUMMARY_POLICY_VERSION):
        files, recipes, memory_text = _summary_inputs(db, customer, record.recipe_id if record else None)
        target.summary = fallback_customer_summary(record.name if record else customer.name, files, recipes, memory_text)
        target.summary_updated_at = now()
        target.summary_policy_version = SUMMARY_POLICY_VERSION
    return target


async def _compose_summary(name: str, files: list[dict[str, Any]], recipes: list[Any], memory_text: str, llm: Any | None) -> str:
    """有模型用模型写，没有模型（或调用失败）时用流程结构直接生成，保证客户页始终能看懂。"""
    if llm is not None:
        try:
            text = await customer_summary(llm, name, files, recipes, memory_text)
            if text:
                return text
        except Exception:
            pass
    return fallback_customer_summary(name, files, recipes, memory_text)


async def refresh_summary(db: Session, customer: Customer, llm: Any | None, recipe_id: str | None = None) -> str:
    """手动「重新总结」：重新生成并恢复为自动更新。"""
    record = recipe_service.resolve_customer_recipe(db, customer.id, recipe_id)
    target = recipe_service.ensure_profile(db, record) if record else customer
    files, recipes, memory_text = _summary_inputs(db, customer, record.recipe_id if record else None)
    text = await _compose_summary(record.name if record else customer.name, files, recipes, memory_text, llm)
    target.summary = text
    target.summary_updated_at = now()
    target.summary_by = "ai"
    target.summary_policy_version = SUMMARY_POLICY_VERSION
    return text


def needs_business_summary(customer: Customer) -> bool:
    return (customer.summary_by == "ai" and bool((customer.summary or "").strip())
            and not re.search(r"(?m)^#{1,3}\s+这项工作做什么\s*$", customer.summary))


async def upgrade_business_summary(db: Session, customer: Customer, llm: Any | None,
                                   recipe_id: str | None = None) -> tuple[str, bool]:
    record = recipe_service.resolve_customer_recipe(db, customer.id, recipe_id)
    target = recipe_service.ensure_profile(db, record) if record else customer
    previous = target.summary
    target = _summary_for_display(db, customer, record)
    sanitized = previous != target.summary
    if not needs_business_summary(target) or llm is None:
        return target.summary, sanitized
    original = target.summary
    files, recipes, memory_text = _summary_inputs(db, customer, record.recipe_id if record else None)
    try:
        text = await customer_summary(llm, record.name if record else customer.name, files, recipes, memory_text, previous_summary=original)
    except Exception:
        return target.summary, sanitized
    if not re.search(r"(?m)^#{1,3}\s+这项工作做什么\s*$", text):
        return target.summary, sanitized
    model = RecipeProfile if record else Customer
    identity = RecipeProfile.recipe_id == record.recipe_id if record else Customer.id == customer.id
    changed = db.execute(
        update(model)
        .where(identity, model.summary_by == "ai", model.summary == original)
        .values(summary=text, summary_updated_at=now(), summary_policy_version=SUMMARY_POLICY_VERSION)
    ).rowcount == 1
    if not changed:
        db.refresh(target, ["summary", "summary_by", "summary_updated_at", "summary_policy_version"])
    return target.summary, changed


_refreshing: set[str] = set()
_background: set[asyncio.Task] = set()


def schedule_summary_refresh(customer_id: str | None) -> None:
    """流程或客户资料变化后，在后台自动更新客户的整体处理逻辑（同一客户同时只跑一个）。必须在事件循环里调用。"""
    if not customer_id or customer_id in _refreshing:
        return
    try:
        task = asyncio.get_running_loop().create_task(_auto_refresh(customer_id))
    except RuntimeError:  # 没有事件循环（例如同步脚本里），跳过
        return
    _background.add(task)
    task.add_done_callback(_background.discard)


async def auto_refresh_summary(customer_id: str | None) -> None:
    """FastAPI BackgroundTasks 用：响应发出后在事件循环里更新总结。"""
    if customer_id and customer_id not in _refreshing:
        await _auto_refresh(customer_id)


async def _auto_refresh(customer_id: str) -> None:
    from sheetmate.agent.llm.client import LLMError, get_llm

    _refreshing.add(customer_id)
    try:
        # 分两段事务，逐个流程整理，调用模型时不占用数据库事务。
        with session_scope() as db:
            customer = db.get(Customer, customer_id)
            if customer is None:
                return
            recipe_service.ensure_primary_recipe(db, customer)
            records = recipe_service.latest_records(db, customer_id=customer_id)
            materials = []
            for record in records:
                profile = recipe_service.ensure_profile(db, record)
                if profile.summary_by != "human":
                    materials.append((record.recipe_id, record.version, record.name,
                                      _summary_inputs(db, customer, record.recipe_id)))
            if not records and customer.summary_by != "human":
                materials.append((None, None, customer.name, _summary_inputs(db, customer)))
        try:
            llm = get_llm()
        except LLMError:
            llm = None
        for recipe_id, version, name, (files, recipes, memory_text) in materials:
            text = await _compose_summary(name, files, recipes, memory_text, llm)
            with session_scope() as db:
                target = db.get(RecipeProfile, recipe_id) if recipe_id else db.get(Customer, customer_id)
                if target is None or target.summary_by == "human":
                    continue
                latest = db.scalar(select(func.max(RecipeRecord.version)).where(RecipeRecord.recipe_id == recipe_id)) if recipe_id else None
                if recipe_id and latest != version:
                    continue
                target.summary = text
                target.summary_updated_at = now()
                target.summary_policy_version = SUMMARY_POLICY_VERSION
    except Exception:
        pass  # 总结只是锦上添花，失败不影响主流程
    finally:
        _refreshing.discard(customer_id)


def recipe_count(db: Session, customer_id: str) -> int:
    return db.scalar(select(func.count(func.distinct(RecipeRecord.recipe_id))).where(RecipeRecord.customer_id == customer_id)) or 0


async def refresh_customer_summary(db: Session, customer: Customer, *, only_if_outdated: bool = False,
                                   recipe_id: str | None = None) -> dict[str, Any]:
    """刷新客户说明，统一处理无模型回退和旧版本说明升级。"""
    from sheetmate.agent.llm import LLMError, get_llm

    try:
        llm = get_llm()
    except LLMError:
        llm = None
    if only_if_outdated:
        summary, updated = await upgrade_business_summary(db, customer, llm, recipe_id=recipe_id)
        record = recipe_service.resolve_customer_recipe(db, customer.id, recipe_id)
        target = recipe_service.ensure_profile(db, record) if record else customer
        return {"summary": summary, "updated": updated, "summary_by": target.summary_by}
    summary = await refresh_summary(db, customer, llm, recipe_id=recipe_id)
    return {"summary": summary, "by_ai": llm is not None}


async def generate_customer_formula(db: Session, customer: Customer, *, description: str,
                                    file_ids: list[str], formula: dict[str, Any] | None = None) -> dict[str, Any]:
    """以当前模型生成客户公式，API 层只处理请求和权限。"""
    from sheetmate.agent.llm import get_llm
    from sheetmate.services.formulas.generation import generate_formula

    return await generate_formula(db, customer, get_llm(), description=description, file_ids=file_ids, formula=formula)
