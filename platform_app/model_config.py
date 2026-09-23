"""模型 API 接入配置：持久化、脱敏读取与连通性测试。

配置保存在平台状态目录（STATE_ROOT/model_config.json），供网页平台
与后续 CLI 模型稽核共用；密钥只在本地保存，接口返回时做脱敏处理。
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Mapping

import httpx

from .config import STATE_ROOT, ensure_storage
from .llm_transport import (
    LLMStreamError,
    consume_stream,
    log_llm_request,
    log_llm_response,
    start_timer,
)

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-5v-turbo"
SUGGESTED_MODELS = ("glm-5v-turbo", "glm-5.3-flash")
DEFAULT_TIMEOUT_SECONDS = 120
MIN_TIMEOUT_SECONDS = 3
MAX_TIMEOUT_SECONDS = 300
_MAX_ERROR_DETAIL_CHARS = 240
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")

# ---- 请求形态：默认值等于平台一直以来的行为，改这些是为了适配非标准网关 ----
# 认证头名称。OpenAI 兼容服务用 Authorization；有些网关用自定义头，
# 例如 Authorization-Gateway。
DEFAULT_AUTH_HEADER = "Authorization"
# 认证值的前缀。标准是 "Bearer <key>"；留空表示头里只放密钥本身。
DEFAULT_AUTH_SCHEME = "Bearer"
# 追加在接入地址后面的对话路径。留空表示接入地址本身就是完整端点。
DEFAULT_CHAT_PATH = "/chat/completions"
# 是否用流式（SSE）请求。部分网关只接受 stream=true。
DEFAULT_STREAM = False
# 思考参数不由平台配置决定，而是走「附加请求参数」这个通用出口：
# 各厂商参数名不统一（Qwen 用 enable_thinking，智谱和 DeepSeek 用
# thinking.type），与其为此维护一堆开关，不如让用户直接写。
# 平台只在用户没写附加参数时，按下面的历史行为补一个默认值。
#
# 说明生成路径对 glm-5.3-flash 的特殊处理：该系列不支持关闭思考，
# 且 reasoning_effort 只认 max/high/low。
GLM_53_FLASH_MODEL = "glm-5.3-flash"
DEFAULT_MAX_TOKENS = 1024
MIN_MAX_TOKENS = 128
MAX_MAX_TOKENS = 32768
_MAX_EXTRA_CHARS = 8192


def parse_extra_body(config: Mapping[str, Any]) -> dict[str, Any]:
    """解析「附加请求参数」。

    这是适配各厂商差异的通用出口：平台只负责 OpenAI 通用信封，厂商特有的参数
    （Qwen 的 enable_thinking / thinking_budget、智谱和 DeepSeek 的
    thinking.type / reasoning_effort 等）全部由用户写 JSON 塞进来，
    不用为新厂商改代码。存的是用户填的原始文本，这里解析成字典。
    """
    raw = str(config.get("extra_body") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        # 保存时已经校验过，这里只是兜底，不让坏配置把请求打死
        return {}
    return parsed if isinstance(parsed, dict) else {}


def parse_extra_headers(config: Mapping[str, Any]) -> dict[str, str]:
    """解析「附加请求头」。键值都按字符串用。"""
    raw = str(config.get("extra_headers") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(value) for key, value in parsed.items()}


def build_thinking_params(model: str, *, default_disabled: bool) -> dict[str, Any]:
    """平台在用户**没有**填「附加请求参数」时补的默认思考参数。

    用户填了附加参数的话，调用方根本不该走到这里——各家参数名不统一，
    以用户写的为准（见 merge_extra 的说明）。

    default_disabled 说明两条历史路径原本的默认并不一致：
      说明生成（ai_service）原本就发 thinking:disabled，传 True
      规则调用（model_client）原本什么都不发，传 False
    """
    if str(model).strip().casefold() == GLM_53_FLASH_MODEL:
        return {
            "thinking": {"type": "enabled", "clear_thinking": False},
            "reasoning_effort": "low",
        }
    return {"thinking": {"type": "disabled"}} if default_disabled else {}


def merge_extra(
    body: dict[str, Any],
    headers: dict[str, str],
    *,
    extra_body: Any = "",
    extra_headers: Any = "",
) -> tuple[dict[str, Any], dict[str, str]]:
    """把「附加请求参数 / 附加请求头」合并进请求。

    这是适配各厂商差异的通用出口：放在最后合并，因此能覆盖平台生成的任何字段
    （max_tokens、thinking 等都盖得过）。参数形式只吃原始 JSON 文本，
    配置字典和 AISettings 都能直接用。
    """
    parsed_body = parse_extra_body({"extra_body": extra_body})
    parsed_headers = parse_extra_headers({"extra_headers": extra_headers})
    if parsed_body:
        body = {**body, **parsed_body}
    if parsed_headers:
        headers = {**headers, **parsed_headers}
    return body, headers

_AUTH_HEADER_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}$")
_MAX_CHAT_PATH_CHARS = 128
_MAX_AUTH_SCHEME_CHARS = 32

# 前端「认证方式」下拉的一键预设，按常用组合给出
AUTH_PRESETS = (
    {
        "key": "openai",
        "label": "OpenAI 兼容（默认）",
        "auth_header": DEFAULT_AUTH_HEADER,
        "auth_scheme": DEFAULT_AUTH_SCHEME,
        "chat_path": DEFAULT_CHAT_PATH,
        "stream": DEFAULT_STREAM,
    },
    {
        "key": "custom-header-gateway",
        "label": "自定义认证头的网关（流式）",
        "auth_header": "Authorization-Gateway",
        "auth_scheme": "",
        "chat_path": "",
        "stream": True,
    },
)

MODEL_CONFIG_PATH = STATE_ROOT / "model_config.json"
CONFIG_SCHEMA_VERSION = "unified-glm-5v-v1"

# 注意：schema_version 保持不变。新增字段都有默认值，旧配置读出来仍然可用；
# 一旦改版本号，load_model_config 会丢弃整份配置，用户得重新填密钥。
_FIELDS = ("base_url", "api_key")


def build_endpoint(base_url: Any, chat_path: Any) -> str:
    """把接入地址和对话路径拼成实际请求的 URL。

    chat_path 为空表示接入地址本身就是完整端点（例如自建网关），不再追加。
    """
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""
    path = str(chat_path or "").strip()
    if not path:
        return base
    return base + (path if path.startswith("/") else "/" + path)


def build_auth_headers(config: Mapping[str, Any], *, json_body: bool = True) -> dict[str, str]:
    """按配置生成请求头。

    auth_scheme 为空时头里只放密钥本身（例如 Authorization-Gateway: sk-xxx），
    否则放 "<scheme> <key>"（例如 Authorization: Bearer sk-xxx）。
    """
    headers: dict[str, str] = {}
    if json_body:
        headers["Content-Type"] = "application/json"
    api_key = str(config.get("api_key") or "")
    if not api_key:
        return headers
    name = str(config.get("auth_header") or DEFAULT_AUTH_HEADER).strip()
    scheme = str(config.get("auth_scheme") or "").strip()
    headers[name] = f"{scheme} {api_key}" if scheme else api_key
    return headers


def _clamp_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS
    return min(max(timeout, MIN_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS)


def _normalize_model(value: Any) -> str:
    model = str(value or DEFAULT_MODEL).strip()
    if not _MODEL_NAME_PATTERN.fullmatch(model):
        raise ValueError(
            "模型编码只能包含字母、数字、点、下划线、短横线、冒号或斜杠，且最长128字符"
        )
    return model


def _normalize_auth_header(value: Any) -> str:
    header = str(value if value is not None else DEFAULT_AUTH_HEADER).strip()
    if not header:
        return DEFAULT_AUTH_HEADER
    if not _AUTH_HEADER_PATTERN.fullmatch(header):
        raise ValueError("认证头名称只能包含字母、数字和短横线，需以字母开头，最长64字符")
    return header


def _normalize_auth_scheme(value: Any) -> str:
    """认证值前缀。空字符串是有意义的值，表示只发密钥本身。"""
    scheme = str(value if value is not None else DEFAULT_AUTH_SCHEME).strip()
    if len(scheme) > _MAX_AUTH_SCHEME_CHARS:
        raise ValueError("认证前缀最长32字符")
    return scheme


def _normalize_chat_path(value: Any) -> str:
    """对话路径。空字符串有意义，表示接入地址本身就是完整端点。"""
    path = str(value if value is not None else DEFAULT_CHAT_PATH).strip()
    if len(path) > _MAX_CHAT_PATH_CHARS:
        raise ValueError("对话路径最长128字符")
    if path and not path.startswith("/"):
        path = "/" + path
    return path


def _normalize_stream(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value if value is not None else "").strip().casefold() in (
        "1", "true", "yes", "on",
    )


def _normalize_max_tokens(value: Any) -> int:
    try:
        tokens = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_TOKENS
    return min(max(tokens, MIN_MAX_TOKENS), MAX_MAX_TOKENS)


def _normalize_json_object(value: Any, label: str) -> str:
    """校验一段 JSON 对象文本，返回去空白后的原文。

    存原文而不是解析后的对象：前端是文本框，原样存回去才能保持用户写的格式，
    改一个字符也不用重排。
    """
    text = str(value if value is not None else "").strip()
    if not text:
        return ""
    if len(text) > _MAX_EXTRA_CHARS:
        raise ValueError(f"{label}最长 {_MAX_EXTRA_CHARS} 字符")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"{label}不是合法 JSON：第 {error.lineno} 行第 {error.colno} 列 {error.msg}"
        ) from error
    if not isinstance(parsed, dict):
        raise ValueError(f'{label}必须是一个 JSON 对象，例如 {{"enable_thinking": true}}')
    return text


def normalize_config(payload: Mapping[str, Any]) -> dict[str, Any]:
    """清洗外部传入的配置：去空白、Base URL 去尾部斜杠、超时夹紧。"""
    normalized = {
        field: str(payload.get(field) or "").strip().rstrip("/") for field in _FIELDS
    }
    normalized["base_url"] = normalized["base_url"] or DEFAULT_BASE_URL
    normalized["model"] = _normalize_model(payload.get("model"))
    normalized["auth_header"] = _normalize_auth_header(payload.get("auth_header"))
    normalized["auth_scheme"] = _normalize_auth_scheme(payload.get("auth_scheme"))
    normalized["chat_path"] = _normalize_chat_path(payload.get("chat_path"))
    normalized["stream"] = _normalize_stream(payload.get("stream"))
    normalized["max_tokens"] = _normalize_max_tokens(payload.get("max_tokens"))
    normalized["extra_body"] = _normalize_json_object(
        payload.get("extra_body"), "附加请求参数"
    )
    normalized["extra_headers"] = _normalize_json_object(
        payload.get("extra_headers"), "附加请求头"
    )
    normalized["schema_version"] = CONFIG_SCHEMA_VERSION
    normalized["timeout_seconds"] = _clamp_timeout(payload.get("timeout_seconds"))
    return normalized


def _tighten_permissions(path: Path) -> None:
    """把已存在的配置文件权限收紧为仅属主可读写；失败不影响读取。"""
    try:
        if path.exists() and (path.stat().st_mode & 0o777) != 0o600:
            os.chmod(path, 0o600)
    except OSError:
        pass


def load_model_config(path: Path = MODEL_CONFIG_PATH) -> dict[str, Any]:
    """读取完整配置（含明文密钥，仅限服务端内部使用）。

    读取时顺带收紧权限：升级部署前遗留的 0644 配置文件无需等下一次保存。
    """
    if not path.is_file():
        return {
            "base_url": DEFAULT_BASE_URL,
            "api_key": "",
            "model": DEFAULT_MODEL,
            "timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        }
    _tighten_permissions(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    if payload.get("schema_version") != CONFIG_SCHEMA_VERSION:
        # 不迁移旧版双模型配置或旧密钥；升级后必须在网页重新填写已轮换密钥。
        payload = {}
    try:
        return normalize_config(payload)
    except ValueError:
        # 配置文件被手工写入非法模型名时安全回退，不让平台无法启动。
        return normalize_config({})


def save_model_config(
    payload: Mapping[str, Any], path: Path = MODEL_CONFIG_PATH
) -> dict[str, Any]:
    """保存配置；api_key 留空表示沿用已保存密钥。返回脱敏视图。"""
    ensure_storage()
    existing = load_model_config(path)
    merged = dict(payload)
    if not str(merged.get("api_key") or "").strip():
        merged["api_key"] = existing["api_key"]
    if not str(merged.get("model") or "").strip():
        merged["model"] = existing["model"]
    normalized = normalize_config(merged)

    # 临时文件名带进程/线程标识：并发保存（保存 + 测试连接同时点击）时互不
    # replace 对方的临时文件；os.open 的 0o600 使文件自创建起即仅属主可读写，
    # 不存在 write→chmod 之间的宽权限窗口。
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    descriptor = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(normalized, ensure_ascii=False, indent=2))
    os.chmod(tmp_path, 0o600)  # 同名残留时统一收紧；新创建时与 0o600 一致
    tmp_path.replace(path)
    return public_view(normalized)


def mask_api_key(key: str) -> str:
    key = str(key or "")
    if not key:
        return ""
    if len(key) <= 8:
        return "••••"
    return f"{key[:3]}••••{key[-4:]}"


def public_view(config: Mapping[str, Any]) -> dict[str, Any]:
    """对外（含前端）的脱敏视图：绝不返回明文密钥。"""
    return {
        "base_url": str(config.get("base_url") or ""),
        "api_key_set": bool(str(config.get("api_key") or "")),
        "api_key_masked": mask_api_key(str(config.get("api_key") or "")),
        "model": str(config.get("model") or DEFAULT_MODEL),
        "suggested_models": list(SUGGESTED_MODELS),
        "timeout_seconds": _clamp_timeout(config.get("timeout_seconds")),
        "auth_header": _normalize_auth_header(config.get("auth_header")),
        "auth_scheme": _normalize_auth_scheme(config.get("auth_scheme")),
        "chat_path": _normalize_chat_path(config.get("chat_path")),
        "stream": _normalize_stream(config.get("stream")),
        "max_tokens": _normalize_max_tokens(config.get("max_tokens")),
        "extra_body": str(config.get("extra_body") or ""),
        "extra_headers": str(config.get("extra_headers") or ""),
        "extra_body_active": bool(parse_extra_body(config)),
        "auth_presets": [dict(preset) for preset in AUTH_PRESETS],
        # 实际会被请求的完整地址，前端直接展示，省得靠猜
        "endpoint_preview": build_endpoint(
            config.get("base_url"), config.get("chat_path")
        ),
        "configured": bool(
            str(config.get("base_url") or "")
            and str(config.get("api_key") or "")
            and str(config.get("model") or "")
        ),
    }


def _truncate_detail(text: str) -> str:
    detail = str(text or "").strip()
    return detail[:_MAX_ERROR_DETAIL_CHARS] + ("…" if len(detail) > _MAX_ERROR_DETAIL_CHARS else "")


def _error_detail(error: Exception) -> str:
    return _truncate_detail(str(error))


def probe_model_connection(
    payload: Mapping[str, Any], *, saved_api_key: str = ""
) -> dict[str, Any]:
    """对 OpenAI 兼容接口发起一次最小对话请求，验证链路与密钥可用。

    payload 中的 api_key 优先；留空则回退到 saved_api_key（已保存密钥）。
    """
    config = normalize_config(payload)
    api_key = config["api_key"] or saved_api_key
    model = config["model"]
    timeout = config["timeout_seconds"]

    if not config["base_url"]:
        return {"ok": False, "latency_ms": None, "model": model, "message": "未填写接入地址"}
    if not config["base_url"].startswith(("http://", "https://")):
        return {
            "ok": False,
            "latency_ms": None,
            "model": model,
            "message": "接入地址需以 http:// 或 https:// 开头",
        }
    if not api_key:
        return {
            "ok": False,
            "latency_ms": None,
            "model": model,
            "message": "未填写 API Key",
        }

    effective = {**config, "api_key": api_key}
    url = build_endpoint(config["base_url"], config["chat_path"])
    headers = build_auth_headers(effective)
    streaming = bool(config["stream"])
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "连通性测试，请回复：OK"}],
        "max_tokens": 16,
    }
    # 探活要尽量贴近真实调用：把附加参数和自动思考参数都带上，
    # 否则会出现「测试通过、真跑就报错」这种最坑的情况。
    if not parse_extra_body(config):
        body.update(
            build_thinking_params(model, default_disabled=True)
        )
    body, headers = merge_extra(
        body,
        headers,
        extra_body=config.get("extra_body"),
        extra_headers=config.get("extra_headers"),
    )
    if streaming:
        body["stream"] = True
        headers["Accept"] = "text/event-stream"

    started = time.perf_counter()
    status_code = 0
    detail_text = ""
    reply = ""
    log_llm_request(url, headers, body)
    timer = start_timer()
    try:
        if streaming:
            with httpx.stream(
                "POST", url, headers=headers, json=body, timeout=timeout
            ) as response:
                status_code = response.status_code
                if status_code >= 400:
                    response.read()
                    detail_text = response.text
                else:
                    reply, _ = consume_stream(response.iter_lines())
        else:
            response = httpx.post(url, headers=headers, json=body, timeout=timeout)
            status_code = response.status_code
            detail_text = response.text
            if status_code < 400:
                try:
                    data = response.json()
                    reply = str(
                        data.get("choices", [{}])[0].get("message", {}).get("content") or ""
                    ).strip()
                except (ValueError, IndexError, AttributeError):
                    reply = ""
        log_llm_response(status_code, timer.elapsed_ms, reply or detail_text)
    except httpx.TimeoutException:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "model": model,
            "endpoint": url,
            "message": f"请求超时（上限 {timeout} 秒），请检查地址与网络",
        }
    except httpx.HTTPError as error:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "model": model,
            "endpoint": url,
            "message": f"无法连接接入地址：{_error_detail(error) or '网络错误'}",
        }
    except LLMStreamError as error:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "model": model,
            "endpoint": url,
            "message": f"流式响应无法解析：{_error_detail(error)}",
        }

    latency_ms = round((time.perf_counter() - started) * 1000)
    if status_code in (401, 403):
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "model": model,
            "endpoint": url,
            "message": (
                f"密钥被拒绝（HTTP {status_code}）。除了密钥本身，"
                f"也检查「认证头名称」和「认证前缀」是否与该服务一致"
            ),
        }
    if status_code == 404:
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "model": model,
            "endpoint": url,
            # 把实际请求的完整地址回给前端：路径拼错时这是最直接的线索
            "message": (
                f"接口路径不存在（HTTP 404）。实际请求的是 {url}，"
                "请确认「接入地址」与「对话路径」拼出来的地址是该服务的正确端点"
            ),
        }
    if status_code >= 400:
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "model": model,
            "endpoint": url,
            "message": f"服务返回 HTTP {status_code}：{_truncate_detail(detail_text)}",
        }

    return {
        "ok": True,
        "latency_ms": latency_ms,
        "model": model,
        "endpoint": url,
        "message": reply[:80] or "模型已应答",
    }
