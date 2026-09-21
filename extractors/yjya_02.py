from __future__ import annotations

from pathlib import Path
from typing import Any

from .emergency_audit_common import (
    find_body_markdown_heading,
    read_current_emergency_markdown,
)
from .registry import register_extractor


def extract_yjya_02_data(markdown: str) -> dict[str, Any]:
    evidence = find_body_markdown_heading(markdown, "安全事件定级")
    return {
        "安全事件定级正文标题存在": bool(evidence),
        "安全事件定级标题证据": evidence,
        "匹配范围": "正文Markdown标题，已排除目录，不限制章节号",
    }


@register_extractor(
    "YJYA-02", document_name_contains=("应急材料分析",), file_suffixes=(".json",)
)
def extract_yjya_02(source: Path) -> dict[str, Any]:
    path, markdown, _ = read_current_emergency_markdown(Path(source), "应急预案")
    fields = extract_yjya_02_data(markdown)
    fields["应急预案Markdown"] = path.name
    return fields
