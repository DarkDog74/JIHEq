from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule(
    "FHX-04",
    required_fields=(
        "评测项总数",
        "答案说明非空项数",
        "答案说明为空项",
        "证明材料存在",
    ),
)
def audit_fhx_04(fields: Mapping[str, Any]) -> dict[str, Any]:
    item_count = fields.get("评测项总数")
    nonempty_count = fields.get("答案说明非空项数")
    blank_items = fields.get("答案说明为空项")
    proof_exists = fields.get("证明材料存在")

    if not isinstance(item_count, int) or isinstance(item_count, bool) or item_count <= 0:
        raise ValueError("评测项总数格式无效")
    if (
        not isinstance(nonempty_count, int)
        or isinstance(nonempty_count, bool)
        or nonempty_count < 0
    ):
        raise ValueError("答案说明非空项数格式无效")
    if not isinstance(blank_items, list):
        raise ValueError("答案说明为空项必须是数组")
    if not isinstance(proof_exists, bool):
        raise ValueError("证明材料存在必须是布尔值")
    if nonempty_count + len(blank_items) != item_count:
        raise ValueError("评测项数量统计不一致")

    for index, item in enumerate(blank_items, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"答案说明为空项第{index}项格式无效")

    blank_count = len(blank_items)
    passed = blank_count == 0 or proof_exists
    if not blank_count:
        message = "答案说明均非空，无需检查证明材料"
    elif proof_exists:
        message = "答案说明存在空项，证明材料文件存在"
    else:
        message = "答案说明存在空项，证明材料文件不存在"
    issues = [] if passed else [message]

    return {
        "item_id": "FHX-04",
        "passed": passed,
        "checked_rows": item_count,
        "nonempty_count": nonempty_count,
        "blank_explanation_count": blank_count,
        "proof_required_count": blank_count,
        "proof_exists": proof_exists,
        "issues": issues,
        "message": message,
    }
