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

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event, text
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
