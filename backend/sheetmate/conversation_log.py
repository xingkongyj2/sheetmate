"""Readable, durable traces: one file per conversation, one block per user turn.

ContextVars follow async tasks and ``asyncio.to_thread``. Each event is flushed
immediately; late background events are inserted into their original turn, so
memory extraction cannot split the next user's request. No customer payloads
are sampled or shortened. Only authentication fields are redacted.
"""

from __future__ import annotations

import base64
import contextvars
import json
import logging
import os
import re
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from sheetmate.config import settings

try:
    import fcntl
except ImportError:  # Windows uses the process lock (the server runs one worker).
    fcntl = None


log = logging.getLogger(__name__)
_LOCK = threading.RLock()
_CURRENT: contextvars.ContextVar[ConversationTrace | None] = contextvars.ContextVar("conversation_trace", default=None)
_PHASE: contextvars.ContextVar[str | None] = contextvars.ContextVar("conversation_trace_phase", default=None)
_SECRET_FIELDS = {"authorization", "proxy_authorization", "cookie", "set_cookie", "x_api_key", "api_key",
                  "apikey", "access_token", "refresh_token", "password", "secret", "client_secret"}
_SEPARATOR = b"\n\n\n\n"


def _secret_field(key: Any) -> bool:
    return str(key).lower().replace("-", "_") in _SECRET_FIELDS


def _normalise(value: Any, seen: set[int] | None = None) -> Any:
    """Preserve types/values for JSON, handling SDK models and scientific scalars."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        # Valid JSON even for pandas/numpy NaN and infinities.
        return value if value == value and abs(value) != float("inf") else {"type": "float", "value": str(value)}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Path, Enum)):
        return str(value) if isinstance(value, Path) else _normalise(value.value, seen)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "bytes", "encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    seen = set() if seen is None else seen
    if id(value) in seen:
        return {"type": type(value).__name__, "value": "<recursive reference>"}
    seen.add(id(value))
    try:
        if isinstance(value, Mapping):
            return {str(key): "[REDACTED]" if _secret_field(key) else _normalise(item, seen)
                    for key, item in value.items()}
        if isinstance(value, (list, tuple, set, frozenset)):
            return [_normalise(item, seen) for item in value]
        if hasattr(value, "model_dump"):
            return _normalise(value.model_dump(mode="python"), seen)
        if is_dataclass(value) and not isinstance(value, type):
            return _normalise({field.name: getattr(value, field.name) for field in fields(value)}, seen)
        if hasattr(value, "to_dict"):
            if type(value).__name__ == "DataFrame":
                return {"type": "DataFrame", "columns": _normalise(list(value.columns), seen),
                        "dtypes": [str(item) for item in value.dtypes],
                        "index": _normalise(list(value.index), seen),
                        "data": _normalise(value.to_numpy().tolist(), seen)}
            return _normalise(value.to_dict(), seen)
        if hasattr(value, "tolist"):
            return _normalise(value.tolist(), seen)
        if hasattr(value, "item"):
            return _normalise(value.item(), seen)
        if hasattr(value, "__dict__"):
            return _normalise(vars(value), seen)
        return {"type": f"{type(value).__module__}.{type(value).__qualname__}", "value": str(value)}
    finally:
        seen.remove(id(value))


def error_data(exc: BaseException) -> dict[str, Any]:
    return {"type": f"{type(exc).__module__}.{type(exc).__qualname__}", "message": str(exc),
            "code": getattr(exc, "code", None)}


def _file_name(conversation_id: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", conversation_id):
        return conversation_id + ".log"
    return "conversation-" + sha256(conversation_id.encode()).hexdigest() + ".log"


def conversation_log_path(conversation_id: str) -> Path:
    """Return the deterministic trace path for a conversation."""
    return settings.logs_dir / _file_name(conversation_id)


@dataclass
class ConversationTrace:
    conversation_id: str
    turn_id: str
    path: Path
    _fresh: bool = field(default=False, repr=False)

    @property
    def end_marker(self) -> bytes:
        return f"===== TURN END {self.turn_id} =====\n".encode("ascii")

    def record(self, event: str, function: str, data: Any = None, **metadata: Any) -> None:
        try:
            stamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
            payload = {**({"phase": _PHASE.get()} if _PHASE.get() else {}),
                       **metadata, "data": data}
            # Header strings are supplied by code, never by a user or model.
            entry = f"[{stamp}] {event} | {function}\n" + json.dumps(
                _normalise(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
            self._write(entry.encode("utf-8"))
        except Exception:
            log.exception("无法写入对话日志 conversation=%s turn=%s", self.conversation_id, self.turn_id)

    def _write(self, entry: bytes) -> None:
        """Insert before our end marker, moving late successors in bounded chunks."""
        with _LOCK:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            with os.fdopen(descriptor, "r+b", buffering=0) as stream:
                if fcntl is not None:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    stream.seek(0, os.SEEK_END)
                    size = stream.tell()
                    marker = self.end_marker
                    # Starting a new UUID turn must not scan a potentially huge
                    # existing conversation log. Recovery still finds its marker.
                    position = None if self._fresh else _find_marker(stream, size, marker)
                    if position is None:
                        stream.seek(size)
                        prefix = _SEPARATOR if size else b""
                        stream.write(prefix + f"===== TURN BEGIN {self.turn_id} =====\n".encode("ascii") + entry
                                     + marker)
                    else:
                        # Usually just the end marker; only late background work
                        # needs to move subsequent turns. Never read the whole log.
                        cursor = size
                        while cursor > position:
                            start = max(position, cursor - 1024 * 1024)
                            stream.seek(start)
                            chunk = stream.read(cursor - start)
                            stream.seek(start + len(entry))
                            stream.write(chunk)
                            cursor = start
                        stream.seek(position)
                        stream.write(entry)
                    stream.flush()
                    self._fresh = False
                finally:
                    if fcntl is not None:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _find_marker(stream: Any, size: int, marker: bytes) -> int | None:
    end = size
    suffix = b""
    while end:
        start = max(0, end - 64 * 1024)
        stream.seek(start)
        chunk = stream.read(end - start)
        found = (chunk + suffix).rfind(marker)
        if found >= 0:
            return start + found
        suffix = chunk[:len(marker) - 1]
        end = start
    return None


def new_turn(conversation_id: str, *, turn_id: str | None = None) -> ConversationTrace:
    return ConversationTrace(conversation_id, turn_id or uuid4().hex,
                             conversation_log_path(conversation_id), _fresh=turn_id is None)


def current_trace() -> ConversationTrace | None:
    return _CURRENT.get()


@contextmanager
def bind_trace(trace: ConversationTrace | None) -> Iterator[None]:
    token = _CURRENT.set(trace)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def record_event(event: str, function: str, data: Any = None, **metadata: Any) -> None:
    trace = _CURRENT.get()
    if trace is not None:
        trace.record(event, function, data, **metadata)


async def http_request_data(request: Any) -> dict[str, Any]:
    """Retain request values once, without HTTP transport metadata."""
    cached = request.scope.get("sheetmate.http_request")
    if cached is not None:
        return cached
    body = await request.body()
    try:
        return {"body": json.loads(body)}
    except (ValueError, UnicodeDecodeError):
        return {"raw_body": body.decode("utf-8", errors="backslashreplace")}


@contextmanager
def trace_phase(name: str) -> Iterator[None]:
    token = _PHASE.set(name)
    try:
        yield
    finally:
        _PHASE.reset(token)
