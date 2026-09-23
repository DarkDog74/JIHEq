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

# 网页端「选择文件夹」的浏览根目录。
# 服务器是无头环境，平台原有的原生弹窗（macOS 走 osascript、Linux 走 tkinter）
# 在容器里必然失败——缺 libtk 且没有 $DISPLAY，即使装上 tk 也没显示器可弹。
# 因此改成由后端列目录、网页自己渲染选择器。
# 限定在这个根之下，避免网页上能遍历整台服务器的文件系统。
BROWSE_ROOT = Path(
    os.environ.get("AUDIT_BROWSE_ROOT", "/data")
).expanduser().resolve()

# 网页端上传的待稽核材料落在这里。
# 用户在**自己的电脑上**用浏览器的文件夹选择框选目录，浏览器把每个文件连同
# 相对路径传上来，后端按原目录结构还原到这个根目录之下，再把路径交给
# 原有的校验/稽核流程。放在 OUTPUT_ROOT 的兄弟位置，保证挂载出来的
# 卷里、容器重建也不丢。
UPLOAD_ROOT = Path(
    os.environ.get("AUDIT_UPLOAD_ROOT", OUTPUT_ROOT.parent / "上传")
).expanduser().resolve()

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
    # 认证头名称与值前缀。这里写字面量而不是引用 model_config 里的常量：
    # config 被 model_config 反向导入，模块级引用会成环。
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    # 各厂商差异的通用出口：用户填的原始 JSON 文本，直接合并进请求体/请求头。
    # 非空时平台不再自动添加任何思考类参数，完全以用户写的为准。
    extra_body: str = ""
    extra_headers: str = ""

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
    # 端点由「接入地址 + 对话路径」拼成；对话路径留空表示接入地址本身就是完整端点
    # （自建网关常见）。不再硬编码 /chat/completions。
    from .model_config import build_endpoint

    endpoint = build_endpoint(
        model_config.get("base_url"), model_config.get("chat_path")
    )
    return AISettings(
        # 仅保留环境开关用于无代码运行期回滚；密钥和模型参数不再从环境读取。
        enabled=_as_bool(str(source_environment.get("AUDIT_LLM_ENABLED", "true")), True),
        api_key=str(model_config.get("api_key") or ""),
        endpoint=endpoint or DEFAULT_LLM_ENDPOINT,
        auth_header=str(model_config.get("auth_header") or "Authorization"),
        auth_scheme=str(model_config.get("auth_scheme") or ""),
        model=str(model_config.get("model") or DEFAULT_LLM_MODEL),
        timeout_seconds=float(model_config.get("timeout_seconds") or 120),
        # 可配置：推理模型的思考也计入这个额度，1024 常常不够，思考吃满后正文为空
        max_tokens=int(model_config.get("max_tokens") or 1024),
        temperature=0.2,
        extra_body=str(model_config.get("extra_body") or ""),
        extra_headers=str(model_config.get("extra_headers") or ""),
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
