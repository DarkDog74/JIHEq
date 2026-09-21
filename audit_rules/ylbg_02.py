from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule("YLBG-02", required_fields=("演练科目章节存在", "演练科目"))
def audit_ylbg_02(fields: Mapping[str, Any]) -> dict[str, Any]:
    section_exists = fields.get("演练科目章节存在")
    topics = fields.get("演练科目")
    if not isinstance(section_exists, bool):
        raise ValueError("演练科目章节存在格式无效")
    if not isinstance(topics, list) or not all(
        isinstance(topic, str) and topic for topic in topics
    ):
        raise ValueError("演练科目格式无效")
    passed = section_exists and bool(topics)
    if not section_exists:
        issues = ["演练报告正文缺少演练科目标题"]
    elif not topics:
        issues = ["演练科目标题下没有可识别的演练科目"]
    else:
        issues = []
    return {
        "item_id": "YLBG-02",
        "passed": passed,
        "applicable": True,
        "reason_code": "passed" if passed else "business_rule_failed",
        "topics": topics,
        "topic_count": len(topics),
        "issues": issues,
        "message": f"演练报告包含{len(topics)}个演练科目" if passed else issues[0],
    }
