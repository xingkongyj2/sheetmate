"""execution tool capabilities."""

from __future__ import annotations

import asyncio
import contextvars
import sys
import threading
from concurrent.futures import Future
import time
from collections.abc import AsyncIterator
from copy import copy, deepcopy
from dataclasses import replace
from typing import Any
from uuid import uuid4
from sheetmate.agent.llm.structured import StructuredOutputError
from sheetmate.agent.framework.shared.watchdog import (
    phase,
    register_tool_worker,
    report_cleanup,
    report_wait,
    wait_tool,
)
from sheetmate.core.observability.conversation_log import record_event
from sheetmate.agent.tools.contracts import (
    AgentContext,
    Tool,
    ToolResult,
    tool_argument_error,
    validate_tool_args,
)


_LOG_DATA_EVENTS = {"file", "table", "script", "clarify", "memory_saved", "field_mapping_saved",
                    "recipe_saved", "recipe_completed", "recipe_deleted", "recipe_failed", "recipe_run_done"}


class _ToolCancelled(BaseException):
    """Bypass business error recovery when a revoked invocation unwinds."""


class ToolCleanupPending(RuntimeError):
    code = "tool_cleanup_pending"


class _CallCapability:
    """Revocation fences a late worker's next workspace/host operation.

    An already-entered host operation remains owned by its job until its
    real worker future completes; this proxy never claims it killed native IO.
    """

    def __init__(self, target: Any, cancelled: threading.Event):
        object.__setattr__(self, "_target", target)
        object.__setattr__(self, "_cancelled", cancelled)

    def __setattr__(self, name: str, value: Any) -> None:
        if self._cancelled.is_set():
            raise _ToolCancelled()
        setattr(self._target, name, value)

    def __delattr__(self, name: str) -> None:
        if self._cancelled.is_set():
            raise _ToolCancelled()
        delattr(self._target, name)

    def __getattr__(self, name: str) -> Any:
        if self._cancelled.is_set():
            raise _ToolCancelled()
        value = getattr(self._target, name)
        if not callable(value):
            return value

        def invoke(*args, **kwargs):
            if self._cancelled.is_set():
                raise _ToolCancelled()
            return value(*args, **kwargs)
        return invoke


