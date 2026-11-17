"""模型数值参数（输出上限、超时、重试、预算）来自环境变量，默认很大；设置文件和接口不再保存或展示它们。"""

from __future__ import annotations

import asyncio
import json
import os

import pytest

from sheetmate.core.config.settings import AUTO_MAX_TOKENS, LLMSettings, get_llm_provider_settings, save_llm_settings, settings


@pytest.fixture
def clean_settings_file():
    """隔离 settings.json，避免影响共用数据目录的其他测试。"""
    path = settings.settings_file
    backup = path.read_bytes() if path.exists() else None
    path.unlink(missing_ok=True)
    yield path
    if backup is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(backup)


@pytest.fixture(autouse=True)
def _reset_accepted_limits():
    from sheetmate.agent.llm import client as llm_client

    llm_client._ACCEPTED_MAX_TOKENS.clear()
    yield
    llm_client._ACCEPTED_MAX_TOKENS.clear()


def _cfg(**changes) -> LLMSettings:
    return LLMSettings(**{"api_key": "k", "base_url": "https://example.invalid/v1", "model": "m", **changes})


def _kwargs(cfg: LLMSettings) -> dict:
    from sheetmate.agent.llm.client import LLMClient

    return LLMClient(cfg)._kwargs([{"role": "user", "content": "hi"}], None)


def _clear_llm_env(monkeypatch) -> None:
    for name in [name for name in os.environ if name.startswith(("LLM_", "TOOL_TIMEOUT"))]:
        monkeypatch.delenv(name, raising=False)


# ---- 输出上限 ----

def test_default_requests_the_maximum():
    assert _kwargs(_cfg())["max_tokens"] == AUTO_MAX_TOKENS


# ---- 环境变量 ----

def test_env_controls_limits_with_large_defaults(clean_settings_file, monkeypatch):
    _clear_llm_env(monkeypatch)
    cfg = get_llm_provider_settings("custom")
    policy = cfg.runtime_policy
    assert not hasattr(cfg, "max_tokens") and not hasattr(cfg, "output_limit")      # 输出上限不再是配置项，自动处理
    assert not hasattr(policy, "context_window_tokens")                              # 模型窗口也不再预设
    assert cfg.timeout >= 7200
    assert policy.budget_enabled is False
    assert policy.structured_retries >= 20 and policy.tool_protocol_retries >= 40 and policy.sdk_retries >= 10
    assert policy.llm_hard_idle_seconds >= 86400 and policy.tool_timeout_seconds >= 14400
    assert policy.max_total_tokens >= 1_000_000_000 and policy.max_task_seconds >= 86400

    monkeypatch.setenv("LLM_MAX_TOKENS", "50000")          # 旧环境变量即使还留着，也不再有任何作用
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "99")
    monkeypatch.setenv("LLM_SDK_RETRIES", "2")
    monkeypatch.setenv("LLM_BUDGET_ENABLED", "1")
    cfg = get_llm_provider_settings("custom")
    assert not hasattr(cfg, "max_tokens") and cfg.timeout == 99
    assert cfg.runtime_policy.sdk_retries == 2 and cfg.runtime_policy.budget_enabled is True


def test_agent_loop_limits_are_large_by_default():
    from sheetmate.core.config import limits

    assert limits.AUX_CALL_TIMEOUT >= 1800
    assert limits.MAX_STEP_FIXES >= 10 and limits.MAX_PLAN_REPAIRS >= 10
    assert limits.MAX_COMPLETION_RETRIES >= 50 and limits.MAX_METHODS >= 30
    assert limits.TOOL_CALL_BUDGET >= 5000 and limits.FORMULA_MAX_ATTEMPTS >= 10


def test_saved_numeric_settings_are_ignored_and_cleaned(clean_settings_file, monkeypatch):
    _clear_llm_env(monkeypatch)
    # 旧版设置页写入的数值项不再生效，保存时顺带清掉。
    clean_settings_file.write_text(json.dumps({"llm": {"providers": {"custom": {
        "base_url": "https://example.invalid/v1", "model": "m", "api_key": "k", "max_tokens": 8192,
        "max_tokens_enabled": True, "timeout": 5,
        "runtime_policy": {"compatibility_mode": "relaxed", "sdk_retries": 0}}}}}), encoding="utf-8")
    cfg = get_llm_provider_settings("custom")
    assert cfg.timeout != 5 and cfg.runtime_policy.sdk_retries != 0
    save_llm_settings("custom", None, None, None, thinking_enabled=True)
    saved = json.loads(clean_settings_file.read_text(encoding="utf-8"))["llm"]["providers"]["custom"]
    assert not {"max_tokens", "max_tokens_enabled", "timeout", "runtime_policy"} & set(saved)
    assert saved["thinking_enabled"] is True and saved["api_key"] == "k"


