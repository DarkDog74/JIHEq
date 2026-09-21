from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule(
    "FX-16",
    required_fields=(
        "安全风险计算结果附录标题",
        "附录标题Markdown行号",
        "有内容的数据行",
    ),
)
def audit_fx_16(fields: Mapping[str, Any]) -> dict[str, Any]:
    title = str(fields.get("安全风险计算结果附录标题") or "").strip()
    title_line = fields.get("附录标题Markdown行号")
    rows = fields.get("有内容的数据行")
    if (
        not title
        or not isinstance(title_line, int)
        or isinstance(title_line, bool)
        or title_line <= 0
    ):
        raise ValueError("安全风险计算结果附录标题格式无效")
    if not isinstance(rows, list) or any(
        not isinstance(row, int)
        or isinstance(row, bool)
        or row < title_line
        for row in rows
    ):
        raise ValueError("有内容的数据行格式无效")
    if len(set(rows)) != len(rows):
        raise ValueError("有内容的数据行存在重复行号")

    passed = bool(rows)
    return {
        "item_id": "FX-16",
        "passed": passed,
        "content_row_count": len(rows),
        "issues": [] if passed else ["安全风险计算结果附录后未找到实际计算结果"],
        "message": (
            f"安全风险计算结果附录含{len(rows)}行实际结果"
            if passed
            else "安全风险计算结果附录后未找到实际计算结果"
        ),
    }
