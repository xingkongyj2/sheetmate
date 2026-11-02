"""全局配置。

配置来源（优先级从高到低）：
1. data/settings.json —— 在页面「设置」里保存的 LLM 配置（运行时可改，不用重启）
2. 环境变量 / 项目根目录的 .env 文件
3. 代码里的默认值（默认对接 DeepSeek）

只要在 .env 里写上 DEEPSEEK_API_KEY=sk-xxx 就能直接使用。
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent

# 先读项目根目录的 .env，再读 backend/.env（两处都支持，前者优先）
load_dotenv(PROJECT_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-pro"
DEEPSEEK_MODELS = (DEEPSEEK_DEFAULT_MODEL, "deepseek-flash")


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
        agent_max_iterations=_env_int("AGENT_MAX_ITERATIONS", 12),
        repair_max_attempts=_env_int("REPAIR_MAX_ATTEMPTS", 3),
        verify_max_rounds=_env_int("VERIFY_MAX_ROUNDS", 2),
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
    timeout: float = 120.0
    max_tokens: int = 8192  # Recipe JSON 较长，默认预留 8K，可通过 LLM_MAX_TOKENS 调整
    extra_body: dict = field(default_factory=dict)
    thinking_enabled: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

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
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def get_llm_settings() -> LLMSettings:
    """合并 .env 默认值与设置页覆盖值，每次调用都重新读取（文件很小）。"""
    overrides = _read_settings_file().get("llm", {})
    extra_raw = _env("LLM_EXTRA_JSON", "")
    try:
        extra = json.loads(extra_raw) if extra_raw else {}
    except json.JSONDecodeError:
        extra = {}
    extra_body = overrides.get("extra_body", extra) or {}
    if not isinstance(extra_body, dict):
        extra_body = {}
    thinking_enabled = overrides.get("thinking_enabled")
    if not isinstance(thinking_enabled, bool):
        # 保留已有额外参数中的明确选择；未配置时默认关闭思考。
        thinking = extra_body.get("thinking", {})
        thinking_enabled = isinstance(thinking, dict) and thinking.get("type") == "enabled"
    env_key = _env("LLM_API_KEY") or _env("DEEPSEEK_API_KEY")
    base_url = overrides.get("base_url") or _env("LLM_BASE_URL") or DEEPSEEK_BASE_URL
    model = overrides.get("model") or _env("LLM_MODEL") or DEEPSEEK_DEFAULT_MODEL
    # 旧安装可能在 .env 或设置页保存过旧模型名，随默认模型一起迁移。
    if is_deepseek_url(base_url):
        if model in {"deepseek-chat", "deepseek-reasoner"}:
            model = DEEPSEEK_DEFAULT_MODEL
    return LLMSettings(
        api_key=overrides.get("api_key") or env_key,
        base_url=base_url,
        model=model,
        temperature=float(overrides.get("temperature", _env("LLM_TEMPERATURE", "0.1") or 0.1)),
        timeout=float(_env("LLM_TIMEOUT", "120") or 120),
        max_tokens=_env_int("LLM_MAX_TOKENS", 8192),
        extra_body=extra_body,
        thinking_enabled=thinking_enabled,
    )


def save_llm_settings(api_key: str | None, base_url: str | None, model: str | None,
                      thinking_enabled: bool | None = None) -> LLMSettings:
    """保存设置页提交的 LLM 配置。api_key 传 None 表示保持原值，传空串表示清除。"""
    data = _read_settings_file()
    llm = data.get("llm", {})
    if api_key is not None:
        llm["api_key"] = api_key.strip()
    if base_url is not None:
        llm["base_url"] = base_url.strip()
    if model is not None:
        llm["model"] = model.strip()
    if thinking_enabled is not None:
        llm["thinking_enabled"] = thinking_enabled
    data["llm"] = {k: v for k, v in llm.items() if v or k == "thinking_enabled"}
    settings.ensure_dirs()
    settings.settings_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(settings.settings_file, 0o600)  # 里面有 API Key：只允许当前用户读写
    except OSError:
        pass
    return get_llm_settings()


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
