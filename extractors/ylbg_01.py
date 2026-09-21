from __future__ import annotations

from pathlib import Path
from typing import Any

from .dj_02 import DJ02ExtractionError, _extract_declared_level
from .emergency_audit_common import EmergencyAuditExtractionError
from .registry import register_extractor


def _declared_level(grading_markdown: str) -> int:
    try:
        declared_level = _extract_declared_level(grading_markdown)
    except DJ02ExtractionError as error:
        raise EmergencyAuditExtractionError(str(error)) from error
    if declared_level is None:
        raise EmergencyAuditExtractionError(
            "定级报告的定级方法/安全等级计算方法章节中未找到声明安全等级"
        )
    return declared_level


def _fields(declared_level: int, report_path: Path | None) -> dict[str, Any]:
    report_exists = bool(report_path is not None and report_path.is_file())
    return {
        "声明安全等级": declared_level,
        "应急演练报告存在": report_exists,
        "应急演练报告Markdown": report_path.name if report_exists and report_path else None,
        "报告判定口径": "检查系统目录中的当前标准文件应急演练报告.md",
    }


def extract_ylbg_01_data(
    grading_markdown: str, report_path: Path | None = None
) -> dict[str, Any]:
    return _fields(_declared_level(grading_markdown), report_path)


@register_extractor("YLBG-01", document_name_contains=("定级报告",))
def extract_ylbg_01(source: Path) -> dict[str, Any]:
    path = Path(source)
    if not path.is_file():
        raise FileNotFoundError(f"定级报告Markdown不存在：{path}")
    markdown = path.read_text(encoding="utf-8-sig")
    declared_level = _declared_level(markdown)
    report_path = path.parent / "应急演练报告.md" if declared_level >= 2 else None
    return _fields(declared_level, report_path)
