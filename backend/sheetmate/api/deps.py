"""接口层公共依赖：当前用户、SSE 响应。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import Cookie, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from sheetmate.db.session import get_db
from sheetmate.db.models import User
from sheetmate.services.identity.auth import COOKIE_NAME, verify_token
from sheetmate.core.errors import Forbidden, Unauthorized
from sheetmate.services.execution.jobs import Job


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


SSE_HEARTBEAT_SECONDS = 15.0


def _sse(event: dict[str, Any], *, event_id: int | None = None) -> str:
    prefix = f"id: {event_id}\n" if event_id is not None else ""
    return f"{prefix}data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


def parse_last_event_id(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        value = int(str(raw).strip())
        return value if value > 0 else None
    except ValueError:
        return None


def sse_response(
    job: Job,
    *,
    snapshot: bool = False,
    last_event_id: int | str | None = None,
    heartbeat_seconds: float | None = SSE_HEARTBEAT_SECONDS,
) -> StreamingResponse:
    """把后台任务的事件流转成 Server-Sent Events；带周期心跳与 Last-Event-ID 断点续传。"""
    after_id = last_event_id if isinstance(last_event_id, int) else parse_last_event_id(last_event_id)

    async def stream() -> AsyncIterator[str]:
        async for seq, event in job.events_with_ids(
            snapshot=snapshot, after_id=after_id, heartbeat_seconds=heartbeat_seconds,
        ):
            if event is None:
                yield ": ping\n\n"
            else:
                yield _sse(event, event_id=seq)
        yield "data: [DONE]\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
