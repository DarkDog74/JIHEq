from __future__ import annotations

from typing import Any, Mapping

from .fx_13 import _audit_measure_type
from .registry import register_rule


_TARGET_KEYWORD = "预防性安全措施"
_EVIDENCE_FIELD = "预防性安全措施证据"


@register_rule(
    "FX-14",
    required_fields=(
        "目标表标题",
        "目标表标题行",
        "定位方式",
        "类型列存在",
        _EVIDENCE_FIELD,
    ),
)
def audit_fx_14(fields: Mapping[str, Any]) -> dict[str, Any]:
    return _audit_measure_type(
        fields,
        item_id="FX-14",
        target_keyword=_TARGET_KEYWORD,
        evidence_field=_EVIDENCE_FIELD,
    )
