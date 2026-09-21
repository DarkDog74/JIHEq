from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


_REQUIRED_MATERIALS = {
    "应急预案": "应急预案.md",
    "演练方案": "演练方案.md",
    "应急演练报告": "应急演练报告.md",
}


@register_rule(
    "DCZ-02",
    required_fields=("材料存在性", "匹配文件", "缺失材料"),
)
def audit_dcz_02(fields: Mapping[str, Any]) -> dict[str, Any]:
    presence = fields.get("材料存在性")
    matched_files = fields.get("匹配文件")
    missing = fields.get("缺失材料")
    material_names = tuple(_REQUIRED_MATERIALS)

    if not isinstance(presence, Mapping) or set(presence) != set(material_names):
        raise ValueError("材料存在性必须完整包含三类材料")
    if any(not isinstance(presence[name], bool) for name in material_names):
        raise ValueError("材料存在性必须为布尔值")
    if not isinstance(matched_files, Mapping) or set(matched_files) != set(material_names):
        raise ValueError("匹配文件必须完整包含三类材料")
    for name, file_name in _REQUIRED_MATERIALS.items():
        files = matched_files[name]
        if not isinstance(files, list) or any(
            not isinstance(value, str) or not value for value in files
        ):
            raise ValueError(f"{name}的匹配文件格式无效")
        expected_files = [file_name] if presence[name] else []
        if files != expected_files:
            raise ValueError(f"{name}的匹配文件与存在性不一致")
    expected_missing = [name for name in material_names if not presence[name]]
    if not isinstance(missing, list) or missing != expected_missing:
        raise ValueError("缺失材料与材料存在性不一致")

    passed = not missing
    issues = [] if passed else ["缺少材料：" + "、".join(missing)]
    return {
        "item_id": "DCZ-02",
        "passed": passed,
        "applicable": True,
        "reason_code": "passed" if passed else "business_rule_failed",
        "type_presence": dict(presence),
        "matched_files": {name: list(matched_files[name]) for name in material_names},
        "matched_types": [name for name in material_names if presence[name]],
        "missing_types": list(missing),
        "required_count": len(material_names),
        "missing_count": len(missing),
        "issues": issues,
        "message": "应急预案、演练方案、应急演练报告三份齐全" if passed else issues[0],
    }
