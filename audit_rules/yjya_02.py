from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule("YJYA-02", required_fields=("安全事件定级正文标题存在",))
def audit_yjya_02(fields: Mapping[str, Any]) -> dict[str, Any]:
    present = fields.get("安全事件定级正文标题存在")
    if not isinstance(present, bool):
        raise ValueError("安全事件定级正文标题存在格式无效")
    issues = [] if present else ["应急预案正文缺少“安全事件定级”Markdown标题"]
    return {
        "item_id": "YJYA-02",
        "passed": present,
        "applicable": True,
        "reason_code": "passed" if present else "business_rule_failed",
        "heading_evidence": fields.get("安全事件定级标题证据"),
        "issues": issues,
        "message": "应急预案正文包含“安全事件定级”标题" if present else issues[0],
    }
