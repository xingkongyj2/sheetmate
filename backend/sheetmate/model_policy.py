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
