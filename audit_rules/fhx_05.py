from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule("FHX-05", required_fields=("声明安全等级", "风险评估报告"))
def audit_fhx_05(fields: Mapping[str, Any]) -> dict[str, Any]:
    declared_level = fields.get("声明安全等级")
    reports = fields.get("风险评估报告")
    if (
        not isinstance(declared_level, int)
        or isinstance(declared_level, bool)
        or not 1 <= declared_level <= 5
    ):
        raise ValueError("声明安全等级格式无效")
    if not isinstance(reports, list):
        raise ValueError("风险评估报告必须是数组")

    readable_files: list[str] = []
    unreadable_reports: list[dict[str, Any]] = []
    for index, item in enumerate(reports, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"风险评估报告第{index}项格式无效")
        file_name = str(item.get("文件名") or "").strip()
        readable = item.get("可读取")
        reasons = item.get("不可读取原因")
        if not isinstance(readable, bool) or not isinstance(reasons, list) or not all(
            isinstance(reason, str) and reason.strip() for reason in reasons
        ):
            raise ValueError(f"风险评估报告第{index}项可读取信息格式无效")
        if readable:
            if not file_name or reasons:
                raise ValueError(f"风险评估报告第{index}项可读取信息矛盾")
            readable_files.append(file_name)
        else:
            if not reasons:
                raise ValueError(f"风险评估报告第{index}项缺少不可读取原因")
            unreadable_reports.append(dict(item))

    readable_count = len(readable_files)
    report_count = len(reports)
    if declared_level != 1:
        return {
            "item_id": "FHX-05",
            "passed": True,
            "applicable": False,
            "reason_code": "not_applicable",
            "declared_level": declared_level,
            "risk_report_count": readable_count,
            "risk_report_record_count": report_count,
            "readable_risk_report_files": readable_files,
            "unreadable_risk_reports": unreadable_reports,
            "issues": [],
            "message": "非一级不查",
        }

    passed = readable_count > 0
    issues: list[str] = []
    if not passed:
        issues.append(
            "一级系统没有可读取的风险评估报告"
            if report_count
            else "一级系统缺少风险评估报告"
        )
    return {
        "item_id": "FHX-05",
        "passed": passed,
        "applicable": True,
        "declared_level": declared_level,
        "risk_report_count": readable_count,
        "risk_report_record_count": report_count,
        "readable_risk_report_files": readable_files,
        "unreadable_risk_reports": unreadable_reports,
        "issues": issues,
        "message": (
            f"一级系统存在{readable_count}份可读取的风险评估报告"
            if passed
            else issues[0]
        ),
    }
