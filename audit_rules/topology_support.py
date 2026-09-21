"""FX-06 与 DJ-08 共用：拓扑图 VLM 提取 + 确定性硬校验。

模型只负责把拓扑图内容提取成结构化 JSON（节点/IP/类型），
子集比对、名称匹配全部由代码完成。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from core.model_client import (
    ModelCallError,
    ModelNotConfiguredError,
    chat_with_images as _chat_with_images,
    extract_json_object,
)

from .model_support import (
    call_record,
    confidence_too_low,
    extract_node_ips,
    extract_node_names,
    topology_prompt,
)


def build_topology_prompt(image_count: int) -> str:
    """构造拓扑识别提示词；模块级暴露，供失败留档与测试取同一份全文。"""
    return topology_prompt(image_count)


class TopologyRecognitionError(RuntimeError):
    """拓扑识别的模型调用失败：携带提示词、原始异常与已产生的调用留档，供调用方按铁律 3 留档。"""

    def __init__(
        self,
        prompt: str,
        error: Exception,
        records: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(str(error))
        self.prompt = prompt
        self.error = error
        self.records = records


def recognize_topology(image_paths: list[Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """调用视觉模型提取拓扑内容；异常统一抛出，由调用方转 needs_review。

    模型未配置 / 调用失败 / 回复不可解析（均为 ModelNotConfiguredError 或
    ModelCallError）时包装为携带提示词的 TopologyRecognitionError 抛出，
    保证失败路径也能按铁律 3 留档；模型已应答但回复不可解析时，携带含
    原始输出与耗时的完整留档（最常见的失败形态，人工复核需要看到原文）。
    返回 (模型JSON, 调用留档)。
    """
    prompt = build_topology_prompt(len(image_paths))
    records: list[dict[str, Any]] | None = None
    try:
        reply = _chat_with_images(prompt, image_paths, max_tokens=4096)
        records = [call_record(prompt, reply)]
        return extract_json_object(reply.text), records
    except (ModelNotConfiguredError, ModelCallError) as error:
        if records:
            # 模型已应答但回复不可解析：留档同时保留原始输出与解析错误
            records[0]["错误"] = f"{type(error).__name__}: {error}"
        raise TopologyRecognitionError(prompt, error, records) from error


class TopologyNotRecognizedError(RuntimeError):
    """拓扑图一张都定位不到（确定性分支，不调模型）。"""


def match_name_in_assets(name: str, rows: list[Mapping[str, Any]]) -> bool:
    """节点名称与资产行（类型/型号）做归一化互含匹配。"""
    normalized_name = re.sub(r"[\s\u3000]+", "", name)
    if not normalized_name:
        return False
    for row in rows:
        type_text = re.sub(r"[\s\u3000]+", "", str(row.get("资产类型") or ""))
        model_text = re.sub(r"[\s\u3000]+", "", str(row.get("资产型号") or ""))
        for field_text in (type_text, model_text):
            if field_text and (field_text in normalized_name or normalized_name in field_text):
                return True
    return False


__all__ = [
    "TopologyNotRecognizedError",
    "TopologyRecognitionError",
    "build_topology_prompt",
    "match_name_in_assets",
    "recognize_topology",
    "ModelCallError",
    "ModelNotConfiguredError",
    "confidence_too_low",
    "extract_node_ips",
    "extract_node_names",
]
