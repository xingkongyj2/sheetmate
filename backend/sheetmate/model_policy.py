"""模型协议能力与有界运行策略；缺省值兼容已有配置文件。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

LLMTimeout = Annotated[float, Field(strict=True, ge=5, le=900, allow_inf_nan=False)]
LLMMaxTokens = Annotated[int, Field(strict=True, ge=1, le=131072)]


class LLMCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    structured_output: Literal["auto", "json_schema", "json_object", "prompt"] = "auto"


class LLMRuntimePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    structured_retries: int = Field(default=1, ge=0, le=3)
    tool_protocol_retries: int = Field(default=2, ge=0, le=5)
    sdk_retries: int = Field(default=1, ge=0, le=3)
    # 0 保持原行为：不额外限制整次结构化调用的总时长，单次请求仍由 cfg.timeout 控制。
    structured_timeout: float = Field(default=0, ge=0, le=900, allow_inf_nan=False)
    # 单次用户任务共享；嵌套工具、核对、重试也计入。0 表示关闭相应限制。
    max_model_calls: int = Field(default=120, ge=0, le=1000)
    max_prompt_tokens: int = Field(default=300000, ge=0, le=10000000)
    max_total_tokens: int = Field(default=500000, ge=0, le=10000000)
    max_task_seconds: float = Field(default=600, ge=0, le=7200, allow_inf_nan=False)
    # 在兼容服务没有 tokenizer 时使用的每次请求保守输入边界（含工具 schema）。
    max_input_chars: int = Field(default=240000, ge=0, le=2000000)
    auxiliary_thinking_enabled: bool = False
