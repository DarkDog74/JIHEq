from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Mapping

from extractors.dj_09 import ASSET_FIELDS

from .registry import register_rule


def _normalize(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _validate_rows(value: Any, field_name: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name}必须是资产记录数组")
    rows: list[dict[str, str]] = []
    for index, raw_row in enumerate(value, start=1):
        if not isinstance(raw_row, Mapping):
            raise ValueError(f"{field_name}第{index}条不是对象")
        missing = [field for field in ASSET_FIELDS if field not in raw_row]
        if missing:
            raise ValueError(
                f"{field_name}第{index}条缺少字段：{'、'.join(missing)}"
            )
        rows.append({field: _normalize(raw_row[field]) for field in ASSET_FIELDS})
    return rows


def _row_tuple(row: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(row[field] for field in ASSET_FIELDS)


def _row_object(row: tuple[str, ...]) -> dict[str, str]:
    return dict(zip(ASSET_FIELDS, row))


def _expand(counter: Counter[tuple[str, ...]]) -> list[tuple[str, ...]]:
    return [row for row in sorted(counter) for _ in range(counter[row])]


def _pair_residual_rows(
    inventory_rows: list[tuple[str, ...]], report_rows: list[tuple[str, ...]]
) -> tuple[
    list[dict[str, Any]], list[dict[str, str]], list[dict[str, str]]
]:
    mismatches: list[dict[str, Any]] = []
    inventory_missing: list[dict[str, str]] = []
    report_extra: list[dict[str, str]] = []
    inventory_by_ip: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    report_by_ip: dict[str, list[tuple[str, ...]]] = defaultdict(list)
    for row in inventory_rows:
        inventory_by_ip[row[0]].append(row)
    for row in report_rows:
        report_by_ip[row[0]].append(row)

    for asset_ip in sorted(set(inventory_by_ip) | set(report_by_ip)):
        left = list(inventory_by_ip.get(asset_ip, []))
        right = list(report_by_ip.get(asset_ip, []))
        while left and right:
            pair = min(
                (
                    (sum(a[i] != b[i] for i in range(1, len(ASSET_FIELDS))), ai, bi)
                    for ai, a in enumerate(left)
                    for bi, b in enumerate(right)
                ),
                key=lambda value: (value[0], value[1], value[2]),
            )
            _, left_index, right_index = pair
            inventory_row = left.pop(left_index)
            report_row = right.pop(right_index)
            differing_fields = {
                ASSET_FIELDS[index]: {
                    "资产清单": inventory_row[index],
                    "定级报告": report_row[index],
                }
                for index in range(1, len(ASSET_FIELDS))
                if inventory_row[index] != report_row[index]
            }
            mismatches.append(
                {"资产IP": asset_ip, "不一致字段": differing_fields}
            )
        inventory_missing.extend(_row_object(row) for row in left)
        report_extra.extend(_row_object(row) for row in right)
    return mismatches, inventory_missing, report_extra


def _ip_preview(rows: list[Mapping[str, Any]], limit: int = 5) -> str:
    ips = [str(row.get("资产IP", "")) or "<空IP>" for row in rows]
    preview = "、".join(ips[:limit])
    return preview + ("等" if len(ips) > limit else "")


@register_rule(
    "DJ-09", required_fields=("全量资产清单", "定级报告末尾资产")
)
def audit_dj_09(fields: Mapping[str, Any]) -> dict[str, Any]:
    inventory = _validate_rows(fields.get("全量资产清单"), "全量资产清单")
    report = _validate_rows(fields.get("定级报告末尾资产"), "定级报告末尾资产")

    inventory_counter = Counter(_row_tuple(row) for row in inventory)
    report_counter = Counter(_row_tuple(row) for row in report)
    inventory_residual = _expand(inventory_counter - report_counter)
    report_residual = _expand(report_counter - inventory_counter)
    mismatches, missing, extra = _pair_residual_rows(
        inventory_residual, report_residual
    )

    issues: list[str] = []
    if mismatches:
        issues.append(
            f"{len(mismatches)}条资产的字段不一致"
            f"（IP：{_ip_preview(mismatches)}）"
        )
    if missing:
        issues.append(
            f"定级报告缺少{len(missing)}条资产"
            f"（IP：{_ip_preview(missing)}）"
        )
    if extra:
        issues.append(
            f"定级报告多出{len(extra)}条资产"
            f"（IP：{_ip_preview(extra)}）"
        )
    if not inventory and not report:
        issues.append("两份文档均没有资产记录")

    passed = not issues
    count_message = f"资产清单={len(inventory)}条 | 定级报告={len(report)}条"
    return {
        "item_id": "DJ-09",
        "passed": passed,
        "asset_inventory_count": len(inventory),
        "grading_report_count": len(report),
        "mismatch_count": len(mismatches),
        "missing_in_report_count": len(missing),
        "extra_in_report_count": len(extra),
        "differences": {
            "字段不一致": mismatches,
            "定级报告缺少": missing,
            "定级报告多出": extra,
        },
        "issues": issues,
        "message": f"资产一致 | {count_message}" if passed else count_message,
    }
