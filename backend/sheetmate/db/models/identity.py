"""用户、客户及负责关系的持久化模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sheetmate.db.session import Base
from sheetmate.db.models.common import new_id, now


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    display_name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin / member
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # 引导页回答：岗位、场景等
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("c"))
    name: Mapped[str] = mapped_column(String(128))
    code: Mapped[str] = mapped_column(String(64), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")  # 整体处理逻辑（Markdown）
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # ai：AI 自动生成（流程变化时自动更新）；human：用户手动编辑过（不再自动覆盖，点「重新总结」才更新）
    summary_by: Mapped[str] = mapped_column(String(16), default="ai")
    summary_policy_version: Mapped[int] = mapped_column(Integer, default=0)
    # 使用流程身份而非版本行 ID，更新流程版本时主流程保持不变。
    primary_recipe_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    mascot: Mapped[str] = mapped_column(String(32), default="")  # 卡片上的 IP 形象
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class UserCustomer(Base):
    """用户负责哪些客户（登录后只能看到自己负责的客户，管理员可见全部）。"""

    __tablename__ = "user_customers"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True)

