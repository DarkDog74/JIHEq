from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule("YJYA-03", required_fields=("响应级别",))
def audit_yjya_03(fields: Mapping[str, Any]) -> dict[str, Any]:
    levels = fields.get("响应级别")
    if (
        not isinstance(levels, list)
        or not all(
            isinstance(level, int)
            and not isinstance(level, bool)
            and 1 <= level <= 4
            for level in levels
        )
        or len(levels) != len(set(levels))
    ):
        raise ValueError("响应级别格式无效")
    passed = len(levels) >= 2
    issues = [] if passed else [f"正文仅识别到{len(levels)}个不同响应级别，少于2个"]
    return {
        "item_id": "YJYA-03",
        "passed": passed,
        "applicable": True,
        "reason_code": "passed" if passed else "business_rule_failed",
        "response_levels": levels,
        "response_level_count": len(levels),
        "issues": issues,
        "message": (
            "正文包含至少两个不同事件级别的响应描述"
            if passed
            else issues[0]
        ),
    }
