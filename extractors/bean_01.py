from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from .registry import register_extractor


class BEAN01ExtractionError(ValueError):
    """BEAN-01 的备案信息表 JSON 结构无法可靠处理。"""


_DATE_REQUIRED_FIELDS = frozenset({"投入运行时间", "填表时间", "定级时间"})
_CHINESE_DATE_RE = re.compile(
    r"^(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日$"
)
_SEPARATED_DATE_RE = re.compile(
    r"^(?P<year>\d{4})\s*(?P<separator>[/.-])\s*"
    r"(?P<month>\d{1,2})\s*(?P=separator)\s*(?P<day>\d{1,2})$"
)

_COMPLETE_MULTIROW_SECTIONS = (
    "主要硬件使用情况",
    "安全系统使用情况",
    "基础软件使用情况",
    "应用软件情况",
    "网络单元使用的主要协议和端口",
    "处理网络数据信息",
)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return len(value) == 0
    return False


def _is_required_key(key: object) -> bool:
    leaf_label = str(key).rsplit("·", 1)[-1]
    return "*" in leaf_label or "＊" in leaf_label


def _clean_key(key: object) -> str:
    text = str(key).replace("*", "").replace("＊", "")
    return re.sub(r"\s+", " ", text).strip()


def _is_complete_multirow_section(key: object) -> bool:
    label = _clean_key(key)
    return any(section in label for section in _COMPLETE_MULTIROW_SECTIONS)


def _is_remote_local_maintenance_field(key: object) -> bool:
    """识别模板中的“维护方式（□远程/□本地）”复选字段。"""
    label = re.sub(r"\s+", "", _clean_key(key))
    if label == "维护方式":
        # 当前备案信息表解析 Schema 会把“□远程/□本地”选项折叠为该字段名。
        return True
    return (
        "维护方式" in label
        and "远程" in label
        and "本地" in label
        and any(marker in label for marker in ("□", "☐"))
    )


def _is_parser_metadata_key(key: object) -> bool:
    return str(key).strip().startswith("_")


def _date_value_status(value: Any) -> tuple[bool, str]:
    if not isinstance(value, str) or not value.strip():
        return False, "日期未填写完整"

    text = value.strip()
    match = _CHINESE_DATE_RE.fullmatch(text) or _SEPARATED_DATE_RE.fullmatch(text)
    if match is None:
        return False, "日期未填写完整"

    try:
        date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError:
        return False, "日期无效"
    return True, ""


def _required_value_status(label: str, value: Any) -> tuple[bool, str]:
    if label in _DATE_REQUIRED_FIELDS:
        return _date_value_status(value)
    if _is_blank(value):
        return False, "未填写"
    return True, ""


def _raw_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _missing_item(path: str, reason: str, value: Any) -> dict[str, str]:
    return {"字段": path, "原始值": _raw_value(value), "原因": reason}


def _record_declares_not_applicable(record: Mapping[str, Any]) -> bool:
    """首个必填识别字段填“无”时，该条明细的空白后续列不适用。"""
    for raw_key, value in record.items():
        if not _is_required_key(raw_key):
            continue
        if isinstance(value, (Mapping, list)):
            return False
        return isinstance(value, str) and value.strip() == "无"
    return False