def test_settings_api_hides_numeric_limits(client, clean_settings_file):
    config = client.get("/api/settings/llm").json()
    for provider in config["providers"]:
        assert {"timeout", "max_tokens", "max_tokens_enabled", "runtime_policy"}.isdisjoint(provider)
        assert "capabilities" in provider
    assert {"timeout", "max_tokens", "runtime_policy"}.isdisjoint(config)
    ok = client.put("/api/settings/llm", json={"provider_id": "custom", "api_key": "k",
                                                "base_url": "https://example.invalid/v1", "model": "m"})
    assert ok.status_code == 200, ok.text
    rejected = client.put("/api/settings/llm", json={"provider_id": "custom", "max_tokens": 1000})
    assert rejected.status_code == 422


# ---- 自动最大值：模型不接受时按报错降级 ----

def _bad_request(message: str):
    import httpx
    import openai

    request = httpx.Request("POST", "https://example.invalid/v1/chat/completions")
    return openai.BadRequestError(message, response=httpx.Response(400, request=request), body=None)


async def _chat_with_async(cfg: LLMSettings, rejections: list[str], **extra):
    """假服务端：依次拒绝 rejections 里的报错，再成功；返回 (每次请求的 max_tokens, 客户端)。"""
    from types import SimpleNamespace

    from sheetmate.agent.llm.client import LLMClient

    client = LLMClient(cfg)
    sent: list = []
    pending = list(rejections)

    async def create(**kwargs):
        sent.append(kwargs.get("max_tokens"))
        if pending:
            raise _bad_request(pending.pop(0))
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)),
                                    close=lambda: None)
    await client.chat([{"role": "user", "content": "hi"}], **extra)
    return sent, client


def _chat_with(cfg: LLMSettings, rejections: list[str], **extra):
    return asyncio.run(_chat_with_async(cfg, rejections, **extra))


def test_rejected_maximum_drops_to_the_models_real_limit_and_is_remembered():
    cfg = _cfg()
    sent, _ = _chat_with(cfg, ["Invalid max_tokens value, the valid range of max_tokens is [1, 8192]"])
    assert sent == [AUTO_MAX_TOKENS, 8192]
    # 同一模型下一次直接用记住的上限，不再试错。
    sent, _ = _chat_with(cfg, [])
    assert sent == [8192]
    # 换一个模型不受影响。
    sent, _ = _chat_with(_cfg(model="other"), [])
    assert sent == [AUTO_MAX_TOKENS]


def test_unparseable_rejection_stops_sending_max_tokens():
    sent, _ = _chat_with(_cfg(), ["max_tokens is not supported for this model"])
    assert sent == [AUTO_MAX_TOKENS, None]
    sent, _ = _chat_with(_cfg(), [])
    assert sent == [None]


def test_context_bound_rejection_is_not_cached_as_model_limit():
    message = ("'max_tokens' is too large: 131072. This model's maximum context length is 32768 tokens "
               "and your request has 200 input tokens (131072 > 32768 - 200)")
    sent, _ = _chat_with(_cfg(), [message])
    assert sent == [AUTO_MAX_TOKENS, 32768 - 200 - 655]  # 窗口 − 输入 − 2% 余量，不能再超出窗口
    from sheetmate.agent.llm import client as llm_client
    assert not llm_client._ACCEPTED_MAX_TOKENS  # 与输入长度有关，不能当成模型固定上限


def test_context_overflow_uses_window_minus_input_not_the_input_count():
    message = ("Requested token count exceeds the model's maximum context length of 163840 tokens. "
               "You requested a total of 165999 tokens: 34927 tokens from the input messages and "
               "131072 tokens for the completion.")
    sent, _ = _chat_with(_cfg(), [message])
    assert sent == [AUTO_MAX_TOKENS, 163840 - 34927 - 3276]


