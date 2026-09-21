from __future__ import annotations

import re
from typing import Any, Mapping

from .registry import register_rule


_ID_RE = re.compile(r"^\d+(?:-\d+){4}$")


def _validated_ids(value: Any, field_name: str) -> set[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name}必须是数组")
    ids: set[str] = set()
    for index, item in enumerate(value, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"{field_name}第{index}项格式无效")
        item_id = str(item.get("编号") or "").strip()
        if not _ID_RE.fullmatch(item_id):
            raise ValueError(f"{field_name}第{index}项不是五段编号：{item_id or '空'}")
        ids.add(item_id)
    return ids


def _preview(ids: list[str], limit: int = 8) -> str:
    text = "、".join(ids[:limit])
    return text + ("等" if len(ids) > limit else "")


@register_rule(
    "FHX-01",
    required_fields=("评测表全部编号", "需证明材料编号", "证明材料全部编号"),
)
def audit_fhx_01(fields: Mapping[str, Any]) -> dict[str, Any]:
    evaluation_ids = _validated_ids(fields.get("评测表全部编号"), "评测表全部编号")
    proof_required_ids = _validated_ids(
        fields.get("需证明材料编号"), "需证明材料编号"
    )
    proof_ids = _validated_ids(fields.get("证明材料全部编号"), "证明材料全部编号")

    if not proof_required_ids <= evaluation_ids:
        raise ValueError("需证明材料编号必须属于评测表全部编号")

    missing_in_proof = sorted(proof_required_ids - proof_ids)
    # 全部评测项已有答案说明时，本项直接通过，不再检查证明材料内容。
    extra_in_proof = sorted(proof_ids - evaluation_ids) if proof_required_ids else []
    issues: list[str] = []
    if missing_in_proof:
        issues.append(
            f"证明材料缺少{len(missing_in_proof)}个编号：{_preview(missing_in_proof)}"
        )
    if extra_in_proof:
        issues.append(
            f"证明材料多出{len(extra_in_proof)}个编号：{_preview(extra_in_proof)}"
        )

    passed = not issues
    substantive_count = len(evaluation_ids - proof_required_ids)
    count_message = (
        f"评测表全部编号={len(evaluation_ids)} | "
        f"答案说明有内容={substantive_count} | "
        f"需证明材料编号={len(proof_required_ids)} | "
        f"证明材料全部编号={len(proof_ids)}"
    )
    if passed and not proof_required_ids:
        message = f"答案说明均有实质内容，无需证明材料 | {count_message}"
    elif passed:
        message = f"需证明材料编号均已匹配 | {count_message}"
    else:
        message = count_message
    return {
        "item_id": "FHX-01",
        "passed": passed,
        "evaluation_id_count": len(evaluation_ids),
        "substantive_explanation_count": substantive_count,
        "proof_required_id_count": len(proof_required_ids),
        "proof_id_count": len(proof_ids),
        "missing_in_proof_count": len(missing_in_proof),
        "extra_in_proof_count": len(extra_in_proof),
        "differences": {
            "证明材料缺少": missing_in_proof,
            "证明材料多出": extra_in_proof,
        },
        "issues": issues,
        "message": message,
    }
