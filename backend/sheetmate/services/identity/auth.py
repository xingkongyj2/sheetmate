"""认证：密码哈希（PBKDF2-SHA256 + 随机盐）、签名令牌（HMAC，存在 httpOnly Cookie 里）、权限判断。

令牌格式沿用 TabClaw 的思路（自带签名，不依赖第三方库）：base64(用户id.过期时间.随机数).签名
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from sheetmate.core.config.settings import get_secret_key, settings
from sheetmate.db.models import Customer, User, UserCustomer
from sheetmate.core.errors import Conflict, Forbidden, Unauthorized

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


ROLE_LABELS = {"admin": "管理员", "member": "成员"}


def user_dict(user: User) -> dict:
    profile = dict(user.profile or {})
    avatar = str(profile.pop("avatar", "") or "")  # 头像单独给，不混在引导页回答里
    return {
        "id": user.id,
        "account": user.account,
        # 早期管理员账号的默认名字就是「管理员」（和角色重复），当作没设置，显示用户名
        "display_name": user.display_name if user.display_name not in {"", "管理员"} else user.account,
        "avatar": avatar,
        "role": user.role,
        "role_label": ROLE_LABELS.get(user.role, user.role),
        "profile": profile,
        "onboarded": bool(profile.get("onboarded")),
    }


MAX_AVATAR_CHARS = 200_000  # 前端会裁成 256 像素的小图（通常 20KB 左右），这里只是兜底
_AVATAR = re.compile(r"^data:image/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$")


def set_avatar(user: User, avatar: str) -> None:
    """头像存在用户资料里；空字符串 = 清除。"""
    profile = dict(user.profile or {})
    if not avatar:
        profile.pop("avatar", None)
    else:
        if len(avatar) > MAX_AVATAR_CHARS or not _AVATAR.fullmatch(avatar):
            raise Conflict("头像图片不合适，请换一张 PNG / JPG / WebP 图片")
        profile["avatar"] = avatar
    user.profile = profile


def change_password(user: User, current: str, new: str) -> None:
    if not verify_password(current, user.password_hash):
        raise Conflict("当前密码不对")
    if len(new) < 6:
        raise Conflict("新密码至少 6 位")
    if new == current:
        raise Conflict("新密码和当前密码一样")
    user.password_hash = hash_password(new)


def ensure_admin(db: Session) -> User:
    """首次启动创建管理员账号（账号密码见 .env 的 ADMIN_ACCOUNT / ADMIN_PASSWORD）。"""
    user = db.scalar(select(User).where(User.account == settings.admin_account))
    if user is None:
        user = User(account=settings.admin_account, password_hash=hash_password(settings.admin_password),
                    display_name=settings.admin_account, role="admin")
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
