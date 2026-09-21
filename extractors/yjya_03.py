from __future__ import annotations

from pathlib import Path
from typing import Any

from .emergency_audit_common import extract_response_levels, read_current_emergency_markdown
from .registry import register_extractor


def extract_yjya_03_data(markdown: str) -> dict[str, Any]:
    return {
        **extract_response_levels(markdown),
        "匹配范围": "应急预案正文，已排除目录；兼容中文、ASCII和Unicode罗马数字",
    }


@register_extractor(
    "YJYA-03", document_name_contains=("应急材料分析",), file_suffixes=(".json",)
)
def extract_yjya_03(source: Path) -> dict[str, Any]:
    path, markdown, _ = read_current_emergency_markdown(Path(source), "应急预案")
    fields = extract_yjya_03_data(markdown)
    fields["应急预案Markdown"] = path.name
    return fields
