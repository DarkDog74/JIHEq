from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule(
    "FX-01",
    required_fields=(
        "风险评估报告文件数",
        "可定位检验结论文件数",
        "包含问题关键词文件数",
        "报告检查结果",
    ),
)
def audit_fx_01(fields: Mapping[str, Any]) -> dict[str, Any]:
    report_count = fields.get("风险评估报告文件数")
    section_count = fields.get("可定位检验结论文件数")
    problem_count = fields.get("包含问题关键词文件数")
    results = fields.get("报告检查结果")
    counts = (report_count, section_count, problem_count)
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0
        for value in counts
    ):
        raise ValueError("风险评估报告数量统计格式无效")
    if report_count <= 0 or section_count <= 0:
        raise ValueError("风险评估报告或检验结论范围数量无效")
    if not 0 <= problem_count <= section_count <= report_count:
        raise ValueError("风险评估报告数量统计不一致")
    if not isinstance(results, list) or len(results) != report_count:
        raise ValueError("报告检查结果与风险评估报告文件数不一致")

    calculated_sections = 0
    calculated_problems = 0
    for index, item in enumerate(results, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"报告检查结果第{index}项格式无效")
        section_exists = item.get("检验结论范围存在")
        problem_exists = item.get("问题关键词存在")
        if (
            not isinstance(section_exists, bool)
            or not isinstance(problem_exists, bool)
        ):
            raise ValueError(f"报告检查结果第{index}项布尔字段无效")
        if problem_exists and (not section_exists or item.get("匹配章节") != "检验结论"):
            raise ValueError(f"报告检查结果第{index}项关键词位置越界")
        calculated_sections += int(section_exists)
        calculated_problems += int(problem_exists)
    if calculated_sections != section_count or calculated_problems != problem_count:
        raise ValueError("报告检查结果与数量统计不一致")

    passed = problem_count > 0
    return {
        "item_id": "FX-01",
        "passed": passed,
        "checked_report_count": report_count,
        "valid_section_count": section_count,
        "problem_report_count": problem_count,
        "issues": [] if passed else ["检验结论栏内未明确识别到风险问题"],
        "message": (
            f"{problem_count}份报告的检验结论栏内明确包含风险问题"
            if passed
            else "检验结论栏内未明确识别到风险问题"
        ),
    }
