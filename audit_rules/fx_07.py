from __future__ import annotations

import ipaddress
import re
import unicodedata
from collections import Counter
from typing import Any, Mapping

from .registry import register_rule


_TARGET_FIELDS = ("IP", "类型", "型号")


def _normalize(value: Any, *, is_ip: bool = False) -> str:
    candidate = unicodedata.normalize("NFKC", str(value or ""))
    candidate = candidate.replace("**", "").replace("__", "").replace("`", "")
    candidate = re.sub(r"\s+", "", candidate) if is_ip else re.sub(r"\s+", " ", candidate)
    candidate = candidate.strip()
    if not is_ip:
        return candidate
    candidate = candidate.casefold()
    try:
        return str(ipaddress.ip_address(candidate)).casefold()
    except ValueError:
        try:
            return str(ipaddress.ip_interface(candidate).ip).casefold()
        except ValueError:
            return candidate


def _validate_records(value: Any, field_name: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError(f"{field_name}必须是资产记录数组")
    records: list[dict[str, Any]] = []
    for index, raw_record in enumerate(value, start=1):
        if not isinstance(raw_record, Mapping):
            raise ValueError(f"{field_name}第{index}条不是对象")
        missing_keys = [field for field in _TARGET_FIELDS if field not in raw_record]
        if missing_keys:
            raise ValueError(
                f"{field_name}第{index}条缺少字段：{'、'.join(missing_keys)}"
            )
        records.append(
            {
                "IP": _normalize(raw_record["IP"], is_ip=True),
                "类型": _normalize(raw_record["类型"]),
                "型号": _normalize(raw_record["型号"]),
                "行号": raw_record.get("行号"),
            }
        )
    return records


def _validated_list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name}必须是数组")
    return value


def _record_tuple(record: Mapping[str, Any]) -> tuple[str, str, str]:
    return tuple(str(record[field]) for field in _TARGET_FIELDS)  # type: ignore[return-value]


def _record_object(record: tuple[str, str, str]) -> dict[str, str]:
    return dict(zip(_TARGET_FIELDS, record))


def _expand(counter: Counter[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    return [record for record in sorted(counter) for _ in range(counter[record])]


def _field_counter(
    records: list[dict[str, Any]], incomplete: list[Any], field: str
) -> Counter[str]:
    values = [str(record[field]) for record in records]
    for raw_record in incomplete:
        if not isinstance(raw_record, Mapping):
            continue
        values.append(
            _normalize(raw_record.get(field), is_ip=field == "IP")
        )
    return Counter(values)


def _incomplete_missing_field(incomplete: list[Any], field: str) -> bool:
    for raw_record in incomplete:
        if not isinstance(raw_record, Mapping):
            return True
        if not _normalize(raw_record.get(field), is_ip=field == "IP"):
            return True
    return False


def _pair_residual_records(
    left: list[tuple[str, str, str]], right: list[tuple[str, str, str]]
) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]], list[tuple[str, str, str]]]:
    """将已取消完全一致项后的记录做确定性配对，仅用于生成字段级证据。"""
    remaining_left = list(left)
    remaining_right = list(right)
    pairs: list[tuple[tuple[str, str, str], tuple[str, str, str]]] = []

    def consume(predicate: Any) -> None:
        left_index = 0
        while left_index < len(remaining_left):
            left_record = remaining_left[left_index]
            right_index = next(
                (
                    index
                    for index, right_record in enumerate(remaining_right)
                    if predicate(left_record, right_record)
                ),
                None,
            )
            if right_index is None:
                left_index += 1
                continue
            pairs.append(
                (remaining_left.pop(left_index), remaining_right.pop(right_index))
            )

    # 先用 IP 对齐，可直接定位同一资产的类型/型号差异。
    consume(lambda left_record, right_record: left_record[0] == right_record[0])
    # IP 本身不同时，优先配对类型和型号都相同的记录。
    consume(
        lambda left_record, right_record: left_record[1:] == right_record[1:]
    )
    consume(lambda left_record, right_record: left_record[1] == right_record[1])
    consume(lambda left_record, right_record: left_record[2] == right_record[2])

    while remaining_left and remaining_right:
        pairs.append((remaining_left.pop(0), remaining_right.pop(0)))

    differences: list[dict[str, Any]] = []
    for left_record, right_record in pairs:
        differing_fields = {
            field: {
                "风险评估报告": left_record[index],
                "定级报告": right_record[index],
            }
            for index, field in enumerate(_TARGET_FIELDS)
            if left_record[index] != right_record[index]
        }
        differences.append(
            {
                "风险评估报告记录": _record_object(left_record),
                "定级报告记录": _record_object(right_record),
                "不一致字段": differing_fields,
            }
        )
    return differences, remaining_left, remaining_right


