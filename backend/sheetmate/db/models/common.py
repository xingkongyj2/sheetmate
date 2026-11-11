"""数据库模型共用的标识和时间函数。"""

from __future__ import annotations

import uuid
from datetime import datetime


def new_id(prefix: str) -> str:
    """生成带前缀的短 ID，例如 c_3f2a9b1c0d（客户）、cv_...（会话）。"""
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def now() -> datetime:
    """本地时间（本地优先的单机应用，直接存本地时间，展示时不用换算）。"""
    return datetime.now().replace(microsecond=0)

