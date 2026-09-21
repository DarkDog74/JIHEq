from __future__ import annotations

from pathlib import Path
from typing import Any

from .emergency_audit_common import extract_report_drill_subjects, read_current_emergency_markdown
from .registry import register_extractor


def extract_ylbg_02_data(markdown: str) -> dict[str, Any]:
    result = extract_report_drill_subjects(markdown)
    if result is None:
        return {
            "演练科目章节存在": False,
            "演练科目章节": None,
            "演练科目": [],
            "演练科目内容行号": [],
        }
    return {
        "演练科目章节存在": True,
        "演练科目章节": result["演练科目章节"],
        "演练科目": result["演练科目"],
        "演练科目内容行号": result["科目内容行号"],
    }


@register_extractor(
    "YLBG-02", document_name_contains=("应急材料分析",), file_suffixes=(".json",)
)
def extract_ylbg_02(source: Path) -> dict[str, Any]:
    path, markdown, _ = read_current_emergency_markdown(Path(source), "应急演练报告")
    fields = extract_ylbg_02_data(markdown)
    fields["应急演练报告Markdown"] = path.name
    return fields
