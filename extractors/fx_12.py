from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .registry import register_extractor


class FX12ExtractionError(ValueError):
    """FX-12 的扫描报告汇总无法可靠读取。"""


_MATERIAL_TYPES = ("基线", "漏扫", "渗透测试")


def _nonnegative_int(value: object, field: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FX12ExtractionError(f"{field}格式无效")
    return value


def extract_fx_12_json(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FX12ExtractionError("扫描报告.json顶层必须是对象")

    archive_count = _nonnegative_int(payload.get("扫描报告压缩包数"), "扫描报告压缩包数")
    readable_count = _nonnegative_int(
        payload.get("可读取HTML压缩包数"), "可读取HTML压缩包数"
    )
    if readable_count > archive_count:
        raise FX12ExtractionError("可读取HTML压缩包数不能超过扫描报告压缩包数")

    presence = payload.get("类型存在性")
    if not isinstance(presence, dict) or any(
        not isinstance(presence.get(material_type), bool)
        for material_type in _MATERIAL_TYPES
    ):
        raise FX12ExtractionError("类型存在性格式无效")

    matched_types = payload.get("识别材料类型")
    type_evidence = payload.get("类型证据")
    abnormal_archives = payload.get("异常压缩包")
    archive_results = payload.get("压缩包检查结果")
    if not isinstance(matched_types, list) or any(
        item not in _MATERIAL_TYPES for item in matched_types
    ):
        raise FX12ExtractionError("识别材料类型格式无效")
    if not isinstance(type_evidence, list):
        raise FX12ExtractionError("类型证据必须是数组")
    if not isinstance(abnormal_archives, list):
        raise FX12ExtractionError("异常压缩包必须是数组")
    if not isinstance(archive_results, list) or len(archive_results) != archive_count:
        raise FX12ExtractionError("压缩包检查结果与压缩包数不一致")

    return {
        "扫描报告压缩包数": archive_count,
        "可读取HTML压缩包数": readable_count,
        "类型存在性": {material_type: presence[material_type] for material_type in _MATERIAL_TYPES},
        "识别材料类型": list(matched_types),
        "类型证据": type_evidence,
        "异常压缩包": abnormal_archives,
        "压缩包检查结果": archive_results,
    }


@register_extractor(
    "FX-12",
    document_name_contains=("扫描报告",),
    file_suffixes=(".json",),
)
def extract_fx_12(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(source, Path):
        raise FX12ExtractionError("FX-12只接受一份扫描报告.json")
    if not source.is_file():
        raise FileNotFoundError(f"扫描报告解析结果不存在：{source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FX12ExtractionError(f"扫描报告.json无法读取：{error}") from error
    return extract_fx_12_json(payload)
