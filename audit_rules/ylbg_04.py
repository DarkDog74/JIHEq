from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


_REQUIRED_ELEMENTS = (
    "演练目标",
    "演练时间",
    "参加演练人员名单",
    "演练过程",
    "演练现场照片记录",
)


@register_rule("YLBG-04", required_fields=("要素命中",))
def audit_ylbg_04(fields: Mapping[str, Any]) -> dict[str, Any]:
    presence = fields.get("要素命中")
    if not isinstance(presence, Mapping):
        raise ValueError("要素命中格式无效")
    if set(presence) != set(_REQUIRED_ELEMENTS) or not all(
        isinstance(presence.get(element), bool) for element in _REQUIRED_ELEMENTS
    ):
        raise ValueError("要素命中必须完整包含五个布尔字段")
    missing = [element for element in _REQUIRED_ELEMENTS if not presence[element]]
    passed = not missing
    issues = [] if passed else ["缺少要素：" + "、".join(missing)]
    return {
        "item_id": "YLBG-04",
        "passed": passed,
        "applicable": True,
        "reason_code": "passed" if passed else "business_rule_failed",
        "element_presence": dict(presence),
        "matched_element_count": len(_REQUIRED_ELEMENTS) - len(missing),
        "missing_elements": missing,
        "issues": issues,
        "message": "演练报告包含全部五类必要要素" if passed else issues[0],
    }
