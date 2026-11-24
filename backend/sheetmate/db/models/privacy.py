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
    # 上传的原文件有没有已经被改成脱敏版：空 = 还没有（原文件是真实内容）；"copy:…" = 文件格式没法原地改，只给 AI 和查看用脱敏副本；
    # 其余 = 原文件已按当时的脱敏设置改成令牌版（值是那次的设置指纹，设置没变就不用再处理）
    applied: Mapped[str] = mapped_column(String(64), default="", server_default="")
    # 被换成令牌的列原来是数字还是文字：还原时数字列写回数字（手机号存成数字的表，还原后仍是数字）
    origin: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class PrivacyColumnChoice(Base):
    """用户在文件的脱敏维护页里手动做的选择：某一列一定脱敏（mask），或者不按整列脱敏（keep）。
    没有记录的列照自动识别的结果走。识别结果重新生成时这里的选择仍然有效。"""

    __tablename__ = "privacy_column_choices"

    file_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    sheet: Mapped[str] = mapped_column(String(255), primary_key=True)
    column: Mapped[str] = mapped_column(String(255), primary_key=True)
    choice: Mapped[str] = mapped_column(String(8))                    # mask / keep
    kind: Mapped[str | None] = mapped_column(String(16), nullable=True)  # mask 时的内容类型：phone / address
    values: Mapped[int] = mapped_column(Integer, default=0)           # mask 时这一列大约有多少个值（只用来在面板里显示数量）
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now)
