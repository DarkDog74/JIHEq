from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule(
    "FX-17",
    required_fields=(
        "系统资产风险分析标题存在",
        "系统资产风险分析标题证据",
        "检查报告数",
        "解析异常报告数",
    ),
)
def audit_fx_17(fields: Mapping[str, Any]) -> dict[str, Any]:
    title_exists = fields.get("系统资产风险分析标题存在")
    evidence = fields.get("系统资产风险分析标题证据")
    checked_report_count = fields.get("检查报告数")
    parse_error_count = fields.get("解析异常报告数")

    if not isinstance(title_exists, bool):
        raise ValueError("系统资产风险分析标题存在格式无效")
    if not isinstance(evidence, list):
        raise ValueError("系统资产风险分析标题证据格式无效")
    if title_exists != bool(evidence):
        raise ValueError("系统资产风险分析标题存在性与证据不一致")
    for name, value in (
        ("检查报告数", checked_report_count),
        ("解析异常报告数", parse_error_count),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name}格式无效")
    if checked_report_count <= 0:
        raise ValueError("检查报告数必须大于0")

    issues = [] if title_exists else ["缺少“系统资产风险分析”标题"]
    title_report_count = len(
        {
            str(item.get("文件"))
            for item in evidence
            if isinstance(item, Mapping) and item.get("文件")
        }
    )
    return {
        "item_id": "FX-17",
        "passed": title_exists,
        "title_exists": title_exists,
        "matched_title_count": len(evidence),
        "title_report_count": title_report_count,
        "checked_report_count": checked_report_count,
        "parse_error_count": parse_error_count,
        "issues": issues,
        "message": (
            f"存在“系统资产风险分析”标题（{len(evidence)}处）"
            if title_exists
            else issues[0]
        ),
    }
