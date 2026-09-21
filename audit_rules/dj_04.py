from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


_ROWS_FIELD = "修订历史"
_REQUIRED_ROW_FIELDS = ("版本号", "日期", "作者")


def _is_filled(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


@register_rule("DJ-04", required_fields=(_ROWS_FIELD,))
def audit_dj_04(fields: Mapping[str, Any]) -> dict[str, Any]:
    rows = fields.get(_ROWS_FIELD)
    structure_issues: list[str] = []
    violations: list[dict[str, Any]] = []

    if not isinstance(rows, list):
        structure_issues.append("缺少字段或字段类型错误：修订历史")
        rows_to_check: list[Any] = []
    else:
        rows_to_check = rows

    for position, row in enumerate(rows_to_check, start=1):
        if not isinstance(row, Mapping):
            structure_issues.append(f"修订历史第{position}行数据格式错误")
            continue
        row_number = row.get("行号", position)
        values = {field: row.get(field) for field in _REQUIRED_ROW_FIELDS}
        if not any(_is_filled(value) for value in values.values()):
            continue
        missing = [field for field, value in values.items() if not _is_filled(value)]
        if missing:
            violations.append(
                {
                    "行号": row_number,
                    "Markdown行号": row.get("Markdown行号"),
                    "缺少字段": missing,
                }
            )

    issues = list(structure_issues)
    issues.extend(
        f"修订历史第{violation['行号']}行缺少{'、'.join(violation['缺少字段'])}"
        for violation in violations
    )
    passed = not issues
    checked_rows = len(rows_to_check)
    return {
        "item_id": "DJ-04",
        "passed": passed,
        "checked_rows": checked_rows,
        "violations": violations,
        "issues": issues,
        "message": (
            f"修订历史共检查{checked_rows}行，版本号、日期、作者均填写完整"
            if passed
            else "；".join(issues)
        ),
    }