def test_unrelated_400_is_not_touched():
    from sheetmate.agent.llm import client as llm_client
    from sheetmate.agent.llm.client import LLMError

    with pytest.raises(LLMError):
        _chat_with(_cfg(), ["image input is not supported"])
    assert not llm_client._ACCEPTED_MAX_TOKENS


def test_auto_maximum_does_not_inflate_budget_reserve():
    from sheetmate.agent.llm.client import LLMClient

    auto = LLMClient(_cfg())
    assert auto._output_reserve(auto._kwargs([{"role": "user", "content": "hi"}], None)) == 8192


# ---- 超出模型窗口：不替使用者限制，报错讲清楚 ----

VLLM_OVERFLOW = ("Requested token count exceeds the model's maximum context length of 20000 tokens. "
                 "You requested a total of 34096 tokens: 30000 tokens from the input messages and "
                 "4096 tokens for the completion. Please reduce the length of the input messages or completion.")


def test_input_alone_over_the_window_is_reported_without_touching_the_request():
    from sheetmate.agent.llm import client as llm_client
    from sheetmate.agent.llm.client import LLMError, lower_max_tokens

    kwargs = {"max_tokens": AUTO_MAX_TOKENS}
    assert lower_max_tokens(kwargs, VLLM_OVERFLOW, ("u", "m")) is False and kwargs == {"max_tokens": AUTO_MAX_TOKENS}
    with pytest.raises(LLMError) as caught:
        _chat_with(_cfg(), [VLLM_OVERFLOW])
    assert caught.value.code == "context_overflow"
    assert not llm_client._ACCEPTED_MAX_TOKENS                      # 不能把这次的结论缓存成「该模型不接受输出上限」


def test_input_alone_over_the_window_sends_one_request_only():
    from types import SimpleNamespace

    from sheetmate.agent.llm.client import LLMClient, LLMError

    sent: list = []
    client = LLMClient(_cfg())

    async def create(**kwargs):
        sent.append(kwargs.get("max_tokens"))
        raise _bad_request(VLLM_OVERFLOW)

    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=lambda: None)
    with pytest.raises(LLMError):
        asyncio.run(client.chat([{"role": "user", "content": "hi"}]))
    assert sent == [AUTO_MAX_TOKENS]                                 # 没有「改小 / 不传 max_tokens」再试一遍之类的折腾


def test_context_overflow_error_text_is_clear_and_has_no_settings_link_word():
    from sheetmate.agent.llm.client import LLMError

    with pytest.raises(LLMError) as caught:
        _chat_with(_cfg(), [VLLM_OVERFLOW])
    message = str(caught.value)
    for expected in ("输入内容超出了模型的上下文窗口", "模型窗口 20,000 Token", "本次输入 30,000", "请求输出 4,096", "合计 34,096",
                     "仅输入就已超出窗口", "服务端原文", "maximum context length of 20000"):
        assert expected in message, expected
    assert "设置" not in message                                      # 前端看到「设置」会给「去设置」链接，这里不该有


def test_other_400s_keep_the_generic_message():
    from sheetmate.agent.llm.client import LLMError, context_overflow_message

    assert context_overflow_message("image input is not supported") is None
    assert context_overflow_message("valid range of max_tokens is [1, 8192]") is None
    with pytest.raises(LLMError) as caught:
        _chat_with(_cfg(), ["unsupported value: temperature must be 1"])
    assert caught.value.code == "api_error"


def test_length_truncation_message_is_accurate_and_has_no_settings_link_word():
    from types import SimpleNamespace

    from sheetmate.agent.llm.client import LLMClient, LLMError

    client = LLMClient(_cfg())

    def chunk(content=None, finish=None):
        delta = SimpleNamespace(content=content, reasoning_content=None, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(index=0, delta=delta, finish_reason=finish)], usage=None, model="m")

    async def stream():
        yield chunk("部分内容")
        yield chunk(None, "length")

    async def create(**kwargs):
        return stream()

    client.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=lambda: None)

    async def run():
        async for _ in client.stream_chat([{"role": "user", "content": "hi"}]):
            pass

    with pytest.raises(LLMError) as caught:
        asyncio.run(run())
    message = str(caught.value)
    assert caught.value.code == "output_limit" and "已写满" not in message and "设置" not in message
    assert f"输出上限为 {AUTO_MAX_TOKENS}" in message and "输入加输出用完了模型窗口" in message
