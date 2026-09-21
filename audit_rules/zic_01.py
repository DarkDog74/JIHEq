from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule("ZIC-01", required_fields=("资产清单行数", "模糊占位符"))
def audit_zic_01(fields: Mapping[str, Any]) -> dict[str, Any]:
    row_count = fields.get("资产清单行数")
    findings = fields.get("模糊占位符")
    if not isinstance(row_count, int) or isinstance(row_count, bool) or row_count <= 0:
        raise ValueError("资产清单行数格式无效")
    if not isinstance(findings, list):
        raise ValueError("模糊占位符必须是数组")

    issues: list[str] = []
    for index, item in enumerate(findings, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"模糊占位符第{index}项格式无效")
        row = item.get("资产清单行号")
        field = str(item.get("字段") or "").strip()
        placeholders = item.get("命中占位符")
        if not isinstance(row, int) or not field or not isinstance(placeholders, list):
            raise ValueError(f"模糊占位符第{index}项字段不完整")
        issues.append(
            f"资产清单第{row}行“{field}”包含{'、'.join(str(value) for value in placeholders)}"
        )

    passed = not issues
    return {
        "item_id": "ZIC-01",
        "passed": passed,
        "checked_rows": row_count,
        "placeholder_count": len(findings),
        "issues": issues,
        "message": (
            f"资产清单行数={row_count}，未发现模糊占位符"
            if passed
            else f"资产清单行数={row_count}，发现{len(findings)}处模糊占位符"
        ),
    }
