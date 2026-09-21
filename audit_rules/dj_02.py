from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping

from .registry import register_rule


VALUE_FIELDS = (
    "社会影响力赋值",
    "规模和服务范围赋值",
    "提供服务的重要性赋值",
)
WEIGHT_FIELDS = ("权重α", "权重β", "权重γ")
REQUIRED_FIELDS = VALUE_FIELDS + WEIGHT_FIELDS + ("声明k值", "声明安全等级")


def _is_finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and (
            isinstance(value, int)
            or isinstance(value, float) and math.isfinite(value)
        )
    )


def round_one(value: float) -> float:
    return float(
        Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    )


def level_for_k(k_value: float) -> int | None:
    if 1 <= k_value < 1.5:
        return 1
    if 1.5 <= k_value < 2.5:
        return 2
    if 2.5 <= k_value < 4:
        return 3
    if 4 <= k_value < 4.5:
        return 4
    if 4.5 <= k_value <= 5:
        return 5
    return None


@register_rule("DJ-02", required_fields=REQUIRED_FIELDS)
def audit_dj_02(fields: Mapping[str, Any]) -> dict[str, Any]:
    missing = [
        field_name
        for field_name in REQUIRED_FIELDS
        if field_name not in fields or fields[field_name] is None
    ]
    integer_fields = VALUE_FIELDS + ("声明安全等级",)
    invalid = [
        field_name
        for field_name in integer_fields
        if fields.get(field_name) is not None
        and (
            not isinstance(fields[field_name], int)
            or isinstance(fields[field_name], bool)
            or not 1 <= fields[field_name] <= 5
        )
    ]
    invalid.extend(
        field_name
        for field_name in WEIGHT_FIELDS
        if fields.get(field_name) is not None
        and (
            not _is_finite_number(fields[field_name])
            or not 0 <= fields[field_name] <= 1
        )
    )
    if fields.get("声明k值") is not None and (
        not _is_finite_number(fields["声明k值"]) or not 1 <= fields["声明k值"] <= 5
    ):
        invalid.append("声明k值")

    weight_values_valid = not any(
        field_name in missing or field_name in invalid for field_name in WEIGHT_FIELDS
    )
    weight_sum = (
        sum(fields[field_name] for field_name in WEIGHT_FIELDS)
        if weight_values_valid
        else None
    )
    weights_valid = weight_sum is not None and math.isclose(
        weight_sum, 1.0, rel_tol=0.0, abs_tol=1e-6
    )
    fields_valid = not missing and not invalid and weights_valid

    weighted_sum: float | None = None
    raw_k: float | None = None
    calculated_k: float | None = None
    calculated_level: int | None = None
    k_matches = False
    rounded_declared_k: float | None = None
    level_matches = False

    if fields_valid:
        influence = int(fields["社会影响力赋值"])
        scale = int(fields["规模和服务范围赋值"])
        importance = int(fields["提供服务的重要性赋值"])
        weighted_sum = (
            fields["权重α"] * (2**influence)
            + fields["权重β"] * (2**scale)
            + fields["权重γ"] * (2**importance)
        )
        raw_k = math.log2(weighted_sum)
        calculated_k = round_one(raw_k)
        calculated_level = level_for_k(calculated_k)
        # 两侧统一为一位小数比较，允许报告保留更多小数位（3.41 -> 3.4）。
        rounded_declared_k = round_one(fields["声明k值"])
        k_matches = rounded_declared_k == calculated_k
        level_matches = fields["声明安全等级"] == calculated_level

    passed = fields_valid and k_matches and level_matches
    issues: list[str] = []
    if missing:
        issues.append("缺少字段：" + "、".join(missing))
    if invalid:
        issues.append("字段值无效：" + "、".join(invalid))
    if weight_values_valid and not weights_valid:
        issues.append(f"权重之和应为1，实际为{weight_sum}")
    if fields_valid and not k_matches:
        issues.append(
            f"公式计算k值为{calculated_k}，报告声明k值为{fields['声明k值']}"
        )
    if fields_valid and not level_matches:
        issues.append(
            f"公式计算结果为第{calculated_level}级，报告声明为第{fields['声明安全等级']}级"
        )

    return {
        "item_id": "DJ-02",
        "passed": passed,
        "inputs": {field: fields.get(field) for field in REQUIRED_FIELDS},
        "formula": "Round1{Log2[α×2^I + β×2^R + γ×2^V]}",
        "calculation": {
            "weighted_sum": weighted_sum,
            "raw_k": raw_k,
            "calculated_k": calculated_k,
            "calculated_level": calculated_level,
        },
        "declared_k": fields.get("声明k值"),
        "rounded_declared_k": rounded_declared_k,
        "k_matches": k_matches,
        "declared_level": fields.get("声明安全等级"),
        "level_matches": level_matches,
        "weight_sum": weight_sum,
        "weights_valid": weights_valid,
        "missing_fields": missing,
        "invalid_fields": invalid,
        "issues": issues,
        "message": "计算k值及等级均与声明一致" if passed else "k值或等级校验未通过",
    }
