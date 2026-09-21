from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from .registry import register_rule


_REQUIRED_COLUMNS = ("保密性", "完整性", "可用性")


@register_rule(
    "FX-08",
    required_fields=("目标表标题", "目标表标题行", "列检查", "赋值记录"),
)
def audit_fx_08(fields: Mapping[str, Any]) -> dict[str, Any]:
    title = str(fields.get("目标表标题") or "").strip()
    heading_line = fields.get("目标表标题行")
    checks = fields.get("列检查")
    records = fields.get("赋值记录")
    if not title:
        raise ValueError("目标表标题不能为空")
    if not isinstance(heading_line, int) or isinstance(heading_line, bool) or heading_line <= 0:
        raise ValueError("目标表标题行格式无效")
    if not isinstance(checks, list) or len(checks) != len(_REQUIRED_COLUMNS):
        raise ValueError("列检查格式无效")
    if not isinstance(records, list):
        raise ValueError("赋值记录格式无效")

    presence: dict[str, bool] = {}
    for expected, item in zip(_REQUIRED_COLUMNS, checks, strict=True):
        if not isinstance(item, Mapping) or item.get("列名") != expected:
            raise ValueError("列检查顺序或字段无效")
        exists = item.get("存在")
        if not isinstance(exists, bool):
            raise ValueError(f"“{expected}”列检查结果格式无效")
        presence[expected] = exists

    incomplete: list[Mapping[str, Any]] = []
    problem_counts: Counter[str] = Counter()
    for item in records:
        if not isinstance(item, Mapping):
            raise ValueError("赋值记录内容格式无效")
        missing = item.get("缺少赋值")
        complete = item.get("是否完整赋值")
        if not isinstance(missing, list) or not isinstance(complete, bool):
            raise ValueError("赋值记录判定字段格式无效")
        if complete != (not missing):
            raise ValueError("赋值记录完整性与缺失字段不一致")
        if missing:
            incomplete.append(item)
            problem_counts.update(str(column) for column in missing)

    missing_columns = [column for column in _REQUIRED_COLUMNS if not presence[column]]
    issues: list[str] = []
    if missing_columns:
        issues.append(f"目标表缺少列：{'、'.join(missing_columns)}")
    if not records:
        issues.append("目标表中没有可检查的资产赋值记录")
    for item in incomplete:
        issues.append(
            f"赋值表第{item.get('赋值行号')}行（记录{item.get('记录编号')}）"
            f"缺少：{'、'.join(str(value) for value in item.get('缺少赋值', []))}"
        )

    passed = not issues
    checked_rows = len(records)
    complete_rows = checked_rows - len(incomplete)
    return {
        "item_id": "FX-08",
        "passed": passed,
        "column_presence": presence,
        "checked_rows": checked_rows,
        "complete_row_count": complete_rows,
        "incomplete_row_count": len(incomplete),
        "problem_field_counts": dict(problem_counts),
        "issues": issues,
        "message": (
            f"三列齐全，{checked_rows}条资产记录均已赋值"
            if passed
            else "；".join(issues)
        ),
    }
