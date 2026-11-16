"""脱敏模块的持久化：每个文件的识别结果，以及面板里展示的处理记录。

这里只存「做了什么」（哪个文件、哪一列、什么类型、数量），不存任何真实值；令牌和真实值的对应关系在本机的令牌库里，不在数据库。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sheetmate.db.models.common import now
from sheetmate.db.session import Base


class PrivacyFile(Base):
    """一个文件的脱敏识别结果。文件内容或脱敏模型变了（指纹不同）就重新识别。"""

    __tablename__ = "privacy_files"

    file_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="ready")  # ready / failed
    mode: Mapped[str] = mapped_column(String(16), default="rules")  # model_rules / rules
    model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    sheets: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # 工作表 → 被保护的列
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 是在上传文件时识别的（upload），还是用到这个文件的那轮对话里才识别的（chat）
    source: Mapped[str] = mapped_column(String(16), default="chat", server_default="chat")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PrivacyEvent(Base):
    """处理记录（和 privacy.log 里的内容一致，只是存在数据库里给前端展示）。"""

    __tablename__ = "privacy_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    level: Mapped[str] = mapped_column(String(8), default="info")  # info / warn / error
    kind: Mapped[str] = mapped_column(String(16))  # 启动 / 识别 / 发送检查 / 还原 / 拦截 / 错误
    text: Mapped[str] = mapped_column(Text)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
