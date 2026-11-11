"""文件及其会话、客户和流程归属关系。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from sheetmate.db.session import Base
from sheetmate.db.models.common import new_id, now


class FileRecord(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("f"))
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    filename: Mapped[str] = mapped_column(String(255))
    stored_path: Mapped[str] = mapped_column(String(1024))
    size: Mapped[int] = mapped_column(Integer, default=0)
    # input 输入文件 / expected 人工做好的期望结果 / output 系统生成的结果 / doc 处理说明
    kind: Mapped[str] = mapped_column(String(16), default="input")
    profile: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # 文件画像（sheet、列、样本）
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class FileConversation(Base):
    """会话持有的文件引用。客户与多个会话共用同一份原件，互相独立地保留。"""

    __tablename__ = "file_conversations"

    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True, index=True
    )


class FileCustomer(Base):
    """会话改绑客户时保留双方的文件归属，原件与文件 ID 仍只有一份。"""

    __tablename__ = "file_customers"

    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True, index=True)


class FileRecipe(Base):
    """流程引用同一份原件；excluded 保留移除记录，避免历史运行重新带回文件。"""

    __tablename__ = "file_recipes"

    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"), primary_key=True)
    recipe_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)

