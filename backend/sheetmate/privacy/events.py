"""处理记录：写成一行大白话的日志文件，同时交给订阅者（服务层存进数据库，给前端面板看）。

铁律：这里只记「做了什么」（哪个文件、哪一列、什么类型、多少个值），绝不记真实值，也不记令牌和真实值的对应关系。
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Iterator

KINDS = ("启动", "识别", "发送检查", "还原", "拦截", "导出", "错误")


@dataclass
class Scope:
    """当前这次处理属于哪个会话；required=True 表示必须先完成脱敏才允许调用正常模型。"""

    conversation_id: str | None = None
    user_id: int | None = None
    required: bool = False
    ready: bool = False
    # 每次调用正常模型之前再确认一遍「用到的文件都脱敏完成了」；没完成就抛出 PrivacyBlocked（由服务层提供）
    verify: Any = None


_scope: contextvars.ContextVar[Scope | None] = contextvars.ContextVar("sheetmate_privacy_scope", default=None)


def current_scope() -> Scope | None:
    return _scope.get()


@contextlib.contextmanager
def bind(scope: Scope) -> Iterator[Scope]:
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


class PrivacyLog:
    def __init__(self) -> None:
        self._logger = logging.getLogger("sheetmate.privacy")
        self._logger.setLevel(logging.INFO)
        self._logger.propagate = False
        self._logger.addHandler(logging.NullHandler())
        self._sinks: list[Callable[[dict[str, Any]], None]] = []
        self._handler: logging.Handler | None = None
        self._path: Path | None = None
        self._lock = threading.Lock()

    @property
    def path(self) -> Path | None:
        return self._path

    def configure(self, log_dir: Path) -> Path:
        """日志写到 <数据目录>/log/privacy.log，按天滚动，保留 90 天。"""
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "privacy.log"
        with self._lock:
            if self._handler is not None:
                self._logger.removeHandler(self._handler)
                self._handler.close()
            handler = TimedRotatingFileHandler(path, when="midnight", backupCount=90, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(kind)s] %(message)s"))
            self._logger.addHandler(handler)
            self._handler, self._path = handler, path
        return path

    def add_sink(self, sink: Callable[[dict[str, Any]], None]) -> None:
        if sink not in self._sinks:
            self._sinks.append(sink)

    def emit(self, level: str, kind: str, text: str, **detail: Any) -> dict[str, Any]:
        scope = current_scope()
        conversation_id = scope.conversation_id if scope else None
        prefix = f"会话 {conversation_id}：" if conversation_id else ""
        shown = {key: value for key, value in detail.items() if key != "tokens"}     # 换掉的令牌清单很长，只进数据库给「发送明细」用
        suffix = f" | {json.dumps(shown, ensure_ascii=False, default=str)}" if shown else ""
        log_level = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}.get(level, logging.INFO)
        self._logger.log(log_level, f"{prefix}{text}{suffix}", extra={"kind": kind})
        event = {"time": datetime.now().isoformat(timespec="seconds"), "level": level, "kind": kind, "text": text,
                 "detail": detail or None, "conversation_id": conversation_id,
                 "user_id": scope.user_id if scope else None}
        for sink in list(self._sinks):
            try:
                sink(event)
            except Exception:  # 记录失败不能影响主流程，但要在日志文件里留下痕迹
                self._logger.exception("处理记录写入订阅者失败", extra={"kind": "错误"})
        return event