async def _sync_tool(tool: Tool, args: dict[str, Any], ctx: AgentContext) -> ToolResult:
    """Bound the waiter while retaining ownership of every real live worker."""
    from sheetmate.agent.tools.worker import DataWorkerCancelled, run_data_tool
    from sheetmate.core.config.model_policy import LLMRuntimePolicy

    policy = getattr(getattr(ctx.llm, "cfg", None), "runtime_policy", None) or LLMRuntimePolicy()
    timeout = getattr(policy, "tool_timeout_seconds", 1800)
    grace = getattr(policy, "cancellation_grace_seconds", 5)
    cancelled = threading.Event()
    publication = threading.Lock()
    future: Future[ToolResult] = Future()
    ws = _CallCapability(ctx.ws, cancelled)
    host = copy(ctx.host) if ctx.host is not None else None
    host_baseline = dict(vars(host)) if host is not None and hasattr(host, "__dict__") else {}
    # Mutable bookkeeping also stays private until this invocation settles;
    # late native callbacks must not append to the live host after revocation.
    for name, value in host_baseline.items():
        if type(value) in {list, dict, set}:
            setattr(host, name, copy(value))
    if host is not None and hasattr(host, "ws"):
        host.ws = ws
    worker_ctx = replace(ctx, ws=ws, host=_CallCapability(host, cancelled) if host is not None else None)
    invocation_context = contextvars.copy_context()

    def publish_host() -> None:
        if host is None or not hasattr(host, "__dict__"):
            return
        with publication:
            if cancelled.is_set():
                return
            changes = {}
            missing = object()
            for name, value in vars(host).items():
                if name == "ws":
                    continue
                previous = host_baseline.get(name, missing)
                if value is previous:
                    continue
                if type(value) in {list, dict, set} and type(value) is type(previous):
                    try:
                        if value == previous:
                            continue
                    except (TypeError, ValueError):
                        pass
                changes[name] = value
            vars(ctx.host).update(changes)
            for name in host_baseline.keys() - vars(host).keys() - {"ws"}:
                vars(ctx.host).pop(name, None)

    def trace(frame, event, arg):
        if (cancelled.is_set() and event == "line"
                and frame.f_globals.get("__name__") not in {__name__, "sheetmate.agent.tools.worker"}):
            raise _ToolCancelled()
        return trace

    def run():
        future.set_running_or_notify_cancel()
        try:
            # The cooperative tracer handles Python work; native pure data
            # computations run in a separate, killable process instead.
            if not tool.process_safe:
                # Restrict cancellation checks to the tool and domain code.
                # Raising inside SQLAlchemy/SQLite/threading cleanup would
                # interrupt the very rollback/release paths we must preserve.
                implementation_module = getattr(tool.run, "__module__", "")

                def domain_trace(frame, event, arg):
                    module = frame.f_globals.get("__name__", "")
                    if (module.startswith("sheetmate.db.")
                            or module in {"sheetmate.engine.tables.workspace", "sheetmate.engine.workbooks.copies",
                                          "sheetmate.services.execution.workspace"}
                            or frame.f_code.co_name in {"close", "aclose", "__exit__", "__aexit__", "cleanup"}):
                        return None
                    if module == implementation_module or module.startswith("sheetmate."):
                        return trace(frame, event, arg)
                    return None

                sys.settrace(domain_trace)
            result = (run_data_tool(tool.name, args, worker_ctx, cancelled) if tool.process_safe
                      else tool.run(args, worker_ctx))
            if cancelled.is_set():
                raise _ToolCancelled()
        except (_ToolCancelled, DataWorkerCancelled):
            sys.settrace(None)
            future.set_exception(_ToolCancelled())
        except BaseException as exc:
            sys.settrace(None)
            publish_host()
            future.set_exception(exc)
        else:
            sys.settrace(None)
            publish_host()
            future.set_result(result)
        finally:
            sys.settrace(None)

    with phase("executing_tool", tool.name) as item:
        register_tool_worker(future)
        thread = threading.Thread(target=lambda: invocation_context.run(run), daemon=True,
                                  name=f"sheetmate-tool-{tool.name}")
        thread.start()
        wrapped = asyncio.wrap_future(future)

        async def revoke() -> bool:
            with publication:
                cancelled.set()
            ctx.ws.interrupt()
            done, _ = await asyncio.wait({wrapped}, timeout=grace)
            if done:
                if not wrapped.cancelled():
                    wrapped.exception()
                return True
            wrapped.add_done_callback(lambda value: value.exception() if not value.cancelled() else None)
            await report_cleanup(item)
            return False

        try:
            done, _ = await asyncio.wait({wrapped}, timeout=timeout)
            if done:
                return wrapped.result()
            await report_wait(item, elapsed=timeout, hard=True, code="tool_timeout")
            if not await revoke():
                # No retry may overlap an unfinished side-effect-capable call.
                raise ToolCleanupPending("工具已超时，正在清理资源；清理完成前不能开始下一次处理。")
            return ToolResult(f"工具「{tool.name}」超过 {timeout:g} 秒执行时限，已停止本次尝试。",
                              ok=False, summary="本次工具执行超时", error_kind="timeout")
        except asyncio.CancelledError:
            await revoke()
            raise


