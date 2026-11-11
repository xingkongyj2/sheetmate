"""执行检查点、工具回执与工具输出的持久化模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sheetmate.db.session import Base
from sheetmate.db.models.common import new_id, now


class AgentExecution(Base):
    """A durable turn, indexed independently from the conversation transcript.

    ``version`` protects concurrent checkpoint writers. A checkpoint is a
    restart boundary, while tool receipts below record what actually happened.
    """

    __tablename__ = "agent_executions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("exec"))
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    request_message_id: Mapped[int] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), unique=True
    )
    assistant_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    request: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_fingerprint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    recovery_attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)
    expires_at: Mapped[datetime] = mapped_column(DateTime, index=True)


class AgentToolReceipt(Base):
    """Durable intent/result pair keyed by one stable tool call identity."""

    __tablename__ = "agent_tool_receipts"
    __table_args__ = (UniqueConstraint("execution_id", "call_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("agent_executions.id", ondelete="CASCADE"), index=True
    )
    call_id: Mapped[str] = mapped_column(String(200))
    tool_name: Mapped[str] = mapped_column(String(128))
    # Arguments are hashed rather than duplicated: the checkpoint owns the
    # replay input and the receipt owns the actual outcome.
    arguments_digest: Mapped[str] = mapped_column(String(64))
    input_digest: Mapped[str] = mapped_column(String(64))
    side_effect: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="started")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AgentToolOutput(Base):
    """Complete safe tool payloads; checkpoints carry only bounded references."""

    __tablename__ = "agent_tool_outputs"

    id: Mapped[str] = mapped_column(String(67), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=True
    )
    execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_executions.id", ondelete="CASCADE"), nullable=True, index=True
    )
    call_id: Mapped[str] = mapped_column(String(200))
    tool_name: Mapped[str] = mapped_column(String(128))
    branch_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    payload: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    characters: Mapped[int] = mapped_column(Integer)
    sanitized_fields: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)

