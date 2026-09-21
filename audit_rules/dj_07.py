from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


_REQUIRED_FIELDS = (
    "网络拓扑结构章节存在",
    "网络拓扑图数量",
    "网络边界划分章节存在",
)


@register_rule("DJ-07", required_fields=_REQUIRED_FIELDS)
def audit_dj_07(fields: Mapping[str, Any]) -> dict[str, Any]:
    topology_section_exists = fields.get("网络拓扑结构章节存在") is True
    boundary_section_exists = fields.get("网络边界划分章节存在") is True
    raw_image_count = fields.get("网络拓扑图数量")
    image_count_valid = (
        isinstance(raw_image_count, int)
        and not isinstance(raw_image_count, bool)
        and raw_image_count >= 0
    )
    image_count = raw_image_count if image_count_valid else 0

    issues: list[str] = []
    if not topology_section_exists:
        issues.append("缺少标题中包含“拓扑”的章节")
    elif image_count == 0:
        issues.append("包含“拓扑”的标题章节中缺少IMAGE标识")
    if not boundary_section_exists:
        issues.append("缺少标题中包含“网络边界划分”的章节")
    if not image_count_valid:
        issues.append("网络拓扑图数量字段格式无效")

    passed = not issues
    return {
        "item_id": "DJ-07",
        "passed": passed,
        "inputs": {
            "网络拓扑结构章节存在": topology_section_exists,
            "网络拓扑图数量": raw_image_count,
            "网络边界划分章节存在": boundary_section_exists,
        },
        "issues": issues,
        "message": (
            f"拓扑标题章节内存在{image_count}个IMAGE标识，且存在网络边界划分章节"
            if passed
            else "；".join(issues)
        ),
    }
