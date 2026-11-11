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
from pydantic import BaseModel, TypeAdapter, ValidationError

from sheetmate.core.config.model_policy import LLMCapabilities, LLMMaxTokens, LLMRuntimePolicy, LLMTimeout

BACKEND_DIR = Path(__file__).resolve().parents[3]
PROJECT_DIR = BACKEND_DIR.parent

# 先读项目根目录的 .env，再读 backend/.env（两处都支持，前者优先）
load_dotenv(PROJECT_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-pro"
DEEPSEEK_THINKING_MODELS = (DEEPSEEK_DEFAULT_MODEL, "deepseek-flash")
DEEPSEEK_MODELS = DEEPSEEK_THINKING_MODELS
QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
QWEN_DEFAULT_MODEL = "qwen-plus"
QWEN_MODELS = (QWEN_DEFAULT_MODEL, "qwen-max", "deepseek-v3")
LLM_PROVIDER_PRIORITY = ("deepseek", "qwen", "custom")


def is_deepseek_url(base_url: str) -> bool:
    return base_url.rstrip("/") in {DEEPSEEK_BASE_URL, f"{DEEPSEEK_BASE_URL}/v1"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    value = _env(name, "1" if default else "0").lower()
    return value in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """进程级配置（启动时确定，修改需要重启）。"""

    data_dir: Path
    host: str
    port: int
    max_upload_mb: int
    agent_max_iterations: int
    repair_max_attempts: int
    verify_max_rounds: int
    seed_demo: bool
    admin_account: str
    admin_password: str
    frontend_dist: Path
    cors_origins: list[str] = field(default_factory=list)

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


def _load_settings() -> Settings:
    data_dir = Path(_env("SHEETMATE_DATA_DIR", str(BACKEND_DIR / "data"))).expanduser().resolve()
    origins = [o.strip() for o in _env("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
    return Settings(
        data_dir=data_dir,
        host=_env("HOST", "127.0.0.1"),
        port=_env_int("PORT", 8000),
        max_upload_mb=_env_int("MAX_UPLOAD_MB", 50),
        agent_max_iterations=_env_int("AGENT_MAX_ITERATIONS", 200),
        repair_max_attempts=_env_int("REPAIR_MAX_ATTEMPTS", 6),
        verify_max_rounds=_env_int("VERIFY_MAX_ROUNDS", 4),
        seed_demo=_env_bool("SEED_DEMO", True),
        admin_account=_env("ADMIN_ACCOUNT", "admin"),
        admin_password=_env("ADMIN_PASSWORD", "admin123"),
        frontend_dist=Path(_env("FRONTEND_DIST", str(PROJECT_DIR / "frontend" / "dist"))),
        cors_origins=origins,
    )


settings = _load_settings()


# ---------------------------------------------------------------------------
# LLM 配置（运行时可改）
# ---------------------------------------------------------------------------


@dataclass
class LLMSettings:
    """LLM 连接参数。OpenAI 兼容接口，默认 DeepSeek。"""

    api_key: str
    base_url: str
    model: str
    temperature: float = 0.1
    timeout: float = 1800.0
    max_tokens: int = 8192  # Recipe JSON 较长，默认预留 8K
    extra_body: dict = field(default_factory=dict)
    thinking_enabled: bool = False
    provider_id: str = "deepseek"
    capabilities: LLMCapabilities = field(default_factory=LLMCapabilities)
    runtime_policy: LLMRuntimePolicy = field(default_factory=LLMRuntimePolicy)
    enabled: bool = True

    @property
    def thinking_supported(self) -> bool:
        if self.provider_id == "qwen":
            return True
        return is_deepseek_url(self.base_url) and self.model in DEEPSEEK_THINKING_MODELS

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


_LLM_FIELDS = {
    "api_key", "base_url", "model", "temperature", "timeout", "max_tokens", "extra_body", "thinking_enabled",
    "capabilities", "runtime_policy", "enabled",
}
_llm_settings_lock = RLock()
_timeout_adapter = TypeAdapter(LLMTimeout)
_max_tokens_adapter = TypeAdapter(LLMMaxTokens)


def _saved_policy(model: type[BaseModel], value: object) -> BaseModel:
    """Older files have no policy; malformed hand-edited policy cannot break settings loading."""
    try:
        return model.model_validate({} if value is None else value)
    except ValidationError:
        return model()


_LEGACY_POLICY_DEFAULTS = {
    "structured_retries": 1, "tool_protocol_retries": 2, "sdk_retries": 1,
    "max_task_seconds": 600, "llm_hard_idle_seconds": 540,
    "stream_idle_seconds": 90, "tool_timeout_seconds": 120,
}


def _saved_runtime_policy(value: object) -> LLMRuntimePolicy:
    """Upgrade legacy shipped defaults once; retain customized limits.

    Any explicit compatibility mode marks the new schema. Legacy ``strict``
    normalizes to ``relaxed`` without migrating its explicit numeric values.
    A subsequent save writes that marker so old numeric choices stay exact.
    """
    if isinstance(value, dict) and "compatibility_mode" not in value:
        value = dict(value)
        defaults = LLMRuntimePolicy()
        for key, previous in _LEGACY_POLICY_DEFAULTS.items():
            if value.get(key) == previous:
                value[key] = getattr(defaults, key)
    return _saved_policy(LLMRuntimePolicy, value)


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
        thinking_enabled = False
    enabled = overrides.get("enabled", True)
    if not isinstance(enabled, bool):
        enabled = True
    base_url = str(overrides.get("base_url") or default_base_url).strip()
    model = str(overrides.get("model") or default_model).strip()
    policy = overrides.get("runtime_policy")
    legacy = not isinstance(policy, dict) or "compatibility_mode" not in policy
    saved_timeout = overrides.get("timeout", 1800)
    if legacy and saved_timeout == 120:
        saved_timeout = 1800
    try:
        timeout = _timeout_adapter.validate_python(float(saved_timeout))
    except (TypeError, ValueError):
        timeout = 1800.0
    try:
        max_tokens = _max_tokens_adapter.validate_python(overrides.get("max_tokens", 8192))
    except (TypeError, ValueError):
        max_tokens = 8192
    cfg = LLMSettings(
        api_key=str(overrides.get("api_key") or "").strip(),
        base_url=base_url,
        model=model,
        temperature=float(overrides.get("temperature", 0.1)),
        timeout=timeout,
        max_tokens=max_tokens,
        extra_body=extra_body,
        thinking_enabled=thinking_enabled,
        provider_id=provider_id,
        capabilities=_saved_policy(LLMCapabilities, overrides.get("capabilities")),
        runtime_policy=_saved_runtime_policy(policy),
        enabled=enabled,
    )
    cfg.thinking_enabled = thinking_enabled and cfg.thinking_supported
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
                      thinking_enabled: bool | None = None, *, timeout: float | None = None,
                      max_tokens: int | None = None, capabilities: LLMCapabilities | dict | None = None,
                      runtime_policy: LLMRuntimePolicy | dict | None = None,
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
        # Materialize the legacy upgrade before applying explicit user edits.
        # In particular a newly selected 120 seconds must not be migrated again.
        llm["timeout"] = current.timeout
        llm["runtime_policy"] = current.runtime_policy.model_dump()
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
        if timeout is not None:
            llm["timeout"] = _timeout_adapter.validate_python(timeout)
        if max_tokens is not None:
            llm["max_tokens"] = _max_tokens_adapter.validate_python(max_tokens)
        for name, policy, schema in (("capabilities", capabilities, LLMCapabilities),
                                     ("runtime_policy", runtime_policy, LLMRuntimePolicy)):
            if policy is not None:
                changes = policy.model_dump(exclude_unset=True) if isinstance(policy, BaseModel) else policy
                validated = schema.model_validate({**getattr(current, name).model_dump(), **changes})
                llm[name] = validated.model_dump()
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
