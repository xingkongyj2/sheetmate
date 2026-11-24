"""Readable, durable traces: one file per conversation, one block per user turn.

ContextVars follow async tasks and ``asyncio.to_thread``. Each event is flushed
immediately; late background events are inserted into their original turn, so
memory extraction cannot split the next user's request. Diagnostic projections
redact recognizable contacts, credentials and inline image bytes; source records
remain unchanged.
"""

from __future__ import annotations

import atexit
import contextvars
import json
import logging
import os
import queue
import re
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

from sheetmate.core.config.settings import settings
from sheetmate.core.security.privacy import redact_derived_text

try:
    import fcntl
except ImportError:  # Windows uses the process lock (the server runs one worker).
    fcntl = None


log = logging.getLogger(__name__)
_LOCK = threading.RLock()
_WRITE_QUEUE: queue.Queue[tuple[ConversationTrace, bytes, int] | None] = queue.Queue()
_DELETE_GEN: dict[str, int] = {}
_WRITER_THREAD: threading.Thread | None = None
_WRITER_START_LOCK = threading.Lock()
_CURRENT: contextvars.ContextVar[ConversationTrace | None] = contextvars.ContextVar("conversation_trace", default=None)
_PHASE: contextvars.ContextVar[str | None] = contextvars.ContextVar("conversation_trace_phase", default=None)
_SECRET_FIELDS = {"authorization", "proxy_authorization", "cookie", "set_cookie", "x_api_key", "api_key",
                  "apikey", "access_token", "refresh_token", "password", "secret", "client_secret"}
_SEPARATOR = b"\n\n\n\n"


def _secret_field(key: Any) -> bool:
    return str(key).lower().replace("-", "_") in _SECRET_FIELDS