def extract_bean_01_data(payload: Mapping[str, Any]) -> dict[str, Any]:
    parser_info = payload.get("_解析信息")
    generated_placeholder_sections: set[str] = set()
    if isinstance(parser_info, Mapping):
        compatibility = str(parser_info.get("模板兼容状态") or "").strip()
        if compatibility and compatibility != "compatible":
            reason = str(parser_info.get("说明") or "备案信息表模板需要人工复核").strip()
            return {
                "必填项总数": 0,
                "已填写必填项数": 0,
                "缺失必填项": [],
                "需人工复核": True,
                "人工复核原因": reason,
                "模板类型": str(parser_info.get("模板类型") or "unknown"),
            }
        raw_placeholders = parser_info.get("合成占位区块")
        if isinstance(raw_placeholders, (list, tuple, set)):
            generated_placeholder_sections = {
                _clean_key(item) for item in raw_placeholders if str(item).strip()
            }

    checks: list[bool] = []
    missing: list[dict[str, str]] = []

    public_protocol_gate = ""
    for raw_key, value in payload.items():
        if _clean_key(raw_key) == "是否存在主要的协议和端口（公网）":
            public_protocol_gate = str(value or "").strip()
            break

    def is_public_protocol_section(raw_key: object) -> bool:
        label = _clean_key(raw_key)
        return (
            "主要协议和端口" in label
            and "公网" in label
            and "资产清单公网IP对应" in label
        )

    def add_check(path: str, value: Any, filled: bool, reason: str) -> None:
        checks.append(filled)
        if not filled:
            missing.append(_missing_item(path, reason, value))

    def business_items(record: Mapping[str, Any]) -> list[tuple[object, Any]]:
        return [
            (raw_key, value)
            for raw_key, value in record.items()
            if not _is_parser_metadata_key(raw_key)
        ]

    def check_complete_record(
        record: Mapping[str, Any],
        record_path: str,
        *,
        allow_blank_remote_local_maintenance: bool = False,
    ) -> None:
        for raw_key, child in business_items(record):
            if (
                allow_blank_remote_local_maintenance
                and _is_remote_local_maintenance_field(raw_key)
                and _is_blank(child)
            ):
                continue
            label = _clean_key(raw_key)
            child_path = f"{record_path}.{label}"
            filled, reason = _required_value_status(label, child)
            add_check(child_path, child, filled, reason)

    def walk_complete_multirow(value: Any, path: str) -> None:
        if isinstance(value, Mapping):
            records = [value]
        elif isinstance(value, list):
            records = value
        else:
            raise BEAN01ExtractionError(f"{path}应为明细记录列表")

        if not records:
            add_check(path, records, False, "首条记录未填写")
            return

        first = records[0]
        if not isinstance(first, Mapping):
            raise BEAN01ExtractionError(f"{path}首条记录应为对象")
        first_items = business_items(first)
        if not first_items or all(_is_blank(child) for _, child in first_items):
            add_check(path, first, False, "首条记录未填写")
        else:
            check_complete_record(
                first,
                f"{path}[第1条]",
                allow_blank_remote_local_maintenance=True,
            )

        for index, record in enumerate(records[1:], start=2):
            if not isinstance(record, Mapping):
                if _is_blank(record):
                    continue
                raise BEAN01ExtractionError(f"{path}第{index}条记录应为对象")
            items = business_items(record)
            # 预留的全空行不是一条业务记录；一旦任一业务列有值，
            # 包括 0、False、“无”和“/”，该行全部业务列都必须完整。
            if not items or all(_is_blank(child) for _, child in items):
                continue
            check_complete_record(record, f"{path}[第{index}条]")

    def walk(value: Any, path: str = "", *, is_record: bool = False) -> None:
        if isinstance(value, Mapping):
            record_not_applicable = is_record and _record_declares_not_applicable(value)
            for raw_key, child in value.items():
                # 解析器为了在 JSON 中展示完整 Schema，会给原表没有
                # 业务记录的区块生成一条全空对象。这些对象不是原始填写
                # 数据，不得当成一条“必填但全空”的业务记录。
                if not path and _clean_key(raw_key) in generated_placeholder_sections:
                    continue
                if not path and is_public_protocol_section(raw_key):
                    if public_protocol_gate in {"", "否", "无", "不适用"}:
                        continue
                label = _clean_key(raw_key)
                child_path = f"{path}.{label}" if path else label
                if not path and _is_complete_multirow_section(raw_key):
                    walk_complete_multirow(child, child_path)
                    continue
                if _is_required_key(raw_key):
                    if not (record_not_applicable and _is_blank(child)):
                        filled, reason = _required_value_status(label, child)
                        add_check(child_path, child, filled, reason)
                if isinstance(child, (Mapping, list)):
                    walk(child, child_path)
            return

        if isinstance(value, list):
            for index, item in enumerate(value, start=1):
                item_path = f"{path}[第{index}条]" if path else f"[第{index}条]"
                if isinstance(item, (Mapping, list)):
                    walk(item, item_path, is_record=isinstance(item, Mapping))

    walk(payload)

    if not checks:
        raise BEAN01ExtractionError("未在备案信息表 JSON 中找到任何带星号字段")

    return {
        "必填项总数": len(checks),
        "已填写必填项数": sum(checks),
        "缺失必填项": missing,
        "需人工复核": False,
    }


@register_extractor(
    "BEAN-01",
    document_name_contains=("备案信息表",),
    file_suffixes=(".json",),
)
def extract_bean_01(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(source, Path):
        raise BEAN01ExtractionError("BEAN-01 只接受一份备案信息表 JSON")
    if not source.is_file():
        raise FileNotFoundError(f"输入 JSON 不存在：{source}")
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise BEAN01ExtractionError(f"JSON 格式错误：{error}") from error
    if not isinstance(payload, Mapping):
        raise BEAN01ExtractionError("备案信息表 JSON 顶层必须是对象")
    return extract_bean_01_data(payload)
