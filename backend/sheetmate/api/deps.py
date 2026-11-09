"""接口层公共依赖：当前用户、SSE 响应。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Cookie, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from sheetmate.db.database import get_db
from sheetmate.db.models import User
from sheetmate.services.auth import COOKIE_NAME, verify_token
from sheetmate.services.errors import Forbidden, Unauthorized
from sheetmate.services.jobs import Job


def current_user(db: Session = Depends(get_db), sm_session: str | None = Cookie(default=None, alias=COOKIE_NAME)) -> User:
    user_id = verify_token(sm_session)
    if user_id is None:
        raise Unauthorized("请先登录")
    user = db.get(User, user_id)
    if user is None:
        raise Unauthorized("账号不存在，请重新登录")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin":
        raise Forbidden("只有管理员可以操作")
    return user


def _sse(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def sse_response(job: Job, *, snapshot: bool = False) -> StreamingResponse:
    """把后台任务的事件流转成 Server-Sent Events。客户端断开不影响后台任务继续执行。"""

    async def stream() -> AsyncIterator[str]:
        async for event in job.events(snapshot=snapshot):
            yield _sse(event)
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
