"""全局配置。

应用运行参数从环境变量或 .env 读取；模型服务配置只从设置页保存的
data/settings.json 读取，运行时可改且无需重启。
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from sheetmate.core.config.model_policy import LLMCapabilities, LLMRuntimePolicy

BACKEND_DIR = Path(__file__).resolve().parents[3]
PROJECT_DIR = BACKEND_DIR.parent

# 先读项目根目录的 .env，再读 backend/.env（两处都支持，前者优先）
load_dotenv(PROJECT_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-pro"
DEEPSEEK_THINKING_MODELS = (DEEPSEEK_DEFAULT_MODEL, "deepseek-flash")
DEEPSEEK_MODELS = DEEPSEEK_THINKING_MODELS
# Exact compatibility aliases observed on official responses or compatible
# gateways. Unknown endpoints still need an explicit protocol or body flag.
DEEPSEEK_THINKING_ALIASES = ("deepseek-v4-flash", "deepseek-v4-1-flash", "deepseek-v4-1-pro")
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_DEFAULT_MODEL = "qwen-plus"
QWEN_MODELS = (QWEN_DEFAULT_MODEL, "qwen-max", "deepseek-v3")
QWEN_THINKING_MODELS = (QWEN_DEFAULT_MODEL, "qwen-flash", "qwen3.8-max", "qwen3.8-flash")
LLM_PROVIDER_PRIORITY = ("deepseek", "qwen", "custom")
# 未开启「限制输出 Token 上限」时，每次请求先尝试的输出上限（与设置页允许的最大值一致）；
# 模型支持的上限更小时，请求会自动降到模型报错里给出的真实上限。
AUTO_MAX_TOKENS = 131072


def is_deepseek_url(base_url: str) -> bool:
    return base_url.rstrip("/") in {DEEPSEEK_BASE_URL, f"{DEEPSEEK_BASE_URL}/v1"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name, "1" if default else "0").lower()
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """进程级配置（启动时确定，修改需要重启）。"""

    # 数据目录：数据库、上传文件、输出文件、工作区都在这里。环境变量 SHEETMATE_DATA_DIR，默认 backend/data。
    data_dir: Path
    # 后端监听地址。环境变量 HOST。
    host: str
    # 后端监听端口。环境变量 PORT。
    port: int
    # 单个上传文件的大小上限（MB）。环境变量 MAX_UPLOAD_MB。
    max_upload_mb: int
    # ReAct 循环最多推理几轮。环境变量 AGENT_MAX_ITERATIONS。
    agent_max_iterations: int
    # 流程某一步出错时，AI 最多自动修复几次。环境变量 REPAIR_MAX_ATTEMPTS。
    repair_max_attempts: int
    # 生成流程后与人工结果比对不通过时，最多再修正几轮。环境变量 VERIFY_MAX_ROUNDS。
    verify_max_rounds: int
    # 首次启动是否写入示例客户。环境变量 SEED_DEMO。
    seed_demo: bool
    # 首次启动自动创建的管理员账号。环境变量 ADMIN_ACCOUNT。
    admin_account: str
    # 首次启动自动创建的管理员密码。环境变量 ADMIN_PASSWORD。
    admin_password: str
    # 前端构建产物目录，由后端一起托管。环境变量 FRONTEND_DIST。
    frontend_dist: Path
    # 开发模式下允许跨域访问的前端地址。环境变量 CORS_ORIGINS（逗号分隔）。
    cors_origins: list[str] = field(default_factory=list)
    # OOXML 结构编辑：auto=原件含 openpyxl 会丢失的对象时启用；always=可行时一律启用；off=关闭
    ooxml_edit: str = "auto"

    # ---- 派生路径：所有本地数据都在 data_dir 下 ----
    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def logs_dir(self) -> Path:
        """每个对话一个日志，内部按用户请求分轮记录。"""
        return self.data_dir / "log"

    @property
    def workspaces_dir(self) -> Path:
        """会话工作区（每个会话一个 DuckDB 文件）。"""
        return self.data_dir / "workspaces"

    @property
    def runs_dir(self) -> Path:
        """Recipe 运行工作区（每次运行一个 DuckDB 文件，支持断点续跑）。"""
        return self.data_dir / "runs"

    @property
    def settings_file(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def secret_file(self) -> Path:
        return self.data_dir / "secret.key"

    def ensure_dirs(self) -> None:
        for path in (self.data_dir, self.uploads_dir, self.outputs_dir, self.workspaces_dir, self.runs_dir):
            path.mkdir(parents=True, exist_ok=True)


def _ooxml_edit_mode() -> str:
    mode = _env("OOXML_EDIT", "auto").strip().lower()
    return mode if mode in {"auto", "always", "off"} else "auto"


def _load_settings() -> Settings:
    data_dir = Path(_env("SHEETMATE_DATA_DIR", str(BACKEND_DIR / "data"))).expanduser().resolve()
    origins = [o.strip() for o in _env("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
    return Settings(
        data_dir=data_dir,
        host=_env("HOST", "127.0.0.1"),
        port=_env_int("PORT", 8000),
        max_upload_mb=_env_int("MAX_UPLOAD_MB", 50),
        agent_max_iterations=_env_int("AGENT_MAX_ITERATIONS", 1000),
        repair_max_attempts=_env_int("REPAIR_MAX_ATTEMPTS", 30),
        verify_max_rounds=_env_int("VERIFY_MAX_ROUNDS", 20),
        seed_demo=_env_bool("SEED_DEMO", True),
        admin_account=_env("ADMIN_ACCOUNT", "admin"),
        admin_password=_env("ADMIN_PASSWORD", "admin123"),
        frontend_dist=Path(_env("FRONTEND_DIST", str(PROJECT_DIR / "frontend" / "dist"))),
        cors_origins=origins,
        ooxml_edit=_ooxml_edit_mode(),
    )


settings = _load_settings()


# ---------------------------------------------------------------------------
# LLM 配置（运行时可改）
# ---------------------------------------------------------------------------


@dataclass
class LLMSettings:
    """LLM 连接参数。OpenAI 兼容接口，默认 DeepSeek。"""

    # 模型服务的 API Key；在设置页填写，保存在 settings.json。
    api_key: str
    # 接口地址（OpenAI 兼容，不要带 /chat/completions）；在设置页填写。
    base_url: str
    # 模型名称，按原样发给服务端；在设置页填写。
    model: str
    # 采样温度，越低越稳定。
    temperature: float = 0.1
    # 单次模型请求的总超时（秒）。来自环境变量 LLM_TIMEOUT_SECONDS。
    timeout: float = 7200.0
    # 单次回复的输出 Token 上限。来自环境变量 LLM_MAX_TOKENS：0 表示不固定，请求自动尝试模型能接受的最大值。
    max_tokens: int = 0
    # 额外的请求体字段（私有部署的特殊参数）；只能手工写入 settings.json。
    extra_body: dict = field(default_factory=dict)
    # 是否启用模型自带的深度思考（与输入框旁的「思考模式」不是一回事）；在设置页开关。
    thinking_enabled: bool = False
    # 提供商：deepseek / qwen / custom。
    provider_id: str = "deepseek"
    # 协议能力（结构化输出协议、思考参数协议）；在设置页「结构协议」里选择。
    capabilities: LLMCapabilities = field(default_factory=LLMCapabilities)
    # 运行策略（重试、等待、预算等数值）。来自环境变量，见 env_runtime_policy。
    runtime_policy: LLMRuntimePolicy = field(default_factory=LLMRuntimePolicy)
    # 该提供商是否启用；关闭后不参与当前服务的选择。
    enabled: bool = True

    @property
    def output_limit(self) -> int:
        """环境变量固定的输出上限；0 表示没有固定（此时请求按 AUTO_MAX_TOKENS 尝试最大值）。"""
        return max(0, self.max_tokens)

    @property
    def thinking_protocol(self) -> str:
        return self.resolve_thinking_protocol(self.extra_body)

    def resolve_thinking_protocol(self, body: dict | None = None) -> str:
        configured = self.capabilities.thinking_protocol
        if configured != "auto":
            return configured
        if self.model == "deepseek-chat" and is_deepseek_url(self.base_url):
            return "none"
        # A hand-written protocol flag is also explicit authorization for a
        # proxy. Never infer one dialect from conflicting flags.
        body = body or {}
        thinking = body.get("thinking")
        deepseek = isinstance(thinking, dict) and thinking.get("type") in {"enabled", "disabled"}
        template = body.get("chat_template_kwargs")
        qwen = (isinstance(body.get("enable_thinking"), bool)
                or isinstance(template, dict) and isinstance(template.get("enable_thinking"), bool))
        if deepseek or qwen:
            return "none" if deepseek and qwen else "deepseek" if deepseek else "qwen"
        if is_deepseek_url(self.base_url) and self.model in (*DEEPSEEK_THINKING_MODELS, *DEEPSEEK_THINKING_ALIASES):
            return "deepseek"
        qwen_urls = {QWEN_BASE_URL, "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
                     "https://dashscope-us.aliyuncs.com/compatible-mode/v1"}
        if (self.base_url.rstrip("/") in qwen_urls
                and (self.model in QWEN_THINKING_MODELS
                     or self.model.startswith(("qwen-plus-", "qwen-flash-")))):
            return "qwen"
        return "none"

    @property
    def thinking_supported(self) -> bool:
        return self.thinking_protocol != "none"

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.base_url and self.model)

    def key_hint(self) -> str:
        """只展示 Key 的首尾几位，避免在页面泄露完整 Key。"""
        if not self.api_key:
            return ""
        if len(self.api_key) <= 10:
            return "*" * len(self.api_key)
        return f"{self.api_key[:5]}****{self.api_key[-4:]}"


def _read_settings_file() -> dict:
    path = settings.settings_file
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_settings_file(data: dict) -> None:
    """Atomically replace settings.json without exposing a partially written key file."""
    settings.ensure_dirs()
    path = settings.settings_file
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with stream:
            stream.write(json.dumps(data, ensure_ascii=False, indent=2))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


# settings.json 里只保存连接信息和协议选择；超时、重试、Token 上限、预算等数值全部来自环境变量。
_LLM_FIELDS = {
    "api_key", "base_url", "model", "temperature", "extra_body", "thinking_enabled", "capabilities", "enabled",
}
# 旧版设置页写进文件的数值项：读取时忽略，保存时清掉。
_RETIRED_LLM_FIELDS = ("timeout", "max_tokens", "max_tokens_enabled", "runtime_policy")
_llm_settings_lock = RLock()


def _saved_policy(model: type[BaseModel], value: object) -> BaseModel:
    """Older files have no policy; malformed hand-edited policy cannot break settings loading."""
    try:
        return model.model_validate({} if value is None else value)
    except ValidationError:
        return model()


def env_runtime_policy() -> LLMRuntimePolicy:
    """模型运行策略：全部来自环境变量（.env），默认值都放得很大，保证长任务能跑完；修改后重启生效。

    每个参数上方的注释是它的含义；字段的完整说明见 model_policy.LLMRuntimePolicy，环境变量清单见 .env.example。
    """
    return LLMRuntimePolicy(
        # 回复流中途断开后，自动恢复的最多次数。
        stream_recovery_retries=_env_int("LLM_STREAM_RECOVERY_RETRIES", 20),
        # 结构化 JSON 输出格式不对时，让模型重新输出的最多次数。
        structured_retries=_env_int("LLM_STRUCTURED_RETRIES", 20),
        # 工具调用参数不完整或格式不对时，让模型重新发起的最多次数。
        tool_protocol_retries=_env_int("LLM_TOOL_PROTOCOL_RETRIES", 40),
        # 连接错误、限流、5xx 时，网络层自动重试的最多次数。
        sdk_retries=_env_int("LLM_SDK_RETRIES", 10),
        # 整次结构化调用的总时长上限（秒）；0 = 不额外限制。
        structured_timeout=_env_float("LLM_STRUCTURED_TIMEOUT_SECONDS", 0),
        # 上下文超过多少字符后自动整理较早的执行过程；0 = 不按字符触发。
        context_target_chars=_env_int("LLM_CONTEXT_TARGET_CHARS", 80000),
        # 模型服务实际的上下文容量（Token）；0 = 未知。
        context_window_tokens=_env_int("LLM_CONTEXT_WINDOW_TOKENS", 0),
        # 是否开启任务预算；关闭时下面 5 项预算都不生效。
        budget_enabled=_env_bool("LLM_BUDGET_ENABLED", False),
        # 预算：单次任务最多调用模型多少次；0 = 不限。
        max_model_calls=_env_int("LLM_MAX_MODEL_CALLS", 100_000),
        # 预算：单次任务累计输入 Token 上限；0 = 不限。
        max_prompt_tokens=_env_int("LLM_MAX_PROMPT_TOKENS", 1_000_000_000),
        # 预算：单次任务累计总 Token 上限；0 = 不限。
        max_total_tokens=_env_int("LLM_MAX_TOTAL_TOKENS", 2_000_000_000),
        # 预算：单次任务调用模型的总时长上限（秒）；0 = 不限。
        max_task_seconds=_env_float("LLM_MAX_TASK_SECONDS", 604_800),
        # 预算：单次请求输入内容的字符数上限；0 = 不限。
        max_input_chars=_env_int("LLM_MAX_INPUT_CHARS", 100_000_000),
        # 辅助判断是否也启用模型的深度思考。
        auxiliary_thinking_enabled=_env_bool("LLM_AUXILIARY_THINKING", False),
        # 模型多久没有新内容时界面只提示「仍在等待」（秒）；0 = 不提示。
        llm_soft_idle_seconds=_env_float("LLM_SOFT_IDLE_SECONDS", 90),
        # 模型长时间没有任何响应时，最多等多久后放弃（秒）。
        llm_hard_idle_seconds=_env_float("LLM_HARD_IDLE_SECONDS", 86_400),
        # 回复流连续多久没有新内容就判定断流（秒）。
        stream_idle_seconds=_env_float("LLM_STREAM_IDLE_SECONDS", 7200),
        # 单个工具执行的最长等待（秒）。
        tool_timeout_seconds=_env_float("TOOL_TIMEOUT_SECONDS", 14_400),
        # 点击「停止」后，给模型和工具收尾的宽限时间（秒）。
        cancellation_grace_seconds=_env_float("LLM_CANCELLATION_GRACE_SECONDS", 5),
    )


def _provider_defaults(provider_id: str) -> tuple[str, str]:
    if provider_id == "deepseek":
        return DEEPSEEK_BASE_URL, DEEPSEEK_DEFAULT_MODEL
    if provider_id == "qwen":
        return QWEN_BASE_URL, QWEN_DEFAULT_MODEL
    if provider_id == "custom":
        return "", ""
    raise ValueError(f"Unknown LLM provider: {provider_id}")


def _legacy_provider_id(overrides: dict) -> str:
    base_url = str(overrides.get("base_url") or DEEPSEEK_BASE_URL).strip().rstrip("/")
    if is_deepseek_url(base_url):
        return "deepseek"
    if base_url == QWEN_BASE_URL.rstrip("/"):
        return "qwen"
    return "custom"


def _provider_overrides(llm: object) -> dict[str, dict]:
    """Read the multi-provider schema and fold in the legacy single config."""
    if not isinstance(llm, dict):
        return {}
    result: dict[str, dict] = {}
    providers = llm.get("providers")
    if isinstance(providers, dict):
        for provider_id in LLM_PROVIDER_PRIORITY:
            value = providers.get(provider_id)
            if isinstance(value, dict):
                result[provider_id] = dict(value)
    legacy = {key: value for key, value in llm.items() if key in _LLM_FIELDS}
    if legacy:
        provider_id = _legacy_provider_id(legacy)
        result.setdefault(provider_id, legacy)
    return result


def _build_provider_settings(provider_id: str, overrides: dict) -> LLMSettings:
    """Build a provider config from a single saved snapshot."""
    default_base_url, default_model = _provider_defaults(provider_id)
    extra_body = overrides.get("extra_body", {}) or {}
    if not isinstance(extra_body, dict):
        extra_body = {}
    thinking_enabled = overrides.get("thinking_enabled")
    if not isinstance(thinking_enabled, bool):
        # Preserve manually configured flags when upgrading older settings
        # that never exposed a separate switch for a custom service.
        thinking = extra_body.get("thinking")
        template = extra_body.get("chat_template_kwargs")
        thinking_enabled = (extra_body.get("enable_thinking") is True
                            or isinstance(template, dict) and template.get("enable_thinking") is True
                            or isinstance(thinking, dict) and thinking.get("type") == "enabled")
    enabled = overrides.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = True
    base_url = str(overrides.get("base_url") or default_base_url).strip()
    model = str(overrides.get("model") or default_model).strip()
    cfg = LLMSettings(
        api_key=str(overrides.get("api_key") or "").strip(),
        base_url=base_url,
        model=model,
        temperature=float(overrides.get("temperature", 0.1)),
        timeout=_env_float("LLM_TIMEOUT_SECONDS", 7200),
        max_tokens=max(0, _env_int("LLM_MAX_TOKENS", 0)),
        extra_body=extra_body,
        thinking_enabled=thinking_enabled,
        provider_id=provider_id,
        capabilities=_saved_policy(LLMCapabilities, overrides.get("capabilities")),
        runtime_policy=env_runtime_policy(),
        enabled=enabled,
    )
    return cfg


def get_llm_provider_settings(provider_id: str) -> LLMSettings:
    """Read one provider config from disk so changes apply without a restart."""
    providers = _provider_overrides(_read_settings_file().get("llm", {}))
    return _build_provider_settings(provider_id, providers.get(provider_id, {}))


def get_all_llm_settings() -> dict[str, LLMSettings]:
    providers = _provider_overrides(_read_settings_file().get("llm", {}))
    return {provider_id: _build_provider_settings(provider_id, providers.get(provider_id, {}))
            for provider_id in LLM_PROVIDER_PRIORITY}


def select_llm_settings(providers: dict[str, LLMSettings]) -> LLMSettings:
    """Select only enabled, configured providers using the fixed priority order."""
    return next((providers[provider_id] for provider_id in LLM_PROVIDER_PRIORITY
                 if providers[provider_id].enabled and providers[provider_id].configured),
                LLMSettings(api_key="", base_url=DEEPSEEK_BASE_URL, model=DEEPSEEK_DEFAULT_MODEL, enabled=False))


def get_llm_settings() -> LLMSettings:
    return select_llm_settings(get_all_llm_settings())


def save_llm_settings(provider_id: str, api_key: str | None, base_url: str | None, model: str | None,
                      thinking_enabled: bool | None = None, *,
                      capabilities: LLMCapabilities | dict | None = None,
                      enabled: bool | None = None) -> LLMSettings:
    """Save one provider. None keeps a field; an empty API key clears it."""
    _provider_defaults(provider_id)  # validate before touching the file
    # FastAPI runs sync handlers in separate threads. Keep saves to different
    # providers from replacing one another's changes during read/modify/write.
    with _llm_settings_lock:
        data = _read_settings_file()
        providers = _provider_overrides(data.get("llm", {}))
        llm = dict(providers.get(provider_id, {}))
        current = _build_provider_settings(provider_id, llm)
        for retired in _RETIRED_LLM_FIELDS:  # 旧版保存的数值项不再使用，顺便清理
            llm.pop(retired, None)
        if api_key is not None:
            llm["api_key"] = api_key.strip()
        if base_url is not None:
            llm["base_url"] = base_url.strip()
        if model is not None:
            llm["model"] = model.strip()
        if thinking_enabled is not None:
            llm["thinking_enabled"] = thinking_enabled
        if enabled is not None:
            llm["enabled"] = enabled
        if capabilities is not None:
            changes = capabilities.model_dump(exclude_unset=True) if isinstance(capabilities, BaseModel) else capabilities
            llm["capabilities"] = LLMCapabilities.model_validate({**current.capabilities.model_dump(), **changes}).model_dump()
        # false and 0 are real configuration values, including disabled retries.
        providers[provider_id] = {k: v for k, v in llm.items() if v is not None and v != ""}
        data["llm"] = {"providers": {
            current_id: providers[current_id] for current_id in LLM_PROVIDER_PRIORITY if providers.get(current_id)
        }}
        _write_settings_file(data)
        return _build_provider_settings(provider_id, providers[provider_id])


def get_secret_key() -> bytes:
    """登录令牌签名密钥：首次启动自动生成并保存在 data/secret.key。"""
    settings.ensure_dirs()
    path = settings.secret_file
    if path.exists():
        return path.read_bytes()
    secret = secrets.token_bytes(32)
    path.write_bytes(secret)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return secret
