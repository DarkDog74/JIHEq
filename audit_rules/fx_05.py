from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


def _validated_count(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name}必须是非负整数")
    return value


@register_rule(
    "FX-05", required_fields=("风险评估报告资产数量", "资产清单记录数")
)
def audit_fx_05(fields: Mapping[str, Any]) -> dict[str, Any]:
    risk_count = _validated_count(
        fields.get("风险评估报告资产数量"), "风险评估报告资产数量"
    )
    asset_count = _validated_count(fields.get("资产清单记录数"), "资产清单记录数")
    quantity_source = str(fields.get("风险评估报告数量来源") or "数量列")
    source_label = "IP行统计" if quantity_source == "IP行兜底" else "“数量”列合计"
    difference = risk_count - asset_count
    passed = difference == 0
    issues = (
        []
        if passed
        else [
            f"风险评估报告{source_label}为{risk_count}，"
            f"资产清单共有{asset_count}条记录，数量不一致（相差{abs(difference)}）"
        ]
    )
    return {
        "item_id": "FX-05",
        "passed": passed,
        "risk_asset_count": risk_count,
        "asset_inventory_count": asset_count,
        "quantity_source": quantity_source,
        "quantity_difference": difference,
        "issues": issues,
        "message": (
            f"风险报告数量={risk_count} | 资产清单记录数={asset_count}"
            + (" | 数量一致" if passed else " | 数量不一致")
        ),
    }
