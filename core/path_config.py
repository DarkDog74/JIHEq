"""项目路径和外部服务配置。

配置文件为项目根目录下的 ``config/paths.json``。相对路径均相对于项目根目录，
绝对路径可直接填写。环境变量优先级高于配置文件，便于临时切换机器或数据集。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = PROJECT_ROOT / "config" / "paths.json"


def _load_raw() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"路径配置文件无法读取：{CONFIG_PATH}: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError(f"路径配置文件必须是 JSON 对象：{CONFIG_PATH}")
    return payload


def get_value(name: str, default: Any = None) -> Any:
    """读取配置项；对应的大写环境变量优先。"""
    env_name = "AUDIT_" + name.upper()
    value = os.getenv(env_name)
    if value is not None and value != "":
        return value
    return _load_raw().get(name, default)


def project_path(name: str, default: str | Path | None = None) -> Path:
    """读取一个路径配置并解析为绝对路径。"""
    value = get_value(name, default)
    if value is None or str(value).strip() == "":
        return PROJECT_ROOT
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def soffice_candidates() -> list[Path]:
    """返回配置的 LibreOffice 路径候选，未找到时由调用方继续 which 探测。"""
    value = get_value("soffice_candidates", [])
    if isinstance(value, str):
        values = [item.strip() for item in value.split(os.pathsep) if item.strip()]
    elif isinstance(value, list):
        values = [str(item).strip() for item in value if str(item).strip()]
    else:
        values = []
    result: list[Path] = []
    for item in values:
        path = Path(item).expanduser()
        result.append(path if path.is_absolute() else (PROJECT_ROOT / path).resolve())
    return result


def mineru_url() -> str:
    return str(get_value("mineru_url", "http://127.0.0.1:18001/file_parse"))

