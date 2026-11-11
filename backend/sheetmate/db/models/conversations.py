"""会话与消息的持久化模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheetmate.db.session import Base
from sheetmate.db.models.common import new_id, now


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("cv"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # 绑定客户后按客户记忆对话；为空表示临时会话，数据只属于这个会话
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    recipe_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    mode: Mapped[str] = mapped_column(String(16), default="analyze")  # analyze / modify / create
    # 历史太长时压缩出的上下文摘要（TabClaw 的 auto-compact），以及它覆盖到的最后一条消息 id
    context_summary: Mapped[str] = mapped_column(Text, default="")
    summary_until: Mapped[int] = mapped_column(Integer, default=0)
    # Durable source references for the current task; independent of lossy chat summaries.
    task_state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # 对话里待用户确认的流程删除目标；确认只作用于本次列出的 ID。
    pending_recipe_deletion: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan", order_by="Message.id"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text, default="")
    # 执行过程事件（工具调用、结果表、Recipe 步骤、文件…），刷新页面后用来还原右侧执行明细
    events: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # 模式、附件等
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