@register_rule(
    "FX-07",
    required_fields=("风险评估报告资产记录", "定级报告资产记录"),
)
def audit_fx_07(fields: Mapping[str, Any]) -> dict[str, Any]:
    """风险评估报告与定级报告的 (IP, 类型, 型号) 多重集一致时通过。"""
    risk_records = _validate_records(
        fields.get("风险评估报告资产记录"), "风险评估报告资产记录"
    )
    grading_records = _validate_records(
        fields.get("定级报告资产记录"), "定级报告资产记录"
    )
    risk_incomplete = _validated_list(
        fields.get("风险评估报告不完整资产记录"),
        "风险评估报告不完整资产记录",
    )
    grading_incomplete = _validated_list(
        fields.get("定级报告不完整资产记录"),
        "定级报告不完整资产记录",
    )
    risk_missing_columns = [
        str(value)
        for value in _validated_list(
            fields.get("风险评估报告资产表缺失列"),
            "风险评估报告资产表缺失列",
        )
    ]
    grading_missing_columns = [
        str(value)
        for value in _validated_list(
            fields.get("定级报告资产表缺失列"),
            "定级报告资产表缺失列",
        )
    ]

    risk_count = len(risk_records) + len(risk_incomplete)
    grading_count = len(grading_records) + len(grading_incomplete)
    difference = risk_count - grading_count

    risk_counter = Counter(_record_tuple(record) for record in risk_records)
    grading_counter = Counter(_record_tuple(record) for record in grading_records)
    risk_only_tuples = _expand(risk_counter - grading_counter)
    grading_only_tuples = _expand(grading_counter - risk_counter)
    field_differences, unpaired_risk, unpaired_grading = _pair_residual_records(
        risk_only_tuples, grading_only_tuples
    )

    field_mismatch_counts = {
        field: sum(
            field in difference_item["不一致字段"]
            for difference_item in field_differences
        )
        for field in _TARGET_FIELDS
    }
    field_multiset_matches = {
        field: _field_counter(risk_records, risk_incomplete, field)
        == _field_counter(grading_records, grading_incomplete, field)
        and field_mismatch_counts[field] == 0
        and field not in risk_missing_columns
        and field not in grading_missing_columns
        and not _incomplete_missing_field(risk_incomplete, field)
        and not _incomplete_missing_field(grading_incomplete, field)
        for field in _TARGET_FIELDS
    }
    issues: list[str] = []
    if risk_missing_columns:
        issues.append(
            f"风险评估报告的目标资产表缺少同行表头列：{'、'.join(risk_missing_columns)}"
        )
    if grading_missing_columns:
        issues.append(
            f"定级报告的资产附表缺少同行表头列：{'、'.join(grading_missing_columns)}"
        )
    if not risk_missing_columns and risk_count == 0:
        issues.append("风险评估报告的目标资产表没有资产记录")
    if not grading_missing_columns and grading_count == 0:
        issues.append("定级报告的资产附表没有资产记录")
    if risk_incomplete:
        issues.append(
            f"风险评估报告有{len(risk_incomplete)}条资产记录的IP/类型/型号未填完整"
        )
    if grading_incomplete:
        issues.append(
            f"定级报告有{len(grading_incomplete)}条资产记录的IP/类型/型号未填完整"
        )
    if difference:
        issues.append(
            f"风险评估报告资产数量为{risk_count}，定级报告为{grading_count}，"
            f"数量不一致（相差{abs(difference)}）"
        )
    for field in _TARGET_FIELDS:
        if field_mismatch_counts[field]:
            issues.append(
                f"{field_mismatch_counts[field]}组对应资产的{field}不一致"
            )
    if unpaired_risk:
        issues.append(f"风险评估报告多出{len(unpaired_risk)}条资产记录")
    if unpaired_grading:
        issues.append(f"定级报告多出{len(unpaired_grading)}条资产记录")

    structural_failure = bool(
        risk_missing_columns
        or grading_missing_columns
        or risk_incomplete
        or grading_incomplete
        or risk_count == 0
        or grading_count == 0
    )
    multiset_match = risk_counter == grading_counter
    passed = not structural_failure and multiset_match
    return {
        "item_id": "FX-07",
        "passed": passed,
        "risk_asset_count": risk_count,
        "grading_report_asset_count": grading_count,
        "quantity_difference": difference,
        "quantity_match": difference == 0,
        "ip_match": field_multiset_matches["IP"],
        "type_match": field_multiset_matches["类型"],
        "model_match": field_multiset_matches["型号"],
        "field_mismatch_counts": field_mismatch_counts,
        "incomplete_record_counts": {
            "风险评估报告": len(risk_incomplete),
            "定级报告": len(grading_incomplete),
        },
        "missing_columns": {
            "风险评估报告": risk_missing_columns,
            "定级报告": grading_missing_columns,
        },
        "differences": {
            "字段不一致": field_differences,
            "仅风险评估报告存在": [
                _record_object(record) for record in risk_only_tuples
            ],
            "仅定级报告存在": [
                _record_object(record) for record in grading_only_tuples
            ],
        },
        "issues": issues,
        "message": (
            f"风险报告数量={risk_count} | 定级报告数量={grading_count} | "
            + ("资产三元组一致" if passed else "资产三元组不一致")
        ),
    }
