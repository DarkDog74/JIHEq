from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


def _string_list(value: object, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field}格式无效")
    if not allow_empty and not value:
        raise ValueError(f"{field}不能为空")
    return value


@register_rule(
    "YJYA-01",
    required_fields=(
        "预案演练科目",
        "预案演练科目比较值",
        "报告演练科目",
        "报告演练科目比较值",
    ),
)
def audit_yjya_01(fields: Mapping[str, Any]) -> dict[str, Any]:
    plan_topics = _string_list(fields.get("预案演练科目"), "预案演练科目")
    report_topics = _string_list(fields.get("报告演练科目"), "报告演练科目")
    plan_values = set(
        _string_list(fields.get("预案演练科目比较值"), "预案演练科目比较值")
    )
    report_values = set(
        _string_list(fields.get("报告演练科目比较值"), "报告演练科目比较值")
    )

    plan_only = sorted(plan_values - report_values)
    report_only = sorted(report_values - plan_values)
    passed = not plan_only and not report_only
    issues: list[str] = []
    if plan_only:
        issues.append("仅应急预案存在的科目：" + "、".join(plan_only))
    if report_only:
        issues.append("仅应急演练报告存在的科目：" + "、".join(report_only))
    return {
        "item_id": "YJYA-01",
        "passed": passed,
        "applicable": True,
        "reason_code": "passed" if passed else "business_rule_failed",
        "plan_topics": plan_topics,
        "report_topics": report_topics,
        "plan_only_topics": plan_only,
        "report_only_topics": report_only,
        "issues": issues,
        "message": "应急预案与演练报告的演练科目一致" if passed else "；".join(issues),
    }
