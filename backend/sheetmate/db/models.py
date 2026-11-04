"""ORM 数据模型。

核心实体（对应 SheetMate 技术方案第 5 节，并按「客户 + 会话」的使用方式扩展）：

- users / customers / user_customers   用户、客户、负责关系（多对多，权限隔离）
- conversations / messages             会话与消息（会话可以绑定客户，也可以不绑定）
- files / file_customers / file_conversations 上传与输出原件、客户与会话的独立引用
- formulas                             客户专属公式（内置公式在代码里，只读）
- recipes                              Recipe（流程配方），按版本保存
- runs                                 任务流水（= TabClaw 的 WorkflowRecord + SheetMate 的 tasks）
- memories / field_mappings            记忆：偏好、业务知识、字段别名映射
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheetmate.db.database import Base


def new_id(prefix: str) -> str:
    """生成带前缀的短 ID，例如 c_3f2a9b1c0d（客户）、cv_...（会话）。"""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now() -> datetime:
    """本地时间（本地优先的单机应用，直接存本地时间，展示时不用换算）。"""
    return datetime.now().replace(microsecond=0)


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
    mascot: Mapped[str] = mapped_column(String(32), default="")  # 卡片上的 IP 形象
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


class UserCustomer(Base):
    """用户负责哪些客户（登录后只能看到自己负责的客户，管理员可见全部）。"""

    __tablename__ = "user_customers"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("customers.id", ondelete="CASCADE"), primary_key=True)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: new_id("cv"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    # 绑定客户后按客户记忆对话；为空表示临时会话，数据只属于这个会话
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(200), default="新对话")
    mode: Mapped[str] = mapped_column(String(16), default="analyze")  # analyze / modify / create
    # 历史太长时压缩出的上下文摘要（TabClaw 的 auto-compact），以及它覆盖到的最后一条消息 id
    context_summary: Mapped[str] = mapped_column(Text, default="")
    summary_until: Mapped[int] = mapped_column(Integer, default=0)
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
    status: Mapped[str] = mapped_column(String(16), default="draft")
    created_by: Mapped[str] = mapped_column(String(16), default="ai")
    body: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # 完整 Recipe JSON
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


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


class Memory(Base):
    """记忆条目（沿用 TabClaw 的四类记忆），作用域：客户 > 会话 > 用户。"""

    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    customer_id: Mapped[str | None] = mapped_column(
        ForeignKey("customers.id", ondelete="CASCADE"), nullable=True
    )
    conversation_id: Mapped[str | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True
    )
    # preferences 偏好 / domain_knowledge 业务规则 / user_context 背景 / history_insights 经验
    category: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(128))
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="ai")  # ai / human
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now)


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
