"""客户：列表卡片、详情（文件 / 流程 / 记忆 / 字段映射 / 运行记录 / 整体处理逻辑总结）、新建、编辑。"""

from __future__ import annotations

import asyncio
import random
import re
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from sheetmate.agent.summarizer import customer_summary, fallback_customer_summary
from sheetmate.db.database import session_scope
from sheetmate.db.models import Conversation, Customer, FileRecord, RecipeRecord, Run, User, UserCustomer, now
from sheetmate.services import files as file_service
from sheetmate.services import formulas as formula_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services import runs as run_service
from sheetmate.services.errors import ServiceError

MASCOTS = ["blob", "rocket", "briefcase", "megaphone", "folder", "puzzle", "crown", "book", "beret"]


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


def update_customer(customer: Customer, data: dict[str, Any]) -> Customer:
    for key in ("name", "code", "description", "summary", "mascot"):
        if key in data and data[key] is not None:
            setattr(customer, key, str(data[key]).strip())
    if data.get("summary") is not None:
        customer.summary_updated_at = now()
        customer.summary_by = "human"  # 手动编辑过的总结不再被自动覆盖
    if not customer.name:
        raise ServiceError("客户名称不能为空")
    return customer


def card(db: Session, customer: Customer) -> dict[str, Any]:
    """客户卡片：名称、IP 形象、文件数、流程数、最近活动、处理逻辑摘要。"""
    file_count = db.scalar(select(func.count()).select_from(FileRecord).where(
        FileRecord.customer_id == customer.id, FileRecord.kind != "output")) or 0
    recipes = recipe_service.latest_records(db, customer_id=customer.id)
    conv_count = db.scalar(select(func.count()).select_from(Conversation).where(Conversation.customer_id == customer.id)) or 0
    last_run = db.scalars(select(Run).where(Run.customer_id == customer.id).order_by(Run.created_at.desc()).limit(1)).first()
    summary = (customer.summary or "").strip()
    excerpt = _first_paragraph(summary) if summary else (customer.description or "")
    return {
        "id": customer.id,
        "name": customer.name,
        "code": customer.code,
        "mascot": customer.mascot or "blob",
        "description": customer.description,
        "excerpt": excerpt[:120],
        "file_count": int(file_count),
        "recipe_count": len(recipes),
        "conversation_count": int(conv_count),
        "recipes": [{"recipe_id": r.recipe_id, "name": r.name, "version": r.version, "status": r.status,
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
    files = file_service.list_files(db, customer_id=customer.id)
    records = recipe_service.latest_records(db, customer_id=customer.id, include_deprecated=True)
    recipes = []
    for record in records:
        recipe = recipe_service.record_to_recipe(record)
        recipes.append({**recipe_service.recipe_brief(record), "flow": recipe.flow(), "recipe": recipe.to_dict()})
    conversations = db.scalars(select(Conversation).where(Conversation.customer_id == customer.id)
                               .order_by(Conversation.updated_at.desc()).limit(20))
    return {
        **card(db, customer),
        "summary": customer.summary,
        "summary_by": customer.summary_by,
        "summary_updated_at": customer.summary_updated_at.isoformat() if customer.summary_updated_at else None,
        "files": [file_service.file_dict(f) for f in files if f.kind != "output"],
        "outputs": [file_service.file_dict(f) for f in files if f.kind == "output"][:20],
        "recipes": recipes,
        "memories": memory_service.customer_memories(db, customer.id),
        "field_mapping": memory_service.field_mapping_for(db, customer.id),
        "formulas": formula_service.list_with_usage(db, customer.id),
        "runs": [run_service.run_dict(r, with_steps=False) for r in run_service.list_runs(db, customer_id=customer.id, limit=20)],
        "conversations": [{"id": c.id, "title": c.title, "mode": c.mode, "updated_at": c.updated_at.isoformat()} for c in conversations],
    }


def _summary_inputs(db: Session, customer: Customer) -> tuple[list[dict[str, Any]], list[Any], str]:
    """写总结需要的材料：上传过的文件、当前流程、客户记忆和字段映射。"""
    files = [file_service.file_dict(f) for f in file_service.list_files(db, customer_id=customer.id) if f.kind != "output"]
    recipes = [recipe_service.record_to_recipe(r) for r in recipe_service.latest_records(db, customer_id=customer.id)]
    memories = memory_service.customer_memories(db, customer.id)
    mapping = memory_service.field_mapping_for(db, customer.id)
    memory_text = "\n".join(f"- {m['category_label']}：{m['key']} = {m['value']}" for m in memories)
    if mapping:
        memory_text += "\n" + "\n".join(f"- 字段映射：{std} ← {'、'.join(aliases)}" for std, aliases in mapping.items())
    return files, recipes, memory_text


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


async def refresh_summary(db: Session, customer: Customer, llm: Any | None) -> str:
    """手动「重新总结」：重新生成并恢复为自动更新。"""
    files, recipes, memory_text = _summary_inputs(db, customer)
    text = await _compose_summary(customer.name, files, recipes, memory_text, llm)
    customer.summary = text
    customer.summary_updated_at = now()
    customer.summary_by = "ai"
    return text


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
    from sheetmate.agent.llm import LLMError, get_llm

    _refreshing.add(customer_id)
    try:
        # 分两段事务：先取材料，调模型时不占着数据库
        with session_scope() as db:
            customer = db.get(Customer, customer_id)
            if customer is None or customer.summary_by == "human":
                return
            name = customer.name
            files, recipes, memory_text = _summary_inputs(db, customer)
        try:
            llm = get_llm()
        except LLMError:
            llm = None
        text = await _compose_summary(name, files, recipes, memory_text, llm)
        with session_scope() as db:
            customer = db.get(Customer, customer_id)
            if customer is not None and customer.summary_by != "human":
                customer.summary = text
                customer.summary_updated_at = now()
    except Exception:
        pass  # 总结只是锦上添花，失败不影响主流程
    finally:
        _refreshing.discard(customer_id)


def recipe_count(db: Session, customer_id: str) -> int:
    return db.scalar(select(func.count(func.distinct(RecipeRecord.recipe_id))).where(RecipeRecord.customer_id == customer_id)) or 0
