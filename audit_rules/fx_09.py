from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


_REQUIRED_COLUMNS = ("威胁来源", "威胁种类", "威胁行为")


@register_rule(
    "FX-09",
    required_fields=("目标表标题", "目标表标题行", "表头来源", "表头行", "列检查"),
)
def audit_fx_09(fields: Mapping[str, Any]) -> dict[str, Any]:
    title = str(fields.get("目标表标题") or "").strip()
    heading_line = fields.get("目标表标题行")
    header_line = fields.get("表头行")
    source_type = fields.get("表头来源")
    checks = fields.get("列检查")
    if not title:
        raise ValueError("目标表标题不能为空")
    if not isinstance(heading_line, int) or isinstance(heading_line, bool) or heading_line <= 0:
        raise ValueError("目标表标题行格式无效")
    if (
        not isinstance(header_line, int)
        or isinstance(header_line, bool)
        or header_line < heading_line
    ):
        raise ValueError("表头行格式无效")
    if source_type not in {"Markdown表格", "扁平化表头文本"}:
        raise ValueError("表头来源格式无效")
    if not isinstance(checks, list) or len(checks) != len(_REQUIRED_COLUMNS):
        raise ValueError("列检查格式无效")

    presence: dict[str, bool] = {}
    for expected, item in zip(_REQUIRED_COLUMNS, checks, strict=True):
        if not isinstance(item, Mapping) or item.get("列名") != expected:
            raise ValueError("列检查顺序或字段无效")
        exists = item.get("存在")
        lines = item.get("Markdown行号")
        if not isinstance(exists, bool) or not isinstance(lines, list):
            raise ValueError(f"列“{expected}”检查结果格式无效")
        expected_lines = [header_line] if exists else []
        if lines != expected_lines:
            raise ValueError(f"列“{expected}”证据行号无效")
        presence[expected] = exists

    missing = [column for column in _REQUIRED_COLUMNS if not presence[column]]
    passed = not missing
    return {
        "item_id": "FX-09",
        "passed": passed,
        "column_presence": presence,
        "issues": [] if passed else [f"威胁识别及赋值表缺少列：{'、'.join(missing)}"],
        "message": (
            "威胁识别及赋值表包含威胁来源、威胁种类、威胁行为三列"
            if passed
            else f"威胁识别及赋值表缺少列：{'、'.join(missing)}"
        ),
    }
