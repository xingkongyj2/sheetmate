"""客户：卡片列表、详情、新建 / 编辑 / 删除、整体处理逻辑总结、资料库文件、字段映射、记忆、客户公式。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sheetmate.agent.llm import LLMError, get_llm
from sheetmate.api.deps import current_user
from sheetmate.db.database import get_db
from sheetmate.db.models import User
from sheetmate.services import customers as customer_service
from sheetmate.services import files as file_service
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services.auth import require_customer
from sheetmate.services.errors import Forbidden

router = APIRouter(prefix="/api/customers", tags=["customers"])
memories_router = APIRouter(prefix="/api/memories", tags=["memories"])


class CustomerBody(BaseModel):
    name: str | None = None
    code: str | None = None
    description: str | None = None
    summary: str | None = None
    mascot: str | None = None


class MappingBody(BaseModel):
    mapping: dict[str, list[str]]


class MemoryBody(BaseModel):
    category: str = "domain_knowledge"
    key: str
    value: str


@router.get("")
def list_customers(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    return {"ok": True, "customers": [customer_service.card(db, c) for c in customer_service.accessible_customers(db, user)]}


@router.post("")
def create_customer(body: CustomerBody, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    customer = customer_service.create_customer(db, user, body.name or "", body.code or "", body.description or "")
    return {"ok": True, "customer": customer_service.card(db, customer)}


@router.get("/{customer_id}")
def get_customer(customer_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    customer = require_customer(db, user, customer_id)
    return {"ok": True, "customer": customer_service.detail(db, customer)}


@router.put("/{customer_id}")
def update_customer(customer_id: str, body: CustomerBody, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    customer = require_customer(db, user, customer_id)
    customer_service.update_customer(customer, body.model_dump(exclude_none=True))
    return {"ok": True, "customer": customer_service.card(db, customer)}


@router.delete("/{customer_id}")
def delete_customer(customer_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    customer = require_customer(db, user, customer_id)
    if user.role != "admin" and customer.created_by != user.id:
        raise Forbidden("只有创建者或管理员可以删除客户")
    db.delete(customer)
    return {"ok": True}


@router.post("/{customer_id}/summary")
async def refresh_summary(customer_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """重新生成「Excel 整体处理逻辑」说明（配置了模型就由 AI 写，否则按流程结构生成）。"""
    customer = require_customer(db, user, customer_id)
    try:
        llm = get_llm()
    except LLMError:
        llm = None
    summary = await customer_service.refresh_summary(db, customer, llm)
    return {"ok": True, "summary": summary, "by_ai": llm is not None}


@router.post("/{customer_id}/files")
async def upload_customer_file(customer_id: str, file: UploadFile = File(...), kind: str = Form("input"),
                               db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    require_customer(db, user, customer_id)
    record = file_service.save_upload(db, data=await file.read(), filename=file.filename or "file", user=user,
                                      customer_id=customer_id, kind=kind)
    return {"ok": True, "file": file_service.file_dict(record)}


@router.get("/{customer_id}/field-mapping")
def get_mapping(customer_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    require_customer(db, user, customer_id)
    return {"ok": True, "mapping": memory_service.field_mapping_for(db, customer_id)}


@router.put("/{customer_id}/field-mapping")
def put_mapping(customer_id: str, body: MappingBody, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    require_customer(db, user, customer_id)
    return {"ok": True, "mapping": memory_service.replace_field_mappings(db, customer_id, body.mapping)}


@router.post("/{customer_id}/memories")
def add_memory(customer_id: str, body: MemoryBody, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    require_customer(db, user, customer_id)
    item = memory_service.set_memory(db, category=body.category, key=body.key.strip(), value=body.value.strip(),
                                     customer_id=customer_id, source="human")
    return {"ok": True, "memory": memory_service.memory_dict(item)}


@router.get("/{customer_id}/formulas")
def list_formulas(customer_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    require_customer(db, user, customer_id)
    return {"ok": True, "formulas": [recipe_service.formula_dict(f, "customer") for f in recipe_service.customer_formulas(db, customer_id)]}


@router.post("/{customer_id}/formulas")
def save_formula(customer_id: str, body: dict[str, Any], db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """新增 / 升级客户独有公式（SQL 模板，{{参数}} 占位）。"""
    require_customer(db, user, customer_id)
    record = recipe_service.save_formula(db, customer_id, body, created_by="human")
    return {"ok": True, "formula": {"id": record.formula_id, "version": record.version}}


@memories_router.delete("/{memory_id}")
def delete_memory(memory_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """删除一条记忆：客户记忆需要有客户权限，个人记忆只能本人删除。"""
    from sheetmate.db.models import Conversation, Memory

    item = db.get(Memory, memory_id)
    if item is None:
        return {"ok": True}
    if item.customer_id:
        require_customer(db, user, item.customer_id)
    elif item.conversation_id:
        conv = db.get(Conversation, item.conversation_id)
        if conv is None or (conv.user_id != user.id and user.role != "admin"):
            raise Forbidden("你没有权限删除这条记忆")
    elif item.user_id != user.id and user.role != "admin":
        raise Forbidden("你没有权限删除这条记忆")
    memory_service.delete_memory(db, memory_id)
    return {"ok": True}
