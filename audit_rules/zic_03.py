from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule("ZIC-03", required_fields=("资产记录行数", "不完整资产记录"))
def audit_zic_03(fields: Mapping[str, Any]) -> dict[str, Any]:
    row_count = fields.get("资产记录行数")
    incomplete_rows = fields.get("不完整资产记录")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count <= 0:
        raise ValueError("资产记录行数格式无效")
    if not isinstance(incomplete_rows, list):
        raise ValueError("不完整资产记录必须是数组")

    issues: list[str] = []
    missing_cell_count = 0
    for index, item in enumerate(incomplete_rows, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"不完整资产记录第{index}项格式无效")
        row = item.get("资产清单行号")
        missing_fields = item.get("缺失字段")
        if not isinstance(row, int) or not isinstance(missing_fields, list) or not missing_fields:
            raise ValueError(f"不完整资产记录第{index}项字段不完整")
        field_names = [str(field).strip() for field in missing_fields]
        if any(not field for field in field_names):
            raise ValueError(f"不完整资产记录第{index}项包含空字段名")
        missing_cell_count += len(field_names)
        issues.append(f"资产清单第{row}行缺少：{'、'.join(field_names)}")

    passed = not issues
    return {
        "item_id": "ZIC-03",
        "passed": passed,
        "checked_rows": row_count,
        "incomplete_row_count": len(incomplete_rows),
        "missing_cell_count": missing_cell_count,
        "issues": issues,
        "message": (
            f"资产记录行数={row_count}，除备注外每条记录各字段均已填写"
            if passed
            else f"资产记录行数={row_count}，发现{len(incomplete_rows)}条不完整记录"
        ),
    }
