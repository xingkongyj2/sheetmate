"""会话：列表 / 新建 / 详情 / 修改 / 删除 / 保存到客户、上传文件、工作区表格、对话（SSE，可选思考模式）、澄清、停止。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user, sse_response
from sheetmate.db.database import get_db, session_scope
from sheetmate.db.models import User
from sheetmate.conversation_log import bind_trace, http_request_data, new_turn, record_event
from sheetmate.services import chat as chat_service
from sheetmate.services import conversations as conv_service
from sheetmate.services import customers as customer_service
from sheetmate.services import files as file_service
from sheetmate.services.auth import COOKIE_NAME, require_customer
from sheetmate.services.errors import Conflict, ServiceError
from sheetmate.services.jobs import jobs

class ConversationLogRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        if not self.path.endswith(("/{conversation_id}/chat", "/{conversation_id}/clarify")):
            return handler

        async def handle(request: Request):
            # Preserve the body before Pydantic can reject it. Only authorised
            # conversations are written, including invalid JSON / missing fields.
            original = await http_request_data(request)
            request.scope["sheetmate.http_request"] = original
            try:
                return await handler(request)
            except RequestValidationError as exc:
                conversation_id = request.path_params.get("conversation_id")
                try:
                    with session_scope() as db:
                        user = current_user(db=db, sm_session=request.cookies.get(COOKIE_NAME))
                        conv_service.get_conversation(db, user, conversation_id)
                except ServiceError:
                    raise exc
                with bind_trace(new_turn(conversation_id)):
                    function = "sheetmate.api.conversations.ConversationLogRoute.handle"
                    record_event("turn.start", function, {"user_id": user.id, "source": "validation"})
                    record_event("chat.input", function, original)
                    record_event("chat.rejected", function, {"errors": [
                        {key: value for key, value in error.items() if key != "input"} for error in exc.errors()]})
                    record_event("turn.end", function, {"status": "rejected", "reason": "request_validation"})
                raise

        return handle


router = APIRouter(prefix="/api/conversations", tags=["conversations"], route_class=ConversationLogRoute)


class CreateBody(BaseModel):
    customer_id: str | None = None
    recipe_id: str | None = None
    title: str = ""


class UpdateBody(BaseModel):
    title: str | None = None
    customer_id: str | None = None
    recipe_id: str | None = None


class BindBody(BaseModel):
    customer_id: str | None = None
    new_customer: dict[str, Any] | None = None


class ChatBody(BaseModel):
    message: str
    plan_steps: list[dict[str, Any]] | None = None  # 调用方自带的计划步骤（高级用法；界面不再让用户确认计划）
    attachments: list[str] | None = None
    thinking: bool = False  # 思考模式：后端先规划、再逐步执行并核对，不需要用户确认计划


class MessageBody(BaseModel):
    message: str
    attachments: list[str] | None = None


class LoadFileBody(BaseModel):
    file_id: str


class RoleBody(BaseModel):
    role: str


@router.get("")
def list_conversations(customer_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    convs = conv_service.list_conversations(db, user, customer_id=customer_id)
    return {"ok": True, "conversations": [{**conv_service.conversation_dict(db, c), "running": jobs.running(c.id)} for c in convs]}


@router.post("")
def create_conversation(body: CreateBody, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    conv = conv_service.create_conversation(db, user, customer_id=body.customer_id, title=body.title, recipe_id=body.recipe_id)
    return {"ok": True, "conversation": conv_service.conversation_dict(db, conv)}


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    conv = conv_service.get_conversation(db, user, conversation_id)
    if conv.customer_id:
        require_customer(db, user, conv.customer_id)
    job = jobs.get(conv.id)
    running_job = {"id": job.id, "message_id": job.message_id} if job else None
    return {"ok": True, "conversation": {**conv_service.detail(db, conv), "running": job is not None,
                                         "running_job": running_job}}


@router.get("/{conversation_id}/events")
async def conversation_events(conversation_id: str, job_id: str, request: Request, db: Session = Depends(get_db),
                              user: User = Depends(current_user)):
    conv = conv_service.get_conversation(db, user, conversation_id)
    if conv.customer_id:
        require_customer(db, user, conv.customer_id)
    job = jobs.get(conv.id)
    if job is None or job.id != job_id:
        raise Conflict("任务状态已更新，请重新加载对话")
    return sse_response(job)


@router.put("/{conversation_id}")
def update_conversation(conversation_id: str, body: UpdateBody, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    conv = conv_service.get_conversation(db, user, conversation_id)
    conv_service.update_conversation(db, conv, user, body.model_dump(exclude_unset=True))
    return {"ok": True, "conversation": conv_service.conversation_dict(db, conv)}


@router.delete("/{conversation_id}")
def delete_conversation(conversation_id: str, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    conv = conv_service.get_conversation(db, user, conversation_id)
    jobs.stop(conversation_id)
    conv_service.delete_conversation(db, conv)
    return {"ok": True}


@router.post("/{conversation_id}/bind")
def bind_customer(conversation_id: str, body: BindBody, background: BackgroundTasks, db: Session = Depends(get_db),
                  user: User = Depends(current_user)) -> dict[str, Any]:
    """保存到客户：选择已有客户或新建客户，会话的文件、流程、记忆一起归到客户名下，然后后台更新客户总结。"""
    conv = conv_service.get_conversation(db, user, conversation_id)
    customer = conv_service.bind_to_customer(db, conv, user, customer_id=body.customer_id, new_customer=body.new_customer)
    db.commit()  # 先提交，后台任务才能读到迁移后的数据
    background.add_task(customer_service.auto_refresh_summary, customer.id)
    return {"ok": True, "conversation": conv_service.conversation_dict(db, conv), "customer_id": customer.id}


@router.post("/{conversation_id}/files")
async def upload_file(conversation_id: str, file: UploadFile = File(...), kind: str = Form("input"),
                      db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """上传到会话并立即加载到工作区。绑定了客户的会话，文件同时进入客户资料库。"""
    conv = conv_service.get_conversation(db, user, conversation_id)
    record = file_service.save_upload(db, data=await file.read(), filename=file.filename or "file", user=user,
                                      customer_id=conv.customer_id, conversation_id=conv.id, kind=kind)
    if conv.customer_id and conv.recipe_id:
        file_service.retain_for_recipe(db, record, conv.customer_id, conv.recipe_id)
    tables = file_service.load_into_workspace(conv_service.workspace(conv.id), record)
    return {"ok": True, "file": file_service.file_dict(record),
            "tables": [conv_service._table_brief(t, conversation=conv) for t in tables]}


@router.post("/{conversation_id}/load-file")
def load_file(conversation_id: str, body: LoadFileBody, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    conv = conv_service.get_conversation(db, user, conversation_id)
    record = file_service.get_file(db, body.file_id)
    if not file_service.can_access_in_conversation(db, record, conversation_id=conv.id, customer_id=conv.customer_id):
        raise ServiceError("只能加载本会话或当前客户的文件")
    file_service.retain_for_conversation(db, record, conv.id)
    if conv.customer_id and conv.recipe_id:
        file_service.retain_for_recipe(db, record, conv.customer_id, conv.recipe_id)
    tables = file_service.load_into_workspace(conv_service.workspace(conv.id), record)
    return {"ok": True, "tables": [conv_service._table_brief(t, conversation=conv) for t in tables]}


@router.get("/{conversation_id}/tables/{name}")
def table_page(conversation_id: str, name: str, offset: int = 0, limit: int = 50,
               source_conversation_id: str | None = None,
               db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    source = conv_service.table_source(db, user, conversation_id, source_conversation_id)
    return {"ok": True, "table": conv_service.workspace(source.id).page(name, offset, min(limit, 500))}


@router.delete("/{conversation_id}/tables/{name}")
def drop_table(conversation_id: str, name: str, source_conversation_id: str | None = None,
               db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    source = conv_service.table_source(db, user, conversation_id, source_conversation_id)
    conv_service.workspace(source.id).drop(name)
    return {"ok": True}


@router.put("/{conversation_id}/tables/{name}/role")
def set_table_role(conversation_id: str, name: str, body: RoleBody, source_conversation_id: str | None = None,
                   db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    """标记一张表是输入，还是人工做好的期望结果（生成流程时用来比对）。"""
    source = conv_service.table_source(db, user, conversation_id, source_conversation_id)
    if body.role not in {"input", "expected"}:
        raise ServiceError("role 只能是 input 或 expected")
    info = conv_service.workspace(source.id).update_meta(name, role=body.role)
    return {"ok": True, "table": conv_service._table_brief(info, conversation=source)}


@router.post("/{conversation_id}/chat")
async def chat(conversation_id: str, body: ChatBody, request: Request, user: User = Depends(current_user)):
    # 必须是 async：后台任务要挂在服务的事件循环上（同步接口在线程池里执行，没有事件循环）
    job = chat_service.start_chat(user, conversation_id, body.message, plan_steps=body.plan_steps,
                                  attachments=body.attachments, thinking=body.thinking,
                                  http_request=await http_request_data(request))
    return sse_response(job)


@router.post("/{conversation_id}/clarify")
async def clarify(conversation_id: str, body: MessageBody, request: Request, user: User = Depends(current_user)) -> dict[str, Any]:
    return {"ok": True, **await chat_service.clarify(user, conversation_id, body.message,
                                                  http_request=await http_request_data(request))}


@router.post("/{conversation_id}/stop")
async def stop(conversation_id: str, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict[str, Any]:
    conv_service.get_conversation(db, user, conversation_id)
    result = {"ok": True, "stopped": jobs.stop(conversation_id)}
    return result
