from __future__ import annotations

from pathlib import Path
from typing import Any

from .emergency_audit_common import (
    PHOTO_SECTION_TITLES,
    find_named_section,
    read_current_emergency_markdown,
    section_evidence,
    section_has_image_marker,
)
from .registry import register_extractor


_TEXT_REQUIREMENTS = {
    "演练目标": ("演练目标",),
    "演练时间": ("演练时间、地点、人员", "演练时间"),
    "参加演练人员名单": ("参加演练人员名单", "参演人员"),
    "演练过程": ("演练过程",),
}


def extract_ylbg_04_data(markdown: str) -> dict[str, Any]:
    presence: dict[str, bool] = {}
    evidence: dict[str, Any] = {}
    for field, titles in _TEXT_REQUIREMENTS.items():
        section = find_named_section(markdown, titles)
        presence[field] = section is not None
        evidence[field] = section_evidence(section) if section is not None else None

    photo_section = find_named_section(markdown, PHOTO_SECTION_TITLES)
    has_photo = False
    photo_lines: list[int] = []
    if photo_section is not None:
        has_photo, photo_lines = section_has_image_marker(photo_section)
    presence["演练现场照片记录"] = has_photo
    evidence["演练现场照片记录"] = {
        "章节": section_evidence(photo_section) if photo_section is not None else None,
        "图片标记行号": photo_lines,
    }
    return {"要素命中": presence, "要素证据": evidence}


@register_extractor(
    "YLBG-04", document_name_contains=("应急材料分析",), file_suffixes=(".json",)
)
def extract_ylbg_04(source: Path) -> dict[str, Any]:
    path, markdown, _ = read_current_emergency_markdown(Path(source), "应急演练报告")
    fields = extract_ylbg_04_data(markdown)
    fields["应急演练报告Markdown"] = path.name
    return fields
