from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule("ZIC-02", required_fields=("资产类型行数", "不在资产类型划分标准表的类型"))
def audit_zic_02(fields: Mapping[str, Any]) -> dict[str, Any]:
    row_count = fields.get("资产类型行数")
    invalid_types = fields.get("不在资产类型划分标准表的类型")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count <= 0:
        raise ValueError("资产类型行数格式无效")
    if not isinstance(invalid_types, list):
        raise ValueError("不在资产类型划分标准表的类型必须是数组")

    issues: list[str] = []
    for index, item in enumerate(invalid_types, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"非标准资产类型第{index}项格式无效")
        row = item.get("资产清单行号")
        asset_type = str(item.get("资产类型") or "").strip()
        if not isinstance(row, int):
            raise ValueError(f"非标准资产类型第{index}项缺少资产清单行号")
        shown_type = asset_type or "（空）"
        issues.append(f"资产清单第{row}行资产类型“{shown_type}”不在资产类型划分标准表中")

    passed = not issues
    return {
        "item_id": "ZIC-02",
        "passed": passed,
        "checked_rows": row_count,
        "invalid_asset_type_count": len(invalid_types),
        "issues": issues,
        "message": (
            f"资产类型行数={row_count}，全部类型均在资产类型划分标准表中"
            if passed
            else f"资产类型行数={row_count}，发现{len(invalid_types)}个非标准资产类型"
        ),
    }
