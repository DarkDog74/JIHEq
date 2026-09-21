from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    normalize_heading_title,
    parse_markdown_heading,
    slice_section_by_heading,
    stage_audit_artifacts,
)
from .errors import TargetSectionNotFoundError
from .registry import register_extractor


class DJ10ExtractionError(ValueError):
    """DJ-10 系统概述无法可靠提取。"""


_TARGET_TITLE = "系统概述"


def extract_system_overview(markdown: str) -> str:
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        heading = parse_markdown_heading(line)
        if heading is None:
            continue
        if normalize_heading_title(heading[1]) != _TARGET_TITLE:
            continue
        section = slice_section_by_heading(lines, index, heading[0])
        text = "\n".join(section).strip()
        if text:
            return text
        raise TargetSectionNotFoundError("系统概述章节内容为空")
    raise TargetSectionNotFoundError("未找到“系统概述”章节标题")


def extract_dj_10_data(markdown: str) -> dict[str, Any]:
    return {"系统概述": extract_system_overview(markdown)}


@register_extractor(
    "DJ-10",
    document_name_contains=("定级报告",),
)
def extract_dj_10(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(source, Path):
        raise DJ10ExtractionError("DJ-10 只需要一份定级报告 Markdown")
    report_path = source
    if not report_path.is_file():
        raise FileNotFoundError(f"输入定级报告不存在：{report_path}")

    fields = extract_dj_10_data(report_path.read_text(encoding="utf-8-sig"))
    stage_audit_artifacts("DJ-10", source, fields)
    return fields
