"""客户：卡片列表、详情、新建 / 编辑 / 删除、整体处理逻辑总结、资料库文件、字段映射、记忆、客户公式。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from sheetmate.agent.llm import LLMError, get_llm
from sheetmate.api.deps import current_user
from sheetmate.db.database import get_db
from sheetmate.db.models import User
from sheetmate.services import customers as customer_service
from sheetmate.services import files as file_service
from sheetmate.services import formulas as formula_service
from sheetmate.services import formula_generation
from sheetmate.services import memory as memory_service
from sheetmate.services import recipes as recipe_service
from sheetmate.services.auth import require_customer
from sheetmate.services.errors import Forbidden

router = APIRouter(prefix="/api/customers", tags=["customers"])
memories_router = APIRouter(prefix="/api/memories", tags=["memories"])


class TrialInput(BaseModel):
    """试运行用的一张表：客户的文件（可选 sheet），或者粘贴的表格文本（第一行是表头）。"""

    table: str  # 在试运行工作区里的表名，公式的「表」参数填这个名字
    file_id: str | None = None
    sheet: str | None = None
    text: str | None = None


class TrialBody(BaseModel):
    formula: dict[str, Any]  # 完整的公式定义（可以是还没保存的）
    params: dict[str, Any] = {}  # 各个参数的取值
    inputs: list[TrialInput] = []


class FormulaStatusBody(BaseModel):
    status: str  # active / deprecated


class FormulaGenerateBody(BaseModel):
    description: str = Field(min_length=1, max_length=12_000)
    file_ids: list[str] = Field(default_factory=list, max_length=5)
    formula: dict[str, Any] | None = None


class CustomerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    code: str | None = None
    description: str | None = None
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
async def refresh_summary(customer_id: str, only_if_outdated: bool = False,
                          db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """重新生成「Excel 整体处理逻辑」说明（配置了模型就由 AI 写，否则按流程结构生成）。"""
    customer = require_customer(db, user, customer_id)
    try:
        llm = get_llm()
    except LLMError:
        llm = None
    if only_if_outdated:
        summary, updated = await customer_service.upgrade_business_summary(db, customer, llm)
        return {"ok": True, "summary": summary, "updated": updated, "summary_by": customer.summary_by}
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
    """客户独有公式：每个只有最新版本，带「被哪些流程用到」。"""
    require_customer(db, user, customer_id)
    return {"ok": True, "formulas": formula_service.list_with_usage(db, customer_id)}


@router.post("/{customer_id}/formulas")
def save_formula(customer_id: str, body: dict[str, Any], db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """新增 / 升级客户独有公式（SQL 模板，{{参数}} 占位；同一个 id 每保存一次升一个版本）。

    返回保存后的公式；used_by 里的 problems 是「新版本保存后哪些流程的哪一步会出问题」，界面据此提醒。
    """
    require_customer(db, user, customer_id)
    record = recipe_service.save_formula(db, customer_id, body, created_by="human")
    return {"ok": True, "formula": formula_service.get_item(db, customer_id, record.formula_id)}


@router.post("/{customer_id}/formulas/check")
def check_formula(customer_id: str, body: dict[str, Any], db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """表单边写边检查：和保存时的检查完全一样，但不保存。"""
    require_customer(db, user, customer_id)
    return {"ok": True, "problems": formula_service.check_formula(customer_id, body)}


@router.post("/{customer_id}/formulas/generate")
async def generate_formula(customer_id: str, body: FormulaGenerateBody, db: Session = Depends(get_db),
                           user: User = Depends(current_user)) -> dict[str, Any]:
    """根据描述和选中的客户文件生成公式草稿；不保存、不执行。"""
    customer = require_customer(db, user, customer_id)
    result = await formula_generation.generate_formula(
        db, customer, get_llm(), description=body.description, file_ids=body.file_ids, formula=body.formula,
    )
    return {"ok": True, **result}


@router.post("/{customer_id}/formulas/trial")
def trial_formula(customer_id: str, body: TrialBody, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """试运行：在临时工作区里用客户的文件或粘贴的数据跑一遍公式（可以是还没保存的），返回结果预览。

    试运行失败（公式有问题、参数不对、SQL 执行出错）也返回 200，result.status = failed，原因在 result.error。
    """
    require_customer(db, user, customer_id)
    result = formula_service.trial_formula(db, customer_id, body.formula, body.params, [i.model_dump() for i in body.inputs])
    return {"ok": True, "result": result}


@router.put("/{customer_id}/formulas/{formula_id}/status")
def set_formula_status(customer_id: str, formula_id: str, body: FormulaStatusBody, db: Session = Depends(get_db),
                       user: User = Depends(current_user)) -> dict[str, Any]:
    """启用 / 停用客户公式。被流程用到的公式不能停用。"""
    require_customer(db, user, customer_id)
    return {"ok": True, "formula": formula_service.set_status(db, customer_id, formula_id, body.status)}


@router.delete("/{customer_id}/formulas/{formula_id}")
def delete_formula(customer_id: str, formula_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """删除客户公式（所有版本）；引用它的流程保留。"""
    require_customer(db, user, customer_id)
    formula_service.delete_formula(db, customer_id, formula_id)
    return {"ok": True}


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
