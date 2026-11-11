"""公式、流程版本、流程说明及运行记录。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sheetmate.db.session import Base
from sheetmate.db.models.common import new_id, now


class FormulaRecord(Base):
    """客户专属公式（scope=customer）。内置公式写在 engine/formulas/builtin.py，不入库。"""

    __tablename__ = "formulas"
    __table_args__ = (UniqueConstraint("formula_id", "version", "customer_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    formula_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    scope: Mapped[str] = mapped_column(String(16), default="customer")
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft / active / deprecated
    created_by: Mapped[str] = mapped_column(String(16), default="ai")  # ai / human
    body: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # 完整公式 JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RecipeRecord(Base):
    """Recipe 的一个版本。同一个 recipe_id 的多个版本并存，旧版本保留，历史任务可追溯。"""

    __tablename__ = "recipes"
    __table_args__ = (UniqueConstraint("recipe_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    recipe_id: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(200))
    scope: Mapped[str] = mapped_column(String(16), default="customer")
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # 来源会话
    # 仅兼容旧数据库的非空列；流程业务、接口与快照均不再使用状态。
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[str] = mapped_column(String(16), default="ai")
    body: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # 完整 Recipe JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class RecipeProfile(Base):
    """流程跨版本保留的业务说明，执行和文件通过 recipe_id 关联。"""

    __tablename__ = "recipe_profiles"

    recipe_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    summary_by: Mapped[str] = mapped_column(String(16), default="ai")
    summary_policy_version: Mapped[int] = mapped_column(Integer, default=0)
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Run(Base):
    """任务流水：每次分析、每次生成并执行 Recipe、每次重复执行 Recipe 都记一条。"""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("run"))
    kind: Mapped[str] = mapped_column(String(16))  # analysis / modify / replay / create / script
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recipe_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recipe_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/success/failed/escalated
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    tool_sequence: Mapped[str] = mapped_column(Text, default="")
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verification: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_file_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    output_file_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

