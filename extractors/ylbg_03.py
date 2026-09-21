from __future__ import annotations

from pathlib import Path
from typing import Any

from .emergency_audit_common import (
    REPORT_SUBJECT_TITLES,
    find_named_section,
    read_current_emergency_markdown,
    section_contains_keyword,
    section_evidence,
)
from .registry import register_extractor


def extract_ylbg_03_data(markdown: str) -> dict[str, Any]:
    section = find_named_section(markdown, REPORT_SUBJECT_TITLES)
    if section is None:
        return {
            "演练科目章节存在": False,
            "演练科目章节": None,
            "勒索关键词证据": [],
        }
    evidence = section_contains_keyword(section, "勒索")
    return {
        "演练科目章节存在": True,
        "演练科目章节": section_evidence(section),
        "勒索关键词证据": evidence,
    }


@register_extractor(
    "YLBG-03", document_name_contains=("应急材料分析",), file_suffixes=(".json",)
)
def extract_ylbg_03(source: Path) -> dict[str, Any]:
    path, markdown, _ = read_current_emergency_markdown(Path(source), "应急演练报告")
    fields = extract_ylbg_03_data(markdown)
    fields["应急演练报告Markdown"] = path.name
    return fields
