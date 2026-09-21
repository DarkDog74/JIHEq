from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule("YLBG-01", required_fields=("声明安全等级", "应急演练报告存在"))
def audit_ylbg_01(fields: Mapping[str, Any]) -> dict[str, Any]:
    level = fields.get("声明安全等级")
    report_exists = fields.get("应急演练报告存在")
    if (
        not isinstance(level, int)
        or isinstance(level, bool)
        or not 1 <= level <= 5
    ):
        raise ValueError("声明安全等级格式无效")
    if not isinstance(report_exists, bool):
        raise ValueError("应急演练报告存在格式无效")

    if level == 1:
        return {
            "item_id": "YLBG-01",
            "passed": True,
            "applicable": False,
            "reason_code": "not_applicable",
            "declared_level": level,
            "report_exists": report_exists,
            "issues": [],
            "message": "声明安全等级为第1级，本项不适用",
        }

    issues = [] if report_exists else [f"第{level}级系统缺少应急演练报告.md"]
    return {
        "item_id": "YLBG-01",
        "passed": report_exists,
        "applicable": True,
        "reason_code": "passed" if report_exists else "business_rule_failed",
        "declared_level": level,
        "report_exists": report_exists,
        "issues": issues,
        "message": (
            f"第{level}级系统存在应急演练报告.md"
            if report_exists
            else issues[0]
        ),
    }
