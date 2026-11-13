"""模型协议能力与运行策略的结构定义。运行策略的取值全部来自环境变量（见 settings.env_runtime_policy）。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator



class LLMCapabilities(BaseModel):
    """模型服务的协议能力；在设置页「结构协议」里按服务选择，随提供商保存在 settings.json。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    # 结构化输出用哪种协议：auto 自动兼容（优先 JSON 模式，服务明确不支持时改用提示词）/
    # json_schema 优先 JSON Schema / json_object 优先 JSON 模式 / prompt 仅靠提示词约束。
    structured_output: Literal["auto", "json_schema", "json_object", "prompt"] = "auto"

    # 思考开关用哪家的请求参数：auto 自动识别 / deepseek / qwen / none 不自动发送思考参数。
    thinking_protocol: Literal["auto", "deepseek", "qwen", "none"] = "auto"


class LLMRuntimePolicy(BaseModel):
    """模型运行策略。取值全部来自环境变量（见 settings.env_runtime_policy 和 .env.example），不经接口读写。"""

    model_config = ConfigDict(extra="forbid", strict=True)

    # 旧配置的迁移标记，固定为 relaxed（宽松兼容：适合私有部署和较弱模型）；工具参数仍须有效。
    compatibility_mode: Literal["relaxed"] = "relaxed"

    # 回复流中途断开后，自动恢复的最多次数。（LLM_STREAM_RECOVERY_RETRIES）
    stream_recovery_retries: int = Field(default=20, ge=0, le=1000)

    # 结构化 JSON 输出格式不对时，让模型重新输出的最多次数；0 = 不纠错。（LLM_STRUCTURED_RETRIES）
    structured_retries: int = Field(default=20, ge=0, le=1000)

    # 工具调用参数不完整或格式不对时，让模型重新发起的最多次数；0 = 不纠错。（LLM_TOOL_PROTOCOL_RETRIES）
    tool_protocol_retries: int = Field(default=40, ge=0, le=1000)

    # 连接错误、限流、5xx 时，网络层自动重试的最多次数；0 = 不重试。（LLM_SDK_RETRIES）
    sdk_retries: int = Field(default=10, ge=0, le=1000)

    # 整次结构化调用（含纠错重试）的总时长上限（秒）；0 = 不额外限制，单次请求仍受请求超时约束。（LLM_STRUCTURED_TIMEOUT_SECONDS）
    structured_timeout: float = Field(default=0, ge=0, le=10_000_000, allow_inf_nan=False)

    # 对话上下文超过多少字符后，自动把较早的执行过程收成简短进度；只整理，不中止任务；0 = 不按字符触发。（LLM_CONTEXT_TARGET_CHARS）
    context_target_chars: int = Field(default=80000, ge=0, le=2_000_000_000)

    # 模型服务实际的上下文容量（Token），用于给输出和安全余量预留空间；0 = 未知，不从模型名称猜测。（LLM_CONTEXT_WINDOW_TOKENS）
    context_window_tokens: int = Field(default=0, ge=0, le=2_000_000_000)

    # 是否开启任务预算：False 时只记录用量，下面 5 项预算都不生效；True 时超过任何一项就保留已有结果并停止任务。（LLM_BUDGET_ENABLED）
    budget_enabled: bool = False

    # 预算：单次用户任务最多调用模型多少次（嵌套工具、核对、重试都计入）；0 = 不限。（LLM_MAX_MODEL_CALLS）
    max_model_calls: int = Field(default=100_000, ge=0, le=1_000_000_000)

    # 预算：单次任务累计输入 Token 上限；0 = 不限。（LLM_MAX_PROMPT_TOKENS）
    max_prompt_tokens: int = Field(default=1_000_000_000, ge=0, le=2_000_000_000_000)

    # 预算：单次任务累计总 Token（输入 + 输出）上限；0 = 不限。（LLM_MAX_TOTAL_TOKENS）
    max_total_tokens: int = Field(default=2_000_000_000, ge=0, le=2_000_000_000_000)

    # 预算：单次任务调用模型的总时长上限（秒）；0 = 不限。（LLM_MAX_TASK_SECONDS）
    max_task_seconds: float = Field(default=604_800, ge=0, le=10_000_000, allow_inf_nan=False)

    # 预算：单次请求输入内容的字符数上限（含工具定义），用于没有 tokenizer 的兼容服务；0 = 不限。（LLM_MAX_INPUT_CHARS）
    max_input_chars: int = Field(default=100_000_000, ge=0, le=2_000_000_000)

    # 辅助判断（意图识别、核对、计划等）是否也启用模型的深度思考；False 更快更省。（LLM_AUXILIARY_THINKING）
    auxiliary_thinking_enabled: bool = False

    # 模型多久没有新内容时，界面只提示「仍在等待」而不中止任务（秒）；0 = 不提示。（LLM_SOFT_IDLE_SECONDS）
    llm_soft_idle_seconds: float = Field(default=90, ge=0, le=10_000_000, allow_inf_nan=False)

    # 模型长时间没有任何响应时，最多等多久后放弃（秒）；工具有进展也不会重置这个计时。（LLM_HARD_IDLE_SECONDS）
    llm_hard_idle_seconds: float = Field(default=86_400, gt=0, le=10_000_000, allow_inf_nan=False)

    # 回复流连续多久没有新内容就判定断流（秒）。（LLM_STREAM_IDLE_SECONDS）
    stream_idle_seconds: float = Field(default=7200, gt=0, le=10_000_000, allow_inf_nan=False)

    # 单个工具（读表、跑 SQL、运行脚本等）执行的最长等待（秒）。（TOOL_TIMEOUT_SECONDS）
    tool_timeout_seconds: float = Field(default=14_400, gt=0, le=10_000_000, allow_inf_nan=False)

    # 点击「停止」后，给模型和工具收尾的宽限时间（秒），超时强制结束。（LLM_CANCELLATION_GRACE_SECONDS）
    cancellation_grace_seconds: float = Field(default=5, gt=0, le=30, allow_inf_nan=False)

    @field_validator("compatibility_mode", mode="before")
    @classmethod
    def normalize_legacy_mode(cls, value: object) -> object:
        return "relaxed" if value == "strict" else value
