"""包在所有「正常模型」外面的脱敏拦截器。

get_llm() 是项目里调用 AI 的唯一入口，这里把它拿到的模型（真实的或测试用的假模型）包一层：
  - 受后台管理「脱敏设置」总开关控制：总开关关闭时完全跳过所有脱敏检查、替换、拦截与提示注入；
  - 总开关开启时：
    发出去：消息、提示词先过 Guard，联系方式和地址换成令牌；检查不通过就不发（PrivacyBlockedError）
    回来的：回复里的令牌原样保留，不在本机还原——数据从上传起就是令牌，只有下载文件时才换回真实值
底线：开启脱敏时宁可不处理，也不能让没脱敏的数据被正常模型读到。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, AsyncIterator

from sheetmate.agent.llm.client import LLMError
from sheetmate.core.config.settings import settings
from sheetmate.core.config.privacy_switch import sync_privacy_settings
from sheetmate.privacy import KIND_LABELS, PrivacyBlocked, Stats, current_scope, privacy, token_legend


class PrivacyBlockedError(LLMError):
    """脱敏没完成或检查没通过，本次没有向模型发送任何内容。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="privacy_blocked")


def _with_legend(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """在系统提示末尾附上加密格式说明（放在末尾，前面不变的系统提示仍能命中模型的提示缓存）；没有系统提示就加一条。"""
    legend = token_legend()
    if messages and messages[0].get("role") == "system" and isinstance(messages[0].get("content"), str):
        return [{**messages[0], "content": f"{messages[0]['content']}\n\n{legend}"}, *messages[1:]]
    return [{"role": "system", "content": legend}, *messages]


def _ensure_ready() -> None:
    if not privacy.configured:
        privacy.configure(settings.data_dir)


SEND_TOKENS_MAX = 500      # 一次发送最多记多少个换掉的值


def _source(roles: set[str] | None) -> str:
    """这个值是从哪来的：
    leak：出现在系统说明、工具结果里（这些内容来自文件和工作区，本该已经是令牌）——文件脱敏漏掉的；
    user：用户自己在对话里打的；history：之前的 AI 回复（给用户看时还原成了原文，再次发送时换回令牌，属于正常往返）。"""
    if not roles or roles - {"user", "assistant"}:
        return "leak"
    return "user" if "user" in roles else "history"


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

    def _block(self, reason: str, leaks: list[tuple[str, str]] | None = None) -> PrivacyBlockedError:
        # tokens：发送前检查没能处理掉的值。和发送明细一样只记令牌，原文在加密的令牌库里，打开明细时才在本机还原
        tokens = []
        if leaks:
            try:
                vault = privacy.require().vault
                tokens = [[vault.token(kind, value), kind] for kind, value in leaks]
            except Exception:
                tokens = []
        privacy.log.emit("error", "拦截", f"已拦截：{reason}", reason=reason, **({"tokens": tokens} if tokens else {}))
        return PrivacyBlockedError(reason)

    def _guard(self):
        _ensure_ready()
        scope = current_scope()
        try:
            guard = privacy.require()
        except PrivacyBlocked as exc:
            raise self._block(str(exc), getattr(exc, "leaks", None)) from exc
        # 没有经过「先脱敏」这一步的调用，一律不放行：宁可不处理，也不能让没脱敏的数据被正常模型读到
        if scope is None:
            raise self._block("这次调用没有经过脱敏准备，为保护数据已停止处理，没有任何内容发给模型。")
        if scope.required and not scope.ready:
            raise self._block("本轮用到的文件还没有完成脱敏，为保护数据已停止处理，没有任何内容发给模型。")
        return guard

    @staticmethod
    def _sync_rules() -> bool:
        """同步设置页的总开关和规则开关；总开关关闭时直接放行，不再执行任何模型脱敏或拦截逻辑。"""
        enabled = sync_privacy_settings()
        if enabled:
            _ensure_ready()
        return enabled

    async def _verify_files(self) -> None:
        """每次发给 AI 之前，确认这个会话里的表格文件都脱敏完成了；没完成就结束本次对话，不发任何内容。"""
        scope = current_scope()
        if scope is None or scope.verify is None:
            return
        try:
            await scope.verify()
        except PrivacyBlocked as exc:
            raise self._block(str(exc), getattr(exc, "leaks", None)) from exc

    def _report(self, stats: Stats, messages: int) -> None:
        if stats.total:
            text = f"发送前检查：替换 {stats.total} 个（{_kinds_text(stats)}）"
        else:
            text = "发送前检查：无敏感内容"
        level = "info"
        if stats.images:
            text += f"，{stats.images} 张图片无法检查"
            level = "warn"
        # tokens：这次发送里换掉的值（只有令牌和类型；真实值在加密的令牌库里，打开「发送明细」时才在本机还原出来）
        privacy.log.emit(level, "发送检查", text, requests=1, messages=messages, values=stats.total,
                         kinds=dict(stats.values), methods=dict(stats.methods), images=stats.images,
                         tokens=[[token, kind, method, _source(stats.roles.get(token))]
                                 for token, (kind, method) in list(stats.tokens.items())[:SEND_TOKENS_MAX]])

    def _protect_messages(self, messages: list[dict[str, Any]]) -> tuple[Any, list[dict[str, Any]]]:
        guard = self._guard()
        try:
            protected, stats = guard.protect_messages(messages)
        except PrivacyBlocked as exc:
            raise self._block(str(exc), getattr(exc, "leaks", None)) from exc
        except Exception as exc:  # 处理过程出任何错，都不能把原文发出去
            raise self._block(f"脱敏处理出错（{type(exc).__name__}），为保护数据已停止发送。") from exc
        self._report(stats, len(messages))
        return guard, _with_legend(protected)

    def _protect_strings(self, *texts: str | None) -> tuple[Any, list[str | None]]:
        guard = self._guard()
        stats = Stats()
        try:
            out = [guard.protect_text(text, stats) if isinstance(text, str) else text for text in texts]
        except PrivacyBlocked as exc:
            raise self._block(str(exc), getattr(exc, "leaks", None)) from exc
        except Exception as exc:
            raise self._block(f"脱敏处理出错（{type(exc).__name__}），为保护数据已停止发送。") from exc
        self._report(stats, sum(1 for text in texts if isinstance(text, str)))
        return guard, out

    # ------------------------------------------------------------------ 模型接口

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None, **extra: Any) -> Any:
        if not self._sync_rules():
            return await self._inner.chat(messages, tools, **extra)
        await self._verify_files()
        guard, protected = self._protect_messages(messages)
        return await self._inner.chat(protected, tools, **extra)       # 回来的内容原样给，令牌不还原

    async def stream_chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                          *args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        if not self._sync_rules():
            async for chunk in self._inner.stream_chat(messages, tools, *args, **kwargs):
                yield chunk
            return
        await self._verify_files()
        _, protected = self._protect_messages(messages)
        async for chunk in self._inner.stream_chat(protected, tools, *args, **kwargs):
            yield chunk                                                # 回来的内容原样给，令牌不还原

    async def complete_json(self, prompt: str, system: str | None = None, *args: Any, **kwargs: Any) -> Any:
        if not self._sync_rules():
            return await self._inner.complete_json(prompt, system, *args, **kwargs)
        await self._verify_files()
        _, (prompt_p, system_p) = self._protect_strings(prompt, system)
        system_p = f"{system_p}\n\n{token_legend()}" if system_p else token_legend()
        return await self._inner.complete_json(prompt_p, system_p, *args, **kwargs)     # 令牌不还原
