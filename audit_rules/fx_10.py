from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule(
    "FX-10",
    required_fields=("目标表标题", "目标表标题行", "检查规则", "赋值记录"),
)
def audit_fx_10(fields: Mapping[str, Any]) -> dict[str, Any]:
    title = str(fields.get("目标表标题") or "").strip()
    heading_line = fields.get("目标表标题行")
    check_rule = str(fields.get("检查规则") or "").strip()
    rows = fields.get("赋值记录")
    if not title:
        raise ValueError("目标表标题不能为空")
    if (
        not isinstance(heading_line, int)
        or isinstance(heading_line, bool)
        or heading_line <= 0
    ):
        raise ValueError("目标表标题行格式无效")
    if not check_rule:
        raise ValueError("检查规则不能为空")
    if not isinstance(rows, list):
        raise ValueError("赋值记录必须是数组")

    problem_rows: list[dict[str, Any]] = []
    for expected_number, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise ValueError(f"赋值记录第{expected_number}项格式无效")
        if row.get("赋值行号") != expected_number:
            raise ValueError(f"赋值记录第{expected_number}项行号无效")
        markdown_line = row.get("Markdown行号")
        numbers = row.get("数字")
        has_number = row.get("是否有数字")
        if (
            not isinstance(markdown_line, int)
            or isinstance(markdown_line, bool)
            or markdown_line <= 0
        ):
            raise ValueError(f"赋值记录第{expected_number}项Markdown行号无效")
        if not isinstance(numbers, list) or any(
            not isinstance(value, str) for value in numbers
        ):
            raise ValueError(f"赋值记录第{expected_number}项数字格式无效")
        if not isinstance(has_number, bool) or has_number != bool(numbers):
            raise ValueError(f"赋值记录第{expected_number}项数字证据不一致")
        if not has_number:
            problem_rows.append(
                {
                    "赋值行号": expected_number,
                    "Markdown行号": markdown_line,
                    "原文摘要": str(row.get("原文摘要") or ""),
                }
            )

    checked_rows = len(rows)
    complete_rows = checked_rows - len(problem_rows)
    issues: list[str] = []
    if checked_rows == 0:
        issues.append("附录 B 威胁识别及赋值表没有可检查的记录行")
    issues.extend(
        f"赋值行{row['赋值行号']}（Markdown第{row['Markdown行号']}行）没有发现赋值数字"
        for row in problem_rows
    )
    passed = checked_rows > 0 and not problem_rows
    return {
        "item_id": "FX-10",
        "passed": passed,
        "checked_rows": checked_rows,
        "complete_row_count": complete_rows,
        "incomplete_row_count": len(problem_rows),
        "invalid_value_count": 0,
        "problem_field_counts": (
            {"未找到赋值数字": len(problem_rows)} if problem_rows else {}
        ),
        "problem_rows": problem_rows,
        "issues": issues,
        "message": (
            f"赋值记录={checked_rows}行 | 有数字={complete_rows}行 | "
            f"无数字={len(problem_rows)}行"
        ),
    }
