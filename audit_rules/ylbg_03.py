from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule("YLBG-03", required_fields=("演练科目章节存在", "勒索关键词证据"))
def audit_ylbg_03(fields: Mapping[str, Any]) -> dict[str, Any]:
    section_exists = fields.get("演练科目章节存在")
    evidence = fields.get("勒索关键词证据")
    if not isinstance(section_exists, bool):
        raise ValueError("演练科目章节存在格式无效")
    if not isinstance(evidence, list) or not all(isinstance(item, Mapping) for item in evidence):
        raise ValueError("勒索关键词证据格式无效")
    passed = section_exists and bool(evidence)
    if not section_exists:
        issues = ["演练报告正文缺少演练科目标题"]
    elif not evidence:
        issues = ["演练科目标题下未出现勒索关键词"]
    else:
        issues = []
    return {
        "item_id": "YLBG-03",
        "passed": passed,
        "applicable": True,
        "reason_code": "passed" if passed else "business_rule_failed",
        "ransomware_evidence_count": len(evidence),
        "issues": issues,
        "message": "演练科目包含勒索关键词" if passed else issues[0],
    }