async def _stream_tool(tool: Tool, args: dict[str, Any], ctx: AgentContext) -> AsyncIterator[Any]:
    """Own streaming-tool silence separately from a nested model's idle clock."""
    from sheetmate.core.config.model_policy import LLMRuntimePolicy

    policy = getattr(getattr(ctx.llm, "cfg", None), "runtime_policy", None) or LLMRuntimePolicy()
    timeout = getattr(policy, "tool_timeout_seconds", 1800)
    grace = getattr(policy, "cancellation_grace_seconds", 5)
    stream = tool.stream(args, ctx)
    with phase("executing_tool", tool.name) as item:
        while True:
            task = asyncio.create_task(anext(stream))
            marker: Future[None] = Future()
            register_tool_worker(marker)
            task.add_done_callback(lambda done, marker=marker: marker.set_result(None))

            async def revoke() -> bool:
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=grace)
                if done:
                    if not task.cancelled():
                        task.exception()
                    return True
                task.add_done_callback(lambda value: value.exception() if not value.cancelled() else None)
                await report_cleanup(item)
                return False

            try:
                if not await wait_tool(task, item, timeout):
                    await report_wait(item, elapsed=timeout, hard=True, code="tool_timeout")
                    if not await revoke():
                        raise ToolCleanupPending("流式工具已超时，正在等待执行器完成清理。")
                    yield ToolResult(f"工具「{tool.name}」连续 {timeout:g} 秒未返回进展，已停止本次尝试。",
                                     ok=False, summary="本次工具执行超时", error_kind="timeout")
                    return
                try:
                    value = task.result()
                except StopAsyncIteration:
                    return
                yield value
            except asyncio.CancelledError:
                await revoke()
                raise


async def execute_tool(tool: Tool, args: dict[str, Any], ctx: AgentContext, *,
                       call_id: str | None = None) -> AsyncIterator[Any]:
    """统一执行：同步工具放线程里跑；流式工具逐个转发事件。最后一定产出一个 ToolResult。"""
    call_id = call_id or uuid4().hex
    started = time.perf_counter()
    implementation = tool.stream or tool.run
    function = (f"{getattr(implementation, '__module__', type(implementation).__module__)}."
                f"{getattr(implementation, '__qualname__', type(implementation).__qualname__)}") if implementation else ""
    metadata = {"call_id": call_id, "tool": tool.name, "implementation": function,
                "conversation_id": ctx.conversation_id}
    record_event("tool.input", function, args, **metadata)
    status = "interrupted"
    validated = False
    outcome: ToolResult | None = None
    failure: dict[str, str] | None = None
    data_events: list[dict[str, Any]] = []
    try:
        try:
            args = validate_tool_args(tool, args)
        except StructuredOutputError as exc:
            outcome = tool_argument_error(tool.name, "schema", exc.errors)
            status = "rejected"
            yield outcome
            return
        validated = True
        if tool.stream is not None:
            got_result = False
            async for item in _stream_tool(tool, args, ctx):
                if isinstance(item, ToolResult):
                    got_result = True
                    outcome = item
                    status = "ok" if item.ok else "failed"
                elif isinstance(item, dict) and item.get("type") in _LOG_DATA_EVENTS:
                    data_events.append(deepcopy(item))
                yield item
            if not got_result:
                outcome = ToolResult("工具没有返回结果", ok=False)
                status = "failed"
                yield outcome
        else:
            assert tool.run is not None
            outcome = await _sync_tool(tool, args, ctx)
            status = "ok" if outcome.ok else "failed"
            yield outcome
    except (asyncio.CancelledError, GeneratorExit) as exc:
        if outcome is None:
            status = "cancelled" if isinstance(exc, asyncio.CancelledError) else "interrupted"
            failure = {"reason": type(exc).__name__}
        raise
    except ToolCleanupPending as exc:
        status = "cleanup_pending"
        failure = {"error_type": type(exc).__name__, "error": str(exc), "code": exc.code}
        raise
    except Exception as exc:  # 工具出错时把错误当结果交给 LLM，让它自己纠正
        message = str(exc) or type(exc).__name__
        status = "error"
        failure = {"error_type": type(exc).__name__, "error": message}
        if not validated:
            # A broken local schema is a configuration fault, not a business
            # tool failure that the model can repair by changing its arguments.
            raise
        outcome = ToolResult(f"工具 `{tool.name}` 出错：{message}", ok=False, summary=message[:200])
        yield outcome
    finally:
        event = "tool.result" if outcome is not None else "tool.cancelled" if status in {"cancelled", "interrupted"} else "tool.error"
        extra_events = [item for item in data_events if outcome is None or item not in outcome.events]
        record_event(event, function if validated else "sheetmate.agent.tools.contracts.validate_tool_args", outcome or failure,
                     status=status, duration_ms=int((time.perf_counter() - started) * 1000),
                     **({"events": extra_events} if extra_events else {}), **metadata)
