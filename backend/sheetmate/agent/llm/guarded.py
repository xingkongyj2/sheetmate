"""包在所有「正常模型」外面的脱敏拦截器。

get_llm() 是项目里调用 AI 的唯一入口，这里把它拿到的模型（真实的或测试用的假模型）包一层：
  发出去：消息、提示词先过 Guard，联系方式和地址换成令牌；检查不通过就不发（PrivacyBlockedError）
  回来的：回复文字和工具调用参数里的令牌，在本机换回真实值，后面的代码完全不用关心令牌
底线：宁可不处理，也不能让没脱敏的数据被正常模型读到。
"""

from __future__ import annotations

from collections import Counter
from types import SimpleNamespace
from typing import Any, AsyncIterator

from sheetmate.agent.llm.client import LLMError
from sheetmate.core.config.settings import settings
from sheetmate.core.config.privacy_switch import sync_privacy_settings
from sheetmate.privacy import KIND_LABELS, PrivacyBlocked, Stats, current_scope, privacy


class PrivacyBlockedError(LLMError):
    """脱敏没完成或检查没通过，本次没有向模型发送任何内容。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="privacy_blocked")


def _ensure_ready() -> None:
    if not privacy.configured:
        privacy.configure(settings.data_dir)


def _kinds_text(stats: Stats) -> str:
    return "、".join(f"{KIND_LABELS.get(kind, kind)} {count}" for kind, count in stats.values.items())


class GuardedLLM:
    """和被包装的模型接口完全一致（chat / stream_chat / complete_json），其余属性与赋值都转发给它。"""

    def __init__(self, inner: Any) -> None:
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name.startswith("_"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._inner, name, value)

    # ------------------------------------------------------------------ 发出去之前

    def _block(self, reason: str) -> PrivacyBlockedError:
        privacy.log.emit("error", "拦截", f"已拒绝向 AI 发送内容：{reason}", reason=reason)
        return PrivacyBlockedError(reason)

    def _guard(self):
        _ensure_ready()
        scope = current_scope()
        try:
            guard = privacy.require()
        except PrivacyBlocked as exc:
            raise self._block(str(exc)) from exc
        # 没有经过「先脱敏」这一步的调用，一律不放行：宁可不处理，也不能让没脱敏的数据被正常模型读到
        if scope is None:
            raise self._block("这次调用没有经过脱敏准备，为保护数据已停止处理，没有任何内容发给 AI。")
        if scope.required and not scope.ready:
            raise self._block("本轮用到的文件还没有完成脱敏，为保护数据已停止处理，没有任何内容发给 AI。")
        return guard

    @staticmethod
    def _sync_rules() -> None:
        """把设置页的规则开关应用到脱敏模块。总开关不影响这里：发给 AI 的内容什么时候都要检查、替换，总开关只管上传文件时的自动分析。"""
        _ensure_ready()
        sync_privacy_settings()

    async def _verify_files(self) -> None:
        """每次发给 AI 之前，确认这个会话里的表格文件都脱敏完成了；没完成就结束本次对话，不发任何内容。"""
        scope = current_scope()
        if scope is None or scope.verify is None:
            return
        try:
            await scope.verify()
        except PrivacyBlocked as exc:
            raise self._block(str(exc)) from exc

    def _report(self, stats: Stats, messages: int) -> None:
        if stats.total:
            text = (f"向 AI 发送 {messages} 条消息前已检查，替换 {stats.total} 个联系方式/地址（{_kinds_text(stats)}），"
                    "检查后没有发现明文")
        else:
            text = f"向 AI 发送 {messages} 条消息前已检查：没有发现联系方式或地址"
        level = "info"
        if stats.images:
            text += f"；含 {stats.images} 张图片，图片内容无法检查"
            level = "warn"
        privacy.log.emit(level, "发送检查", text, requests=1, messages=messages, values=stats.total,
                         kinds=dict(stats.values), methods=dict(stats.methods), images=stats.images)

    def _protect_messages(self, messages: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
        guard = self._guard()
        try:
            protected, stats = guard.protect_messages(messages)
        except PrivacyBlocked as exc:
            raise self._block(str(exc)) from exc
        except Exception as exc:  # 处理过程出任何错，都不能把原文发出去
            raise self._block(f"脱敏处理出错（{type(exc).__name__}），为保护数据已停止发送。") from exc
        self._report(stats, len(messages))
        return guard, protected

    def _protect_strings(self, *texts: str | None) -> tuple[Any, list[str | None]]:
        guard = self._guard()
        stats = Stats()
        try:
            out = [guard.protect_text(text, stats) if isinstance(text, str) else text for text in texts]
        except PrivacyBlocked as exc:
            raise self._block(str(exc)) from exc
        except Exception as exc:
            raise self._block(f"脱敏处理出错（{type(exc).__name__}），为保护数据已停止发送。") from exc
        self._report(stats, sum(1 for text in texts if isinstance(text, str)))
        return guard, out

    # ------------------------------------------------------------------ 回来之后

    @staticmethod
    def _report_restore(counter: Counter[str]) -> None:
        restored, unknown = counter.get("restored", 0), counter.get("unknown", 0)
        if restored or unknown:
            text = f"AI 的回复里有 {restored} 个令牌，已在本机还原为真实值"
            if unknown:
                text += f"；另有 {unknown} 个令牌不认识，保持原样"
            privacy.log.emit("warn" if unknown else "info", "还原", text, restored=restored, unknown=unknown)

    def _restore_message(self, guard: Any, message: Any, counter: Counter[str]) -> None:
        for attr in ("content", "reasoning_content"):
            value = getattr(message, attr, None)
            if isinstance(value, str) and value:
                setattr(message, attr, guard.restore_text(value, counter=counter))
        # 工具调用的参数不还原：工作区里的数据是令牌版，参数里的令牌正好和它对得上；要展示给用户时再还原

    # ------------------------------------------------------------------ 模型接口

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **extra: Any) -> Any:
        self._sync_rules()
        await self._verify_files()
        guard, protected = self._protect_messages(messages)
        message = await self._inner.chat(protected, tools, **extra)
        counter: Counter[str] = Counter()
        self._restore_message(guard, message, counter)
        self._report_restore(counter)
        return message

    async def stream_chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                          *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        self._sync_rules()
        await self._verify_files()
        guard, protected = self._protect_messages(messages)
        counter: Counter[str] = Counter()
        content, reasoning = guard.restorer(counter=counter), guard.restorer(counter=counter)
        calls: dict[int, Any] = {}
        try:
            async for chunk in self._inner.stream_chat(protected, tools, *args, **kwargs):
                choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
                if choice is not None:
                    delta = choice.delta
                    if getattr(delta, "content", None):
                        delta.content = content.feed(delta.content)
                    if getattr(delta, "reasoning_content", None):
                        delta.reasoning_content = reasoning.feed(delta.reasoning_content)
                    if getattr(choice, "finish_reason", None) is not None:
                        self._flush_into(delta, content, reasoning, calls)
                yield chunk
            tail = self._tail_chunk(content, reasoning, calls)
            if tail is not None:
                yield tail
        finally:
            self._report_restore(counter)

    @staticmethod
    def _flush_into(delta: Any, content: Any, reasoning: Any, calls: dict[int, Any]) -> None:
        """流结束时，把还留着的半截内容补进最后一个片段，不能丢。"""
        tail = content.flush()
        if tail:
            delta.content = (getattr(delta, "content", None) or "") + tail
        tail = reasoning.flush()
        if tail:
            delta.reasoning_content = (getattr(delta, "reasoning_content", None) or "") + tail
        pending = {index: restorer.flush() for index, restorer in calls.items()}
        pending = {index: text for index, text in pending.items() if text}
        if pending:
            existing = list(getattr(delta, "tool_calls", None) or [])
            for index, text in pending.items():
                match = next((c for c in existing if c.index == index), None)
                if match is not None:
                    match.function.arguments = (match.function.arguments or "") + text
                else:
                    existing.append(SimpleNamespace(index=index, id=None, type="function",
                                                    function=SimpleNamespace(name=None, arguments=text)))
            delta.tool_calls = existing

    @staticmethod
    def _tail_chunk(content: Any, reasoning: Any, calls: dict[int, Any]) -> Any | None:
        delta = SimpleNamespace(content=None, reasoning_content=None, tool_calls=None, role=None)
        GuardedLLM._flush_into(delta, content, reasoning, calls)
        if not (delta.content or delta.reasoning_content or delta.tool_calls):
            return None
        return SimpleNamespace(choices=[SimpleNamespace(index=0, finish_reason=None, delta=delta)], usage=None, model=None)

    async def complete_json(self, prompt: str, system: str | None = None, *args: Any, **kwargs: Any) -> Any:
        self._sync_rules()
        await self._verify_files()
        guard, (prompt_p, system_p) = self._protect_strings(prompt, system)
        value = await self._inner.complete_json(prompt_p, system_p, *args, **kwargs)
        counter: Counter[str] = Counter()
        value = guard.restore_object(value, counter)
        self._report_restore(counter)
        return value
