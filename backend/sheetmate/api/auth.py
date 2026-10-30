"""登录 / 注册 / 退出 / 当前用户 / 引导信息。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from sheetmate.api.deps import current_user
from sheetmate.db.database import get_db
from sheetmate.db.models import User
from sheetmate.services import auth as auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    account: str
    password: str


class RegisterBody(LoginBody):
    display_name: str = ""


class ProfileBody(BaseModel):
    display_name: str | None = None
    profile: dict[str, Any] | None = None


def _set_cookie(response: Response, user: User) -> None:
    response.set_cookie(auth_service.COOKIE_NAME, auth_service.create_token(user.id), httponly=True, samesite="lax",
                        max_age=auth_service.TOKEN_DAYS * 86400, path="/")


@router.post("/login")
def login(body: LoginBody, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = auth_service.authenticate(db, body.account, body.password)
    _set_cookie(response, user)
    return {"ok": True, "user": auth_service.user_dict(user)}


@router.post("/register")
def register(body: RegisterBody, response: Response, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = auth_service.register(db, body.account, body.password, body.display_name)
    _set_cookie(response, user)
    return {"ok": True, "user": auth_service.user_dict(user)}


@router.post("/logout")
def logout(response: Response) -> dict[str, Any]:
    response.delete_cookie(auth_service.COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(current_user)) -> dict[str, Any]:
    return {"ok": True, "user": auth_service.user_dict(user)}


@router.put("/profile")
def update_profile(body: ProfileBody, user: User = Depends(current_user)) -> dict[str, Any]:
    """引导页（你的岗位 / 主要场景）和昵称。"""
    if body.display_name is not None and body.display_name.strip():
        user.display_name = body.display_name.strip()[:40]
    if body.profile is not None:
        user.profile = {**(user.profile or {}), **body.profile}
    return {"ok": True, "user": auth_service.user_dict(user)}
