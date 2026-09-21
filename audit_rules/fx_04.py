from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


@register_rule(
    "FX-04",
    required_fields=(
        "风险评估报告文件数",
        "可稽核报告文件数",
        "解析异常报告数",
        "拓扑图文字数量",
        "关联IMAGE标识数量",
        "拓扑图证据",
        "报告检查结果",
    ),
)
def audit_fx_04(fields: Mapping[str, Any]) -> dict[str, Any]:
    report_count = fields.get("风险评估报告文件数")
    usable_report_count = fields.get("可稽核报告文件数")
    parse_error_count = fields.get("解析异常报告数")
    topology_text_count = fields.get("拓扑图文字数量")
    image_count = fields.get("关联IMAGE标识数量")
    evidence = fields.get("拓扑图证据")

    for name, value in (
        ("风险评估报告文件数", report_count),
        ("可稽核报告文件数", usable_report_count),
        ("解析异常报告数", parse_error_count),
        ("拓扑图文字数量", topology_text_count),
        ("关联IMAGE标识数量", image_count),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"{name}格式无效")
    if report_count <= 0 or usable_report_count <= 0:
        raise ValueError("风险评估报告或可稽核报告数量无效")
    if usable_report_count + parse_error_count != report_count:
        raise ValueError("风险评估报告数量统计不一致")
    if not isinstance(evidence, list) or len(evidence) != topology_text_count:
        raise ValueError("拓扑图证据与文字数量不一致")

    calculated_image_lines: set[tuple[str, int]] = set()
    for index, item in enumerate(evidence, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"拓扑图证据第{index}项格式无效")
        line_numbers = item.get("关联IMAGE行号")
        if not isinstance(line_numbers, list) or any(
            not isinstance(line, int) or isinstance(line, bool) or line <= 0
            for line in line_numbers
        ):
            raise ValueError(f"拓扑图证据第{index}项IMAGE行号格式无效")
        source_name = str(item.get("文件") or "")
        calculated_image_lines.update((source_name, line_number) for line_number in line_numbers)
    if len(calculated_image_lines) != image_count:
        raise ValueError("拓扑图证据与IMAGE标识数量不一致")

    report_results = fields.get("报告检查结果")
    if not isinstance(report_results, list) or len(report_results) != report_count:
        raise ValueError("报告检查结果与报告文件数不一致")
    calculated_parse_errors = sum(
        1
        for item in report_results
        if isinstance(item, Mapping) and item.get("解析异常") is True
    )
    if calculated_parse_errors != parse_error_count:
        raise ValueError("报告检查结果与解析异常数不一致")

    passed = image_count > 0
    if passed:
        issues: list[str] = []
        message = f"拓扑图范围内存在{image_count}个IMAGE标识"
    elif topology_text_count == 0:
        issues = ["风险评估报告中未找到“拓扑图”文字"]
        message = issues[0]
    else:
        issues = ["“拓扑图”到下一Markdown大标题范围内没有IMAGE标识"]
        message = issues[0]

    return {
        "item_id": "FX-04",
        "passed": passed,
        "topology_text_count": topology_text_count,
        "topology_image_count": image_count,
        "parse_error_count": parse_error_count,
        "warnings": (
            [f"{parse_error_count}份风险评估报告副本解析异常"]
            if parse_error_count
            else []
        ),
        "issues": issues,
        "message": message,
    }
