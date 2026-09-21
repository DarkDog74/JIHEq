from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


_REQUIRED_MATERIALS = (
    ("符合性评测表", "符合性评测表.md"),
    ("符合性评测报告", "符合性评测报告.md"),
)


@register_rule("DCZ-04", required_fields=("材料存在性", "匹配文件", "缺失材料"))
def audit_dcz_04(fields: Mapping[str, Any]) -> dict[str, Any]:
    presence = fields.get("材料存在性")
    matched_files = fields.get("匹配文件")
    missing_materials = fields.get("缺失材料")
    if not isinstance(presence, Mapping):
        raise ValueError("材料存在性必须是对象")
    if not isinstance(matched_files, Mapping):
        raise ValueError("匹配文件必须是对象")
    if not isinstance(missing_materials, list) or not all(
        isinstance(item, str) and item for item in missing_materials
    ):
        raise ValueError("缺失材料必须是非空字符串数组")

    expected_labels = {material for material, _ in _REQUIRED_MATERIALS}
    if set(presence) != expected_labels or set(matched_files) != expected_labels:
        raise ValueError("材料存在性和匹配文件必须同时包含符合性评测表、符合性评测报告")

    expected_missing: list[str] = []
    for material, filename in _REQUIRED_MATERIALS:
        exists = presence[material]
        matched = matched_files[material]
        if not isinstance(exists, bool):
            raise ValueError(f"{material}存在性必须是布尔值")
        expected_match = filename if exists else None
        if matched != expected_match:
            raise ValueError(f"{material}的匹配文件与存在性矛盾")
        if not exists:
            expected_missing.append(filename)
    if missing_materials != expected_missing:
        raise ValueError("缺失材料与材料存在性不一致")

    required_count = len(_REQUIRED_MATERIALS)
    missing_count = len(expected_missing)
    present_count = required_count - missing_count
    passed = missing_count == 0
    issues = [f"缺少{filename}" for filename in expected_missing]
    missing_text = "、".join(expected_missing)
    return {
        "item_id": "DCZ-04",
        "passed": passed,
        "applicable": True,
        "reason_code": "passed" if passed else "business_rule_failed",
        "required_count": required_count,
        "present_count": present_count,
        "missing_count": missing_count,
        "material_presence": dict(presence),
        "matched_files": dict(matched_files),
        "missing_materials": expected_missing,
        "issues": issues,
        "message": (
            "符合性评测表.md和符合性评测报告.md同时存在"
            if passed
            else f"缺少{missing_count}份材料：{missing_text}"
        ),
    }
