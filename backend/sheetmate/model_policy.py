"""模型协议能力与可选任务预算；缺省值兼容已有配置文件。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

LLMTimeout = Annotated[float, Field(strict=True, ge=5, le=3600, allow_inf_nan=False)]
LLMMaxTokens = Annotated[int, Field(strict=True, ge=1, le=131072)]


class LLMCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    structured_output: Literal["auto", "json_schema", "json_object", "prompt"] = "auto"


class LLMRuntimePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    # 保留旧配置的迁移标记；运行时统一宽松兼容，工具参数仍须有效。
    compatibility_mode: Literal["relaxed"] = "relaxed"
    stream_recovery_retries: int = Field(default=5, ge=0, le=10)
    structured_retries: int = Field(default=6, ge=0, le=10)
    tool_protocol_retries: int = Field(default=12, ge=0, le=20)
    sdk_retries: int = Field(default=3, ge=0, le=5)
    # 0 保持原行为：不额外限制整次结构化调用的总时长，单次请求仍由 cfg.timeout 控制。
    structured_timeout: float = Field(default=0, ge=0, le=900, allow_inf_nan=False)
    # 软压缩目标独立于任务预算；只整理旧执行过程，不据此中止任务。
    context_target_chars: int = Field(default=80000, ge=0, le=2000000)
    # 填实际服务端上下文容量；0 表示未知，不从模型名称猜测。
    context_window_tokens: int = Field(default=0, ge=0, le=2000000)
    # 默认仅记录用量；用户主动开启后，才执行下面的任务预算。
    budget_enabled: bool = False
    # 单次用户任务共享；嵌套工具、核对、重试也计入。0 表示关闭相应限制。
    max_model_calls: int = Field(default=1000, ge=0, le=1000)
    max_prompt_tokens: int = Field(default=300000, ge=0, le=10000000)
    max_total_tokens: int = Field(default=500000, ge=0, le=10000000)
    max_task_seconds: float = Field(default=7200, ge=0, le=7200, allow_inf_nan=False)
    # 在兼容服务没有 tokenizer 时使用的每次请求保守输入边界（含工具 schema）。
    max_input_chars: int = Field(default=240000, ge=0, le=2000000)
    auxiliary_thinking_enabled: bool = False
    # Waiting owners have separate clocks; tool activity never resets model idle.
    llm_soft_idle_seconds: float = Field(default=90, ge=0, le=900, allow_inf_nan=False)
    llm_hard_idle_seconds: float = Field(default=3600, gt=0, le=7200, allow_inf_nan=False)
    stream_idle_seconds: float = Field(default=1800, gt=0, le=3600, allow_inf_nan=False)
    tool_timeout_seconds: float = Field(default=1800, gt=0, le=7200, allow_inf_nan=False)
    cancellation_grace_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)

    @field_validator("compatibility_mode", mode="before")
    @classmethod
    def normalize_legacy_mode(cls, value: object) -> object:
        return "relaxed" if value == "strict" else value
