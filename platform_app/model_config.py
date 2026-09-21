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

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-5v-turbo"
SUGGESTED_MODELS = ("glm-5v-turbo", "glm-5.3-flash")
DEFAULT_TIMEOUT_SECONDS = 120
MIN_TIMEOUT_SECONDS = 3
MAX_TIMEOUT_SECONDS = 300
_MAX_ERROR_DETAIL_CHARS = 240
_MODEL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")

MODEL_CONFIG_PATH = STATE_ROOT / "model_config.json"
CONFIG_SCHEMA_VERSION = "unified-glm-5v-v1"

_FIELDS = ("base_url", "api_key")


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


def normalize_config(payload: Mapping[str, Any]) -> dict[str, Any]:
    """清洗外部传入的配置：去空白、Base URL 去尾部斜杠、超时夹紧。"""
    normalized = {
        field: str(payload.get(field) or "").strip().rstrip("/") for field in _FIELDS
    }
    normalized["base_url"] = normalized["base_url"] or DEFAULT_BASE_URL
    normalized["model"] = _normalize_model(payload.get("model"))
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
        "configured": bool(
            str(config.get("base_url") or "")
            and str(config.get("api_key") or "")
            and str(config.get("model") or "")
        ),
    }


def _error_detail(error: Exception) -> str:
    detail = str(error).strip()
    return detail[:_MAX_ERROR_DETAIL_CHARS] + ("…" if len(detail) > _MAX_ERROR_DETAIL_CHARS else "")


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

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "连通性测试，请回复：OK"}],
        "max_tokens": 16,
    }

    started = time.perf_counter()
    try:
        response = httpx.post(
            f"{config['base_url']}/chat/completions",
            headers=headers,
            json=body,
            timeout=timeout,
        )
    except httpx.TimeoutException:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "model": model,
            "message": f"请求超时（上限 {timeout} 秒），请检查地址与网络",
        }
    except httpx.HTTPError as error:
        return {
            "ok": False,
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "model": model,
            "message": f"无法连接接入地址：{_error_detail(error) or '网络错误'}",
        }

    latency_ms = round((time.perf_counter() - started) * 1000)
    if response.status_code in (401, 403):
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "model": model,
            "message": f"密钥被拒绝（HTTP {response.status_code}），请检查 API Key",
        }
    if response.status_code == 404:
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "model": model,
            "message": "接口路径不存在（HTTP 404），请确认地址是否为 OpenAI 兼容根路径（通常以 /v1 结尾）",
        }
    if response.status_code >= 400:
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "model": model,
            "message": f"服务返回 HTTP {response.status_code}：{_error_detail(response.text)}",
        }

    try:
        data = response.json()
        reply = str(
            data.get("choices", [{}])[0].get("message", {}).get("content") or ""
        ).strip()
    except (ValueError, IndexError, AttributeError):
        reply = ""
    return {
        "ok": True,
        "latency_ms": latency_ms,
        "model": model,
        "message": reply[:80] or "模型已应答",
    }
