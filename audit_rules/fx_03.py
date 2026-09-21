from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule(
    "FX-03",
    required_fields=(
        "风险评估报告文件数",
        "完整标题报告数",
        "最佳匹配报告",
        "已找到标题数",
        "缺失标题",
        "报告检查结果",
    ),
)
def audit_fx_03(fields: Mapping[str, Any]) -> dict[str, Any]:
    report_count = fields.get("风险评估报告文件数")
    complete_count = fields.get("完整标题报告数")
    matched_count = fields.get("已找到标题数")
    missing_titles = fields.get("缺失标题")
    best_report = fields.get("最佳匹配报告")
    results = fields.get("报告检查结果")

    if not isinstance(report_count, int) or isinstance(report_count, bool) or report_count < 1:
        raise ValueError("风险评估报告文件数格式无效")
    if (
        not isinstance(complete_count, int)
        or isinstance(complete_count, bool)
        or not 0 <= complete_count <= report_count
    ):
        raise ValueError("完整标题报告数格式无效")
    if (
        not isinstance(matched_count, int)
        or isinstance(matched_count, bool)
        or not 0 <= matched_count <= 7
    ):
        raise ValueError("已找到标题数格式无效")
    if not isinstance(missing_titles, list) or any(
        not isinstance(title, str) or not title for title in missing_titles
    ):
        raise ValueError("缺失标题格式无效")
    if matched_count + len(missing_titles) != 7:
        raise ValueError("已找到标题数与缺失标题数不一致")
    if not isinstance(best_report, str) or not best_report:
        raise ValueError("最佳匹配报告格式无效")
    if not isinstance(results, list) or len(results) != report_count:
        raise ValueError("报告检查结果与风险评估报告文件数不一致")

    passed = complete_count > 0
    issues = [] if passed else [f"缺少标题：{'\u3001'.join(missing_titles)}"]
    return {
        "item_id": "FX-03",
        "passed": passed,
        "checked_report_count": report_count,
        "complete_report_count": complete_count,
        "best_report": best_report,
        "matched_title_count": matched_count,
        "missing_title_count": len(missing_titles),
        "missing_titles": missing_titles,
        "issues": issues,
        "message": (
            f"已在{best_report}找到全部7个必要标题"
            if passed
            else f"{best_report}只找到{matched_count}/7个必要标题"
        ),
    }
