from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from .registry import register_rule


def _parse_iso_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{field}格式无效")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field}格式无效") from error


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(year=value.year + years, day=28)


@register_rule(
    "FX-02",
    required_fields=(
        "声明安全等级",
        "当前风险评估检验结束日期",
        "上一份风险评估检验结束日期",
        "时间间隔天数",
    ),
)
def audit_fx_02(fields: Mapping[str, Any]) -> dict[str, Any]:
    declared_level = fields.get("声明安全等级")
    if (
        not isinstance(declared_level, int)
        or isinstance(declared_level, bool)
        or not 1 <= declared_level <= 5
    ):
        raise ValueError("声明安全等级格式无效")

    if declared_level == 1:
        return {
            "item_id": "FX-02",
            "passed": True,
            "applicable": False,
            "reason_code": "not_applicable",
            "declared_level": declared_level,
            "maximum_years": None,
            "interval_days": None,
            "issues": [],
            "message": "声明安全等级为第1级，本项不适用",
        }

    current_date = _parse_iso_date(
        fields.get("当前风险评估检验结束日期"), "当前风险评估检验结束日期"
    )
    previous_date = _parse_iso_date(
        fields.get("上一份风险评估检验结束日期"), "上一份风险评估检验结束日期"
    )
    interval_days = fields.get("时间间隔天数")
    if not isinstance(interval_days, int) or isinstance(interval_days, bool):
        raise ValueError("时间间隔天数格式无效")
    if interval_days != (current_date - previous_date).days:
        raise ValueError("时间间隔天数与两个检验结束日期不一致")

    maximum_years = 2 if declared_level == 2 else 1
    issues: list[str] = []
    if current_date < previous_date:
        issues.append("当前风险评估检验结束日期早于上一份风险评估报告")
    elif current_date > _add_years(previous_date, maximum_years):
        issues.append(f"两次有效风险评估间隔超过{maximum_years}年")

    passed = not issues
    return {
        "item_id": "FX-02",
        "passed": passed,
        "applicable": True,
        "declared_level": declared_level,
        "maximum_years": maximum_years,
        "interval_days": interval_days,
        "issues": issues,
        "message": (
            f"两次风险评估间隔{interval_days}天，未超过{maximum_years}年"
            if passed
            else "；".join(issues)
        ),
    }
