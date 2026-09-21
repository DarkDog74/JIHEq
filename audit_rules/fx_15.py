from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


_METHODS = ("矩阵法", "相乘法")


@register_rule(
    "FX-15",
    required_fields=(
        "风险计算方法章节标题",
        "风险计算方法章节标题行",
        "命中的风险计算方法",
        "方法检查",
    ),
)
def audit_fx_15(fields: Mapping[str, Any]) -> dict[str, Any]:
    title = str(fields.get("风险计算方法章节标题") or "").strip()
    heading_line = fields.get("风险计算方法章节标题行")
    matched_methods = fields.get("命中的风险计算方法")
    checks = fields.get("方法检查")
    if not title:
        raise ValueError("风险计算方法章节标题不能为空")
    if not isinstance(heading_line, int) or isinstance(heading_line, bool) or heading_line <= 0:
        raise ValueError("风险计算方法章节标题行格式无效")
    if not isinstance(matched_methods, list) or any(
        method not in _METHODS for method in matched_methods
    ):
        raise ValueError("命中的风险计算方法格式无效")
    if len(matched_methods) != len(set(matched_methods)):
        raise ValueError("命中的风险计算方法存在重复值")
    if not isinstance(checks, list) or len(checks) != len(_METHODS):
        raise ValueError("方法检查格式无效")

    presence: dict[str, bool] = {}
    for expected, item in zip(_METHODS, checks, strict=True):
        if not isinstance(item, Mapping) or item.get("方法") != expected:
            raise ValueError("方法检查顺序或字段无效")
        exists = item.get("存在")
        lines = item.get("Markdown行号")
        if not isinstance(exists, bool) or not isinstance(lines, list):
            raise ValueError(f"方法“{expected}”检查结果格式无效")
        if exists != bool(lines) or any(
            not isinstance(line, int)
            or isinstance(line, bool)
            or line <= heading_line
            for line in lines
        ):
            raise ValueError(f"方法“{expected}”证据行号无效")
        presence[expected] = exists

    expected_matches = [method for method in _METHODS if presence[method]]
    if matched_methods != expected_matches:
        raise ValueError("命中的风险计算方法与方法检查不一致")

    passed = bool(matched_methods)
    display = "、".join(matched_methods) if matched_methods else "未命中"
    return {
        "item_id": "FX-15",
        "passed": passed,
        "matched_methods": matched_methods,
        "method_presence": presence,
        "issues": [] if passed else ["风险计算方法章节未包含矩阵法或相乘法"],
        "message": (
            f"风险计算方法章节命中：{display}"
            if passed
            else "风险计算方法章节未包含矩阵法或相乘法"
        ),
    }
