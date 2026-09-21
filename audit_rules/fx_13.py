from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


_TARGET_KEYWORD = "保护性安全措施"
_EVIDENCE_FIELD = "保护性安全措施证据"
_VALID_SOURCES = {"Markdown表格类型列", "压平表格类型字段"}


def _audit_measure_type(
    fields: Mapping[str, Any],
    *,
    item_id: str,
    target_keyword: str,
    evidence_field: str,
) -> dict[str, Any]:
    title = str(fields.get("目标表标题") or "").strip()
    title_line = fields.get("目标表标题行")
    location_source = fields.get("定位方式")
    type_column_exists = fields.get("类型列存在")
    keyword_evidence = fields.get(evidence_field)
    if not title:
        raise ValueError("目标表标题不能为空")
    if not isinstance(title_line, int) or isinstance(title_line, bool) or title_line <= 0:
        raise ValueError("目标表标题行格式无效")
    if location_source not in {
        "Markdown标题",
        "独占行表题",
        "表格内独立表题",
        "压平表格行",
    }:
        raise ValueError("目标表定位方式格式无效")
    if not isinstance(type_column_exists, bool):
        raise ValueError("类型列存在格式无效")
    if not isinstance(keyword_evidence, list):
        raise ValueError(f"{evidence_field}格式无效")

    for index, item in enumerate(keyword_evidence, start=1):
        if not isinstance(item, Mapping):
            raise ValueError(f"{evidence_field}第{index}项格式无效")
        keyword = item.get("关键词")
        line_number = item.get("Markdown行号")
        source = item.get("来源")
        if keyword != target_keyword:
            raise ValueError(f"{evidence_field}第{index}项关键词无效")
        if (
            not isinstance(line_number, int)
            or isinstance(line_number, bool)
            or line_number <= 0
        ):
            raise ValueError(f"{evidence_field}第{index}项行号无效")
        if source not in _VALID_SOURCES:
            raise ValueError(f"{evidence_field}第{index}项来源无效")

    matched_count = len(keyword_evidence)
    passed = type_column_exists and matched_count > 0
    if not type_column_exists:
        issues = ["已有安全措施分析表缺少“类型”字段"]
    elif not matched_count:
        issues = [f"已有安全措施分析表“类型”字段未包含“{target_keyword}”"]
    else:
        issues = []

    return {
        "item_id": item_id,
        "passed": passed,
        "column_presence": {"类型": type_column_exists},
        "keyword_presence": {target_keyword: matched_count > 0},
        "keyword_evidence_count": matched_count,
        "matched_measure_count": matched_count,
        "issues": issues,
        "message": (
            f"已有安全措施分析表“类型”字段包含{matched_count}条“{target_keyword}”"
            if passed
            else issues[0]
        ),
    }


@register_rule(
    "FX-13",
    required_fields=(
        "目标表标题",
        "目标表标题行",
        "定位方式",
        "类型列存在",
        _EVIDENCE_FIELD,
    ),
)
def audit_fx_13(fields: Mapping[str, Any]) -> dict[str, Any]:
    return _audit_measure_type(
        fields,
        item_id="FX-13",
        target_keyword=_TARGET_KEYWORD,
        evidence_field=_EVIDENCE_FIELD,
    )
