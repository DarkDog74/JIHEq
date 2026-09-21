from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_ROOT = Path(
    os.environ.get("AUDIT_PLATFORM_OUTPUT", PROJECT_ROOT / "output" / "单系统")
).expanduser().resolve()
STATE_ROOT = OUTPUT_ROOT / ".state"
STAGING_ROOT = OUTPUT_ROOT / ".staging"
DATABASE_PATH = STATE_ROOT / "platform.db"
SECRETS_PATH = PROJECT_ROOT / "secrets.env"

DEFAULT_LLM_ENDPOINT = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
DEFAULT_LLM_MODEL = "glm-5v-turbo"
DEFAULT_LLM_PROMPT_VERSION = "audit-narration-v5"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class AISettings:
    """Runtime-only AI configuration. The key is intentionally hidden from repr."""

    enabled: bool
    api_key: str = field(repr=False)
    endpoint: str = DEFAULT_LLM_ENDPOINT
    model: str = DEFAULT_LLM_MODEL
    timeout_seconds: float = 90.0
    max_tokens: int = 1024
    temperature: float = 0.2
    max_input_bytes: int = 32 * 1024

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def available(self) -> bool:
        return self.enabled and self.configured


def _read_secrets_file(path: Path) -> dict[str, str]:
    """Read a small dotenv-style file as data; never execute or expand its content."""
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        return {}
    try:
        if candidate.stat().st_size > 64 * 1024:
            return {}
        content = candidate.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        return {}

    values: dict[str, str] = {}
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if not _ENV_NAME.fullmatch(name):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[name] = value
    return values


def _config_value(
    name: str,
    *,
    environment: Mapping[str, str],
    file_values: Mapping[str, str],
    default: str,
) -> str:
    if name in environment:
        return str(environment[name]).strip()
    return str(file_values.get(name, default)).strip()


def _as_bool(value: str, default: bool) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _as_float(value: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _as_int(value: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def load_ai_settings(
    *,
    environment: Mapping[str, str] | None = None,
    secrets_path: Path | None = None,
) -> AISettings:
    """读取网页保存的统一模型配置；secrets.env 仅保留作回滚，不再生效。"""
    source_environment = os.environ if environment is None else environment
    # 延迟导入可避免 model_config 读取 STATE_ROOT 时产生循环依赖。
    from .model_config import load_model_config

    model_config = load_model_config()
    base_url = str(model_config.get("base_url") or "").rstrip("/")
    endpoint = (
        base_url
        if base_url.endswith("/chat/completions")
        else f"{base_url}/chat/completions"
    )
    return AISettings(
        # 仅保留环境开关用于无代码运行期回滚；密钥和模型参数不再从环境读取。
        enabled=_as_bool(str(source_environment.get("AUDIT_LLM_ENABLED", "true")), True),
        api_key=str(model_config.get("api_key") or ""),
        endpoint=endpoint or DEFAULT_LLM_ENDPOINT,
        model=str(model_config.get("model") or DEFAULT_LLM_MODEL),
        timeout_seconds=float(model_config.get("timeout_seconds") or 120),
        max_tokens=1024,
        temperature=0.2,
        # This limit is deliberately not configurable above the confirmed 32 KiB cap.
        max_input_bytes=32 * 1024,
    )


def ai_config_status(settings: AISettings | None = None) -> dict[str, object]:
    """Return non-sensitive AI configuration state for health/UI endpoints."""
    current = settings or load_ai_settings()
    endpoint_host = urlsplit(current.endpoint).hostname or ""
    return {
        "enabled": current.enabled,
        "configured": current.configured,
        "available": current.available,
        "model": current.model,
        "endpoint_host": endpoint_host,
        "prompt_version": DEFAULT_LLM_PROMPT_VERSION,
        "max_input_bytes": current.max_input_bytes,
    }


def ensure_storage() -> None:
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    STATE_ROOT.mkdir(parents=True, exist_ok=True)
    STAGING_ROOT.mkdir(parents=True, exist_ok=True)
