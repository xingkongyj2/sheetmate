"""长期记忆、字段映射和单调递增的记忆标识。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from sheetmate.db.session import Base
from sheetmate.db.models.common import now


class MemoryIdSequence(Base):
    """Durable high-water mark so a deleted memory ID can never name a new fact."""

    __tablename__ = "memory_id_sequence"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_id: Mapped[int] = mapped_column(Integer, default=0)


def advance_memory_id(connection, *, requested: int | None = None) -> int | None:
    """Atomically allocate an ID or preserve an explicitly inserted high ID."""
    if requested is None:
        return int(connection.execute(text(
            "INSERT INTO memory_id_sequence (name, last_id) VALUES ('memories', 1) "
            "ON CONFLICT(name) DO UPDATE SET last_id = memory_id_sequence.last_id + 1 "
            "RETURNING last_id"
        )).scalar_one())
    connection.execute(text(
        "INSERT INTO memory_id_sequence (name, last_id) VALUES ('memories', :requested) "
        "ON CONFLICT(name) DO UPDATE SET last_id = MAX(memory_id_sequence.last_id, excluded.last_id)"
    ), {"requested": requested})
    return None


def _memory_id_default(context) -> int:
    return int(advance_memory_id(context.connection))


class Memory(Base):
    """记忆条目（沿用 TabClaw 的四类记忆），作用域：客户 > 会话 > 用户。"""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, default=_memory_id_default)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True
    )
    # 流程身份不随版本变化；未绑定流程的旧记忆在主流程初始化时迁移。
    recipe_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # preferences 偏好 / domain_knowledge 业务规则 / user_context 背景 / history_insights 经验
    category: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="ai")  # ai / human
    # 来源消息用于幂等和后台抽取乱序保护；旧记录为空时仍按原有行为兼容。
    source_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    source_turn_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    evidence_span: Mapped[str] = mapped_column(Text, default="")
    # 自动修复经验的执行依据独立存放，原始错误、样例和代码不进入可复用规则。
    execution_evidence: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=dict, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

    # Cover direct human edits and scope migrations as well as set_memory.
    # A stale ORM UPDATE/DELETE must fail instead of replacing a newer rule.
    __mapper_args__ = {"version_id_col": version}


@event.listens_for(Memory, "before_insert")
def _assign_memory_id(_mapper, connection, target: Memory) -> None:
    # Covers every ORM insert, including direct admin/API edits and scope moves.
    # An explicit legacy/test ID also advances the watermark monotonically.
    if target.id is None:
        target.id = advance_memory_id(connection)
    else:
        advance_memory_id(connection, requested=target.id)


class FieldMapping(Base):
    """客户字段映射记忆：标准字段名 → 各文件里出现过的实际列名（SheetMate 字段标准化）。"""

    __tablename__ = "field_mappings"
    __table_args__ = (UniqueConstraint("customer_id", "standard"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    standard: Mapped[str] = mapped_column(String(128))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(16), default="human")  # ai 提议 / human 确认
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class RecipeFieldMapping(Base):
    """流程字段映射；独立表兼容旧表的客户级唯一约束。"""

    __tablename__ = "recipe_field_mappings"
    __table_args__ = (UniqueConstraint("customer_id", "recipe_id", "standard"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"))
    recipe_id: Mapped[str] = mapped_column(String(32), index=True)
    standard: Mapped[str] = mapped_column(String(128))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    source: Mapped[str] = mapped_column(String(16), default="human")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)

