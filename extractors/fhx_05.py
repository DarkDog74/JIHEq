from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .dj_02 import _extract_declared_level
from .registry import register_extractor


class FHX05ExtractionError(ValueError):
    """FHX-05 所需的安全等级或风险评估报告信息无法可靠提取。"""


_FAILED_STATUSES = frozenset({"error", "failed", "failure"})


def _risk_report_record(item: Mapping[str, Any], index: int) -> dict[str, Any]:
    source = str(item.get("source") or "").strip()
    size_bytes = item.get("size_bytes")
    status = str(item.get("status") or "").strip()
    error = str(item.get("error") or "").strip()

    reasons: list[str] = []
    if not source:
        reasons.append("缺少原文件名")
    if (
        not isinstance(size_bytes, int)
        or isinstance(size_bytes, bool)
        or size_bytes <= 0
    ):
        reasons.append("文件大小无效")
    if error:
        reasons.append(f"文件分析记录错误：{error}")
    if status.casefold() in _FAILED_STATUSES:
        reasons.append(f"文件状态为{status}")

    return {
        "文件分析序号": index,
        "文件名": source,
        "文件大小": size_bytes,
        "状态": status,
        "可读取": not reasons,
        "不可读取原因": reasons,
    }


def extract_fhx_05_data(
    markdown: str, file_analysis: Mapping[str, Any]
) -> dict[str, Any]:
    declared_level = _extract_declared_level(markdown)
    if declared_level is None:
        raise FHX05ExtractionError(
            "定级报告的定级方法/安全等级计算方法章节中未找到声明安全等级"
        )

    files = file_analysis.get("files")
    if not isinstance(files, list):
        raise FHX05ExtractionError("文件分析 JSON 缺少 files 数组")

    risk_reports: list[dict[str, Any]] = []
    for index, item in enumerate(files, start=1):
        if not isinstance(item, Mapping):
            raise FHX05ExtractionError(f"文件分析 JSON 的 files 第{index}项不是对象")
        if str(item.get("category_name") or "").strip() != "风险评估报告":
            continue
        risk_reports.append(_risk_report_record(item, index))

    return {
        "声明安全等级": declared_level,
        "风险评估报告": risk_reports,
    }


@register_extractor(
    "FHX-05",
    document_name_contains=("定级报告",),
    additional_document_name_groups=(("文件分析",),),
    additional_document_file_suffix_groups=((".json",),),
)
def extract_fhx_05(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(source, tuple) or len(source) != 2:
        raise FHX05ExtractionError("FHX-05 需要定级报告 Markdown 和文件分析 JSON")
    report_path, analysis_path = source
    if not report_path.is_file():
        raise FileNotFoundError(f"输入定级报告不存在：{report_path}")
    if not analysis_path.is_file():
        raise FileNotFoundError(f"输入文件分析 JSON 不存在：{analysis_path}")
    try:
        file_analysis = json.loads(analysis_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise FHX05ExtractionError(f"文件分析 JSON 格式错误：{error}") from error
    if not isinstance(file_analysis, Mapping):
        raise FHX05ExtractionError("文件分析 JSON 顶层必须是对象")
    return extract_fhx_05_data(
        report_path.read_text(encoding="utf-8-sig"), file_analysis
    )