def _normalise(value: Any, seen: set[int] | None = None) -> Any:
    """Preserve types/values for JSON, handling SDK models and scientific scalars."""
    if isinstance(value, str):
        return redact_derived_text(value)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        # Valid JSON even for pandas/numpy NaN and infinities.
        return value if value == value and abs(value) != float("inf") else {"type": "float", "value": str(value)}
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Path, Enum)):
        return redact_derived_text(str(value)) if isinstance(value, Path) else _normalise(value.value, seen)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"type": "bytes", "length": len(value), "sha256": sha256(value).hexdigest(),
                "value": "[BINARY_DATA_REDACTED]"}
    seen = set() if seen is None else seen
    if id(value) in seen:
        return {"type": type(value).__name__, "value": "<recursive reference>"}
    seen.add(id(value))
    try:
        if isinstance(value, Mapping):
            return {redact_derived_text(str(key)): "[REDACTED]" if _secret_field(key) else _normalise(item, seen)
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
        return {"type": f"{type(value).__module__}.{type(value).__qualname__}", "value": redact_derived_text(str(value))}
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
    _require_conversation: bool = field(default=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)
    _closed: bool = field(default=False, init=False, repr=False)
    _marker_pos: int | None = field(default=None, init=False, repr=False)
    _last_llm_msg_hashes: list[str] = field(default_factory=list, init=False, repr=False)
    _last_llm_tools_hash: str | None = field(default=None, init=False, repr=False)
    _compact_lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def end_marker(self) -> bytes:
        return f"===== TURN END {self.turn_id} =====\n".encode("ascii")

    def _compact_llm_request(self, data: Any) -> Any:
        """同轮连续 LLM 调用只增量记录新增消息与变化的 tools 定义，避免 O(N^2) 百兆级日志膨胀。"""
        if not isinstance(data, Mapping):
            return data
        out = dict(data)
        with self._compact_lock:
            tools = out.get("tools")
            if isinstance(tools, list):
                tools_hash = sha256(
                    json.dumps(tools, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
                ).hexdigest()
                if tools_hash == self._last_llm_tools_hash:
                    out["tools"] = {"_unchanged": True, "count": len(tools), "sha256": tools_hash[:16]}
                else:
                    self._last_llm_tools_hash = tools_hash
            messages = out.get("messages")
            if isinstance(messages, list):
                current_hashes = [
                    sha256(json.dumps(item, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
                    for item in messages
                ]
                prefix = 0
                for prev, curr in zip(self._last_llm_msg_hashes, current_hashes):
                    if prev != curr:
                        break
                    prefix += 1
                self._last_llm_msg_hashes = current_hashes
                if prefix > 0:
                    out["messages_prefix_omitted"] = prefix
                    out["messages_total"] = len(messages)
                    out["messages"] = messages[prefix:]
        return out

    def record(self, event: str, function: str, data: Any = None, **metadata: Any) -> None:
        try:
            stamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
            logged_data = self._compact_llm_request(data) if event == "llm.request" else data
            payload = {**({"phase": _PHASE.get()} if _PHASE.get() else {}),
                       **metadata, "data": logged_data}
            # Header strings are supplied by code, never by a user or model.
            entry = f"[{stamp}] {event} | {function}\n" + json.dumps(
                _normalise(payload), ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
            _enqueue_write(self, entry.encode("utf-8"))
        except Exception:
            log.exception("无法写入对话日志 conversation=%s turn=%s", self.conversation_id, self.turn_id)

    def _write(self, entry: bytes) -> None:
        """Insert before our end marker, moving late successors in bounded chunks."""
        with _LOCK:
            if self._closed:
                return
            if not self._started:
                # A newly allocated or recovered trace may reach its first
                # write after the owning conversation was deleted. Checking
                # under the same lock as after-commit deletion closes that
                # check/create race without retaining permanent tombstones.
                if self._require_conversation and not _conversation_exists(self.conversation_id):
                    self._closed = True
                    return
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            flags = os.O_RDWR | (os.O_CREAT if not self._started else 0)
            try:
                descriptor = os.open(self.path, flags, 0o600)
            except FileNotFoundError:
                # Late responses keep their trace object after deletion. They
                # may append to an existing log, but cannot recreate a log.
                self._closed = True
                return
            self._started = True
            with os.fdopen(descriptor, "r+b", buffering=0) as stream:
                if fcntl is not None:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
                try:
                    stream.seek(0, os.SEEK_END)
                    size = stream.tell()
                    marker = self.end_marker
                    # Starting a new UUID turn must not scan a potentially huge
                    # existing conversation log. Recovery still finds its marker.
                    position: int | None = None
                    if not self._fresh:
                        if self._marker_pos is not None and self._marker_pos + len(marker) <= size:
                            stream.seek(self._marker_pos)
                            if stream.read(len(marker)) == marker:
                                position = self._marker_pos
                        if position is None:
                            position = _find_marker(stream, size, marker)
                    if position is None:
                        stream.seek(size)
                        prefix = _SEPARATOR if size else b""
                        begin = f"===== TURN BEGIN {self.turn_id} =====\n".encode("ascii")
                        stream.write(prefix + begin + entry + marker)
                        self._marker_pos = size + len(prefix) + len(begin) + len(entry)
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
                        self._marker_pos = position + len(entry)
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
                             conversation_log_path(conversation_id), _fresh=turn_id is None,
                             _require_conversation=True)


_conversation_probe: Callable[[str], bool] | None = None


def configure_conversation_probe(probe: Callable[[str], bool]) -> None:
    """由应用组合入口注入存储检查，避免日志模块依赖数据库。"""
    global _conversation_probe
    _conversation_probe = probe


def _conversation_exists(conversation_id: str) -> bool:
    return _conversation_probe is not None and _conversation_probe(conversation_id)


def _writer_loop() -> None:
    while True:
        item = _WRITE_QUEUE.get()
        try:
            if item is None:
                return
            trace, entry, generation = item
            with _LOCK:
                if _DELETE_GEN.get(trace.conversation_id, 0) != generation:
                    trace._closed = True
                    continue
                trace._write(entry)
        except Exception:
            log.exception("后台写入对话日志失败")
        finally:
            _WRITE_QUEUE.task_done()


def _ensure_writer() -> None:
    global _WRITER_THREAD
    if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
        return
    with _WRITER_START_LOCK:
        if _WRITER_THREAD is not None and _WRITER_THREAD.is_alive():
            return
        thread = threading.Thread(target=_writer_loop, daemon=True, name="sheetmate-conversation-log-writer")
        thread.start()
        _WRITER_THREAD = thread


def _enqueue_write(trace: ConversationTrace, entry: bytes) -> None:
    with _LOCK:
        if trace._closed:
            return
        generation = _DELETE_GEN.get(trace.conversation_id, 0)
    _ensure_writer()
    _WRITE_QUEUE.put((trace, entry, generation))


def flush_conversation_logs(timeout: float = 5.0) -> bool:
    """等待后台日志队列全部落盘；供关闭进程或测试同步读取日志前使用。"""
    if _WRITER_THREAD is None or not _WRITER_THREAD.is_alive():
        return True
    done = threading.Event()

    def _wait() -> None:
        _WRITE_QUEUE.join()
        done.set()

    waiter = threading.Thread(target=_wait, daemon=True)
    waiter.start()
    return done.wait(timeout)


atexit.register(flush_conversation_logs)


def delete_conversation_log(conversation_id: str) -> None:
    """Commit-time cleanup, serialized with a trace's first check/create write."""
    with _LOCK:
        _DELETE_GEN[conversation_id] = _DELETE_GEN.get(conversation_id, 0) + 1
        conversation_log_path(conversation_id).unlink(missing_ok=True)


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
