from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule("YLP-01", required_fields=("演练计划记录",))
def audit_ylp_01(fields: Mapping[str, Any]) -> dict[str, Any]:
    records = fields.get("演练计划记录")
    if not isinstance(records, list) or not records:
        raise ValueError("演练计划记录必须是非空数组")

    issues: list[str] = []
    checked_count = 0
    unchecked_rows: list[dict[str, Any]] = []
    for index, item in enumerate(records, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"演练计划记录第{index}项格式无效")
        row_number = item.get("计划行号")
        system = str(item.get("参演系统") or "").strip()
        check_count = item.get("勾选数量")
        selected_events = item.get("已勾选事件")
        if (
            not isinstance(row_number, int)
            or isinstance(row_number, bool)
            or row_number <= 0
            or not system
            or not isinstance(check_count, int)
            or isinstance(check_count, bool)
            or check_count < 0
            or not isinstance(selected_events, list)
            or check_count != len(selected_events)
        ):
            raise ValueError(f"演练计划记录第{index}项字段无效")

        if check_count > 0:
            checked_count += 1
            continue
        issue = f"演练计划第{row_number}行（{system}）未勾选任何演练事件"
        issues.append(issue)
        unchecked_rows.append(
            {
                "计划行号": row_number,
                "Markdown行号": item.get("Markdown行号"),
                "归属单位": item.get("归属单位"),
                "参演系统": system,
            }
        )

    total = len(records)
    failed_count = len(unchecked_rows)
    passed = failed_count == 0
    return {
        "item_id": "YLP-01",
        "passed": passed,
        "checked_rows": total,
        "complete_row_count": checked_count,
        "incomplete_row_count": failed_count,
        "未勾选计划行": unchecked_rows,
        "issues": issues,
        "message": (
            f"共检查{total}条演练计划，每条均至少勾选一个演练事件"
            if passed
            else f"共检查{total}条演练计划，发现{failed_count}条未勾选演练事件"
        ),
    }
