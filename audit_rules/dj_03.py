from __future__ import annotations

import re
from typing import Any, Mapping

from .registry import register_rule


_REQUIRED_FIELDS = ("页眉", "文档名称")
_EMPTY_VALUE_MARKERS = frozenset({"空"})


def _normalize_for_comparison(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = re.sub(r"[\s\u200b\ufeff]+", "", value)
    return None if not normalized or normalized in _EMPTY_VALUE_MARKERS else normalized


@register_rule("DJ-03", required_fields=_REQUIRED_FIELDS)
def audit_dj_03(fields: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {
        field: _normalize_for_comparison(fields.get(field))
        for field in _REQUIRED_FIELDS
    }
    missing = [field for field, value in normalized.items() if value is None]
    consistent = not missing and normalized["页眉"] == normalized["文档名称"]

    issues: list[str] = []
    if missing:
        issues.append("缺少" + "、".join(missing))
    elif not consistent:
        issues.append(
            f"页眉与文档名称不一致：页眉“{fields.get('页眉')}”，"
            f"文档名称“{fields.get('文档名称')}”"
        )

    passed = consistent
    return {
        "item_id": "DJ-03",
        "passed": passed,
        "inputs": {field: fields.get(field) for field in _REQUIRED_FIELDS},
        "normalized": normalized,
        "consistent": consistent,
        "missing_fields": missing,
        "issues": issues,
        "message": (
            "页眉与文档名称一致"
            if passed
            else issues[0]
        ),
    }
