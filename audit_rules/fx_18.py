from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule("FX-18", required_fields=("业务资产风险分析标题",))
def audit_fx_18(fields: Mapping[str, Any]) -> dict[str, Any]:
    titles = fields.get("业务资产风险分析标题")
    if not isinstance(titles, list):
        raise ValueError("业务资产风险分析标题格式无效")
    for index, item in enumerate(titles, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"业务资产风险分析标题第{index}项格式无效")
        line_number = item.get("Markdown行号")
        title = str(item.get("标题") or "").strip()
        method = str(item.get("识别方式") or "").strip()
        if (
            not isinstance(line_number, int)
            or isinstance(line_number, bool)
            or line_number <= 0
            or not title
            or not method
        ):
            raise ValueError(f"业务资产风险分析标题第{index}项字段不完整")

    passed = bool(titles)
    return {
        "item_id": "FX-18",
        "passed": passed,
        "matched_title_count": len(titles),
        "issues": [] if passed else ["缺少“业务资产风险分析”标题"],
        "message": (
            f"存在业务资产风险分析标题（{len(titles)}处）"
            if passed
            else "缺少“业务资产风险分析”标题"
        ),
    }
