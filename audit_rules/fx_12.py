from __future__ import annotations

from itertools import product
from typing import Any, Mapping

from .registry import register_rule


_MATERIAL_TYPES = ("基线", "漏扫", "渗透测试")


@register_rule(
    "FX-12",
    required_fields=(
        "扫描报告压缩包数",
        "可读取HTML压缩包数",
        "类型存在性",
        "识别材料类型",
        "类型证据",
        "异常压缩包",
        "压缩包检查结果",
    ),
)
def audit_fx_12(fields: Mapping[str, Any]) -> dict[str, Any]:
    archive_count = fields.get("扫描报告压缩包数")
    readable_count = fields.get("可读取HTML压缩包数")
    presence = fields.get("类型存在性")
    matched_types = fields.get("识别材料类型")
    evidence = fields.get("类型证据")
    abnormal = fields.get("异常压缩包")
    results = fields.get("压缩包检查结果")

    for value, name in (
        (archive_count, "扫描报告压缩包数"),
        (readable_count, "可读取HTML压缩包数"),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name}格式无效")
    if readable_count > archive_count:
        raise ValueError("可读取HTML压缩包数不能超过扫描报告压缩包数")
    if not isinstance(presence, Mapping) or any(
        not isinstance(presence.get(material_type), bool)
        for material_type in _MATERIAL_TYPES
    ):
        raise ValueError("类型存在性格式无效")
    if not isinstance(matched_types, list) or any(
        item not in _MATERIAL_TYPES for item in matched_types
    ):
        raise ValueError("识别材料类型格式无效")
    if not isinstance(evidence, list):
        raise ValueError("类型证据必须是数组")
    if not isinstance(abnormal, list):
        raise ValueError("异常压缩包必须是数组")
    if not isinstance(results, list) or len(results) != archive_count:
        raise ValueError("压缩包检查结果与压缩包数不一致")

    expected_types = [
        material_type for material_type in _MATERIAL_TYPES if presence[material_type]
    ]
    if matched_types != expected_types:
        raise ValueError("识别材料类型与类型存在性不一致")
    evidence_types = {
        item.get("材料类型")
        for item in evidence
        if isinstance(item, Mapping)
    }
    if any(material_type not in evidence_types for material_type in matched_types):
        raise ValueError("识别材料类型缺少对应证据")

    missing_types = [material_type for material_type in _MATERIAL_TYPES if not presence[material_type]]
    # 三类各有对应源文件，不能把同一文件的多个关键词当成三份证明。
    known_sources = {
        item.get("源文件") for item in results
        if isinstance(item, Mapping) and isinstance(item.get("源文件"), str)
    }
    sources_by_type = {
        material_type: sorted({
            item["源文件"] for item in evidence
            if isinstance(item, Mapping)
            and item.get("材料类型") == material_type
            and isinstance(item.get("源文件"), str)
            and item["源文件"].strip()
            and item["源文件"] in known_sources
        })
        for material_type in _MATERIAL_TYPES
    }
    separate_files = next((
        dict(zip(_MATERIAL_TYPES, sources))
        for sources in product(*(sources_by_type[t] for t in _MATERIAL_TYPES))
        if len(set(sources)) == len(_MATERIAL_TYPES)
    ), None)
    passed = not missing_types and readable_count >= 3 and separate_files is not None
    issues: list[str] = []
    if not passed:
        reasons = [
            str(item.get("异常原因"))
            for item in abnormal
            if isinstance(item, Mapping) and item.get("异常原因")
        ]
        if archive_count == 0:
            issues.append("未提供扫描报告ZIP证明材料")
        elif readable_count == 0:
            issues.append("扫描报告ZIP中没有可读取的HTML")
        elif not matched_types:
            issues.append("HTML标识字段未识别到基线、漏扫或渗透测试证明材料")
        if missing_types:
            issues.append("缺少" + "、".join(missing_types) + "证明材料，基线、漏扫、渗透测试三类须同时具备")
        elif readable_count < 3 or separate_files is None:
            issues.append("三类证明材料须分别对应不同源文件，当前未能找到三份独立文件证据")
        for reason in dict.fromkeys(reasons):
            if reason not in issues:
                issues.append(reason)

    message = (
        f"已提供{'、'.join(matched_types)}证明材料"
        if passed
        else "；".join(issues)
    )
    return {
        "item_id": "FX-12",
        "passed": passed,
        "scan_archive_count": archive_count,
        "readable_html_archive_count": readable_count,
        "type_presence": dict(presence),
        "matched_types": matched_types,
        "required_types": list(_MATERIAL_TYPES),
        "missing_types": missing_types,
        "material_files": separate_files or {},
        "evidence_count": len(evidence),
        "abnormal_archive_count": len(abnormal),
        "issues": issues,
        "message": message,
    }
