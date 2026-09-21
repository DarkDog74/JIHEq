from __future__ import annotations

import re
from typing import Any, Mapping

from .registry import register_rule


_REQUIRED_FIELDS = ("封面版本", "封面日期", "最新修订版本", "最新修订日期")
_VERSION_RE = re.compile(r"^\d+(?:\.\d+)+$")
_MONTH_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
_REVISION_DATE_RE = re.compile(
    r"^(?P<month>\d{4}-(?:0[1-9]|1[0-2]))(?:-(?:0[1-9]|[12]\d|3[01]))?$"
)


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


@register_rule("DJ-05", required_fields=_REQUIRED_FIELDS)
def audit_dj_05(fields: Mapping[str, Any]) -> dict[str, Any]:
    values = {field: _text(fields.get(field)) for field in _REQUIRED_FIELDS}
    missing = [field for field, value in values.items() if not value]
    invalid: list[str] = []

    if values["封面版本"] and not _VERSION_RE.fullmatch(values["封面版本"]):
        invalid.append("封面版本")
    if values["最新修订版本"] and not _VERSION_RE.fullmatch(values["最新修订版本"]):
        invalid.append("最新修订版本")
    if values["封面日期"] and not _MONTH_RE.fullmatch(values["封面日期"]):
        invalid.append("封面日期")
    latest_date_match = None
    if values["最新修订日期"]:
        latest_date_match = _REVISION_DATE_RE.fullmatch(values["最新修订日期"])
        if latest_date_match is None:
            invalid.append("最新修订日期")

    comparable = not missing and not invalid
    version_matches = False
    month_matches = False
    if comparable and latest_date_match is not None:
        version_matches = values["封面版本"] == values["最新修订版本"]
        month_matches = values["封面日期"] == latest_date_match.group("month")

    issues: list[str] = []
    if missing:
        issues.append("缺少字段：" + "、".join(missing))
    if invalid:
        issues.append("字段格式无效：" + "、".join(invalid))
    if comparable and not version_matches:
        issues.append(
            f"封面版本为{values['封面版本']}，最新修订版本为{values['最新修订版本']}"
        )
    if comparable and not month_matches:
        issues.append(
            f"封面日期为{values['封面日期']}，最新修订日期为{values['最新修订日期']}"
        )

    passed = comparable and version_matches and month_matches
    return {
        "item_id": "DJ-05",
        "passed": passed,
        "inputs": values,
        "version_matches": version_matches,
        "month_matches": month_matches,
        "issues": issues,
        "message": (
            f"封面版本{values['封面版本']}、日期{values['封面日期']}与最新修订记录一致"
            if passed
            else "；".join(issues)
        ),
    }
