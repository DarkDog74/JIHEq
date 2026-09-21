from __future__ import annotations

from pathlib import Path
from typing import Any

from .emergency_audit_common import (
    extract_latest_plan_drill_subjects,
    extract_report_drill_subjects,
    read_current_emergency_markdown,
)
from .errors import TargetSectionNotFoundError
from .registry import register_extractor


def extract_yjya_01_data(plan_markdown: str, report_markdown: str) -> dict[str, Any]:
    plan = extract_latest_plan_drill_subjects(plan_markdown)
    if plan is None:
        raise TargetSectionNotFoundError(
            "应急预案的预案管理名录/演练记录列中未找到“开展演练：”内容"
        )
    report = extract_report_drill_subjects(report_markdown)
    if report is None:
        raise TargetSectionNotFoundError(
            "应急演练报告正文中未找到演练科目内容/演练科目及内容/演练项目标题"
        )
    if not report["演练科目"]:
        raise TargetSectionNotFoundError("应急演练报告的演练科目标题下没有科目内容")
    return {
        "预案演练科目": plan["演练科目"],
        "预案演练科目比较值": plan["演练科目比较值"],
        "预案演练记录证据": {
            "原文": plan["演练记录原文"],
            "行号": plan["行号"],
            "记录日期": plan["记录日期"],
        },
        "报告演练科目": report["演练科目"],
        "报告演练科目比较值": report["演练科目比较值"],
        "报告演练科目证据": {
            "章节": report["演练科目章节"],
            "原文": report["演练科目原文"],
            "行号": report["科目内容行号"],
        },
        "比较口径": "忽略空格、大小写和标点，多科目忽略顺序；不删除攻击、事件、演练等业务词",
    }


@register_extractor(
    "YJYA-01", document_name_contains=("应急材料分析",), file_suffixes=(".json",)
)
def extract_yjya_01(source: Path) -> dict[str, Any]:
    manifest = Path(source)
    plan_path, plan, _ = read_current_emergency_markdown(manifest, "应急预案")
    report_path, report, _ = read_current_emergency_markdown(manifest, "应急演练报告")
    fields = extract_yjya_01_data(plan, report)
    fields["应急预案Markdown"] = plan_path.name
    fields["应急演练报告Markdown"] = report_path.name
    return fields
