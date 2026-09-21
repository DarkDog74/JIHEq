from __future__ import annotations

import ipaddress
from typing import Any, Mapping

from .registry import register_rule


def _validated_ip_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name}必须是 IP 数组")
    ips: list[str] = []
    for index, raw in enumerate(value, start=1):
        if not isinstance(raw, str):
            raise ValueError(f"{field_name}第{index}项不是字符串")
        try:
            ips.append(str(ipaddress.ip_address(raw.strip())).casefold())
        except ValueError as error:
            raise ValueError(f"{field_name}第{index}项不是有效 IP：{raw}") from error
    return sorted(set(ips))


def _preview(ips: list[str], limit: int = 5) -> str:
    preview = "、".join(ips[:limit])
    return preview + ("等" if len(ips) > limit else "")


@register_rule(
    "BEAN-02",
    required_fields=("备案IP", "资产清单外网资产IP", "数据问题"),
)
def audit_bean_02(fields: Mapping[str, Any]) -> dict[str, Any]:
    filing_ips = _validated_ip_list(fields.get("备案IP"), "备案IP")
    external_asset_ips = _validated_ip_list(
        fields.get("资产清单外网资产IP"), "资产清单外网资产IP"
    )
    raw_issues = fields.get("数据问题")
    if not isinstance(raw_issues, list):
        raise ValueError("数据问题必须是数组")
    data_issues = [str(item).strip() for item in raw_issues if str(item).strip()]

    filing_set = set(filing_ips)
    external_asset_set = set(external_asset_ips)
    missing = sorted(filing_set - external_asset_set)
    extra = sorted(external_asset_set - filing_set)
    issues = list(data_issues)
    if missing:
        issues.append(
            f"资产清单外网资产IP缺少{len(missing)}个备案 IP（{_preview(missing)}）"
        )
    if extra:
        issues.append(
            f"资产清单外网资产IP多出{len(extra)}个 IP（{_preview(extra)}）"
        )
    passed = not issues
    if passed and not filing_ips:
        message = "备案信息表IP和资产清单外网资产IP均为空，双方一致"
    elif passed:
        message = (
            f"备案IP={len(filing_ips)}个 | 资产清单外网资产IP={len(external_asset_ips)}个 | 一一对应"
        )
    else:
        message = (
            f"备案IP={len(filing_ips)}个 | "
            f"资产清单外网资产IP={len(external_asset_ips)}个"
        )
    return {
        "item_id": "BEAN-02",
        "passed": passed,
        "applicable": True,
        "filing_ip_count": len(filing_ips),
        "external_asset_inventory_ip_count": len(external_asset_ips),
        "missing_in_asset_inventory": missing,
        "extra_in_asset_inventory": extra,
        "data_issue_count": len(data_issues),
        "issues": issues,
        "message": message,
    }
