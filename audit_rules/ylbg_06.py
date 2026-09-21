from __future__ import annotations

import re
from typing import Any, Mapping

from .registry import register_rule


def _parse_month(value: object, field: str) -> tuple[int, int, str]:
    if not isinstance(value, str):
        raise ValueError(f"{field}格式无效")
    if re.fullmatch(r"20\d{2}-(?:0[1-9]|1[0-2])", value) is None:
        raise ValueError(f"{field}格式无效，应为 YYYY-MM")
    year, month = map(int, value.split("-"))
    return year, month, value


@register_rule(
    "YLBG-06",
    required_fields=("最新修订月份", "最新演练月份"),
)
def audit_ylbg_06(fields: Mapping[str, Any]) -> dict[str, Any]:
    revision_year, revision_month_number, revision_month = _parse_month(
        fields.get("最新修订月份"), "最新修订月份"
    )
    drill_year, drill_month_number, drill_month = _parse_month(
        fields.get("最新演练月份"), "最新演练月份"
    )
    delta_months = (
        (drill_year - revision_year) * 12
        + drill_month_number
        - revision_month_number
    )

    if 0 <= delta_months <= 1:
        passed = True
        reason_code = "passed"
        issues: list[str] = []
        message = f"演练月份距最新修订月份{delta_months}个月，满足当月或次月开展"
    elif delta_months < 0:
        passed = False
        reason_code = "business_rule_failed"
        issues = ["演练月份早于最新修订月份"]
        message = issues[0]
    else:
        passed = False
        reason_code = "business_rule_failed"
        issues = [f"演练月份距最新修订月份{delta_months}个月，超过次月范围"]
        message = issues[0]

    return {
        "item_id": "YLBG-06",
        "passed": passed,
        "applicable": True,
        "reason_code": reason_code,
        "revision_month": revision_month,
        "drill_month": drill_month,
        "delta_months": delta_months,
        "calculation_unit": "month",
        "pairing_basis": fields.get("配对依据"),
        "issues": issues,
        "message": message,
    }
