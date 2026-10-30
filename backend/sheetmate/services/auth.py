"""认证：密码哈希（PBKDF2-SHA256 + 随机盐）、签名令牌（HMAC，存在 httpOnly Cookie 里）、权限判断。

令牌格式沿用 TabClaw 的思路（自带签名，不依赖第三方库）：base64(用户id.过期时间.随机数).签名
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from sheetmate.config import get_secret_key, settings
from sheetmate.db.models import Customer, User, UserCustomer
from sheetmate.services.errors import Conflict, Forbidden, Unauthorized

COOKIE_NAME = "sm_session"
TOKEN_DAYS = 14
_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS).hex()
    return f"pbkdf2_sha256${_ITERATIONS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, digest = stored.split("$")
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
    return hmac.compare_digest(candidate, digest)


def create_token(user_id: int) -> str:
    payload = f"{user_id}.{int(time.time()) + TOKEN_DAYS * 86400}.{secrets.token_hex(8)}"
    body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    signature = hmac.new(get_secret_key(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{signature}"


def verify_token(token: str | None) -> int | None:
    """返回用户 id；无效或过期返回 None。"""
    if not token or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    expected = hmac.new(get_secret_key(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode()
        user_id, expires, _ = payload.split(".")
    except (ValueError, UnicodeDecodeError):
        return None
    if int(expires) < time.time():
        return None
    return int(user_id)


def authenticate(db: Session, account: str, password: str) -> User:
    user = db.scalar(select(User).where(User.account == account.strip()))
    if user is None or not verify_password(password, user.password_hash):
        raise Unauthorized("账号或密码不对")
    return user


def register(db: Session, account: str, password: str, display_name: str = "") -> User:
    account = account.strip()
    if len(account) < 3:
        raise Conflict("账号至少 3 个字符")
    if len(password) < 6:
        raise Conflict("密码至少 6 位")
    if db.scalar(select(User).where(User.account == account)):
        raise Conflict("这个账号已经被注册了")
    user = User(account=account, password_hash=hash_password(password), display_name=display_name or account, role="member")
    db.add(user)
    db.flush()
    return user


def user_dict(user: User) -> dict:
    return {
        "id": user.id,
        "account": user.account,
        "display_name": user.display_name or user.account,
        "role": user.role,
        "profile": user.profile or {},
        "onboarded": bool((user.profile or {}).get("onboarded")),
    }


def ensure_admin(db: Session) -> User:
    """首次启动创建管理员账号（账号密码见 .env 的 ADMIN_ACCOUNT / ADMIN_PASSWORD）。"""
    user = db.scalar(select(User).where(User.account == settings.admin_account))
    if user is None:
        user = User(account=settings.admin_account, password_hash=hash_password(settings.admin_password),
                    display_name="管理员", role="admin")
        db.add(user)
        db.flush()
    return user


# ---------------------------------------------------------------------------
# 权限：用户只能看到自己负责的客户；管理员可以看到全部
# ---------------------------------------------------------------------------


def can_access_customer(db: Session, user: User, customer_id: str) -> bool:
    if user.role == "admin":
        return True
    link = db.get(UserCustomer, (user.id, customer_id))
    if link is not None:
        return True
    customer = db.get(Customer, customer_id)
    return customer is not None and customer.created_by == user.id


def require_customer(db: Session, user: User, customer_id: str) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None or not can_access_customer(db, user, customer_id):
        raise Forbidden("你没有权限访问这个客户")
    return customer
