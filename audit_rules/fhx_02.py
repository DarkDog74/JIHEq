from __future__ import annotations

import re
from collections import Counter
from typing import Any, Mapping

from .registry import register_rule


_ALLOWED_ANSWERS = frozenset({"是", "不适用"})
_MUTUALLY_EXCLUSIVE_ANSWERS = ("不适用", "是", "否")


def _normalize_answer(value: object) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _invalid_reason(answer: str) -> str | None:
    normalized = _normalize_answer(answer)
    if normalized in _ALLOWED_ANSWERS:
        return None
    if not normalized:
        return "答案空白"
    selected = {option for option in _MUTUALLY_EXCLUSIVE_ANSWERS if option in normalized}
    if len(selected) > 1:
        return "同时选择多个互斥答案"
    if normalized == "否":
        return "答案为否"
    return "答案为其他文字"


@register_rule("FHX-02", required_fields=("答案记录",))
def audit_fhx_02(fields: Mapping[str, Any]) -> dict[str, Any]:
    answer_rows = fields.get("答案记录")
    if not isinstance(answer_rows, list) or not answer_rows:
        raise ValueError("答案记录必须是非空数组")

    issues: list[str] = []
    reason_counts: Counter[str] = Counter()
    for index, item in enumerate(answer_rows, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"答案记录第{index}项格式无效")
        row = item.get("题目行号")
        question = str(item.get("检查点") or "").strip()
        answer = str(item.get("答案") or "").strip()
        sequence = str(item.get("序号") or "").strip()
        if not isinstance(row, int) or not question:
            raise ValueError(f"答案记录第{index}项字段不完整")
        reason = _invalid_reason(answer)
        if reason is None:
            continue
        reason_counts[reason] += 1
        shown_answer = answer or "（空白）"
        sequence_text = f"（序号{sequence}）" if sequence else ""
        issues.append(
            f"第{row}题{sequence_text}答案“{shown_answer}”不合法：{reason}；检查点：{question}"
        )

    passed = not issues
    return {
        "item_id": "FHX-02",
        "passed": passed,
        "checked_rows": len(answer_rows),
        "invalid_answer_count": len(issues),
        "invalid_reason_counts": dict(reason_counts),
        "issues": issues,
        "message": (
            f"共检查{len(answer_rows)}题，答案均为“是”或“不适用”"
            if passed
            else f"共检查{len(answer_rows)}题，发现{len(issues)}个非法答案"
        ),
    }
