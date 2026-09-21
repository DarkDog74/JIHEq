from __future__ import annotations

import ipaddress
import json
import re
from pathlib import Path
from typing import Any, Mapping

from .dj_04 import _is_separator_row, _normalize_label, _split_markdown_row
from .registry import register_extractor


class BEAN02ExtractionError(ValueError):
    """BEAN-02 的备案 IP 或资产清单 IP 无法可靠提取。"""


_IPV4_PART = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
_IPV4 = rf"{_IPV4_PART}(?:\.{_IPV4_PART}){{3}}"
_IPV4_RANGE_RE = re.compile(
    rf"(?P<start>{_IPV4})\s*(?:～|~|至|-)\s*(?:(?P<end_full>{_IPV4})|(?P<end_last>{_IPV4_PART}))"
)
_IP_TOKEN_SPLIT_RE = re.compile(r"[、,，;；\s]+")
_NON_IP_VALUES = frozenset({"", "-", "/", "无", "无公网", "NONE", "N/A"})
_MAX_EXPANDED_RANGE_SIZE = 65_536


def _normalize_ip(value: str) -> str | None:
    candidate = value.strip().strip("，,；;。．（）()[]【】等")
    if candidate.casefold() in {item.casefold() for item in _NON_IP_VALUES}:
        return None
    try:
        return str(ipaddress.ip_address(candidate)).casefold()
    except ValueError:
        try:
            # 备案表常把单个主机写成 IP/前缀长度（如 IPv4 /32、IPv6 /72）。
            # 这里保留其主机地址，不把前缀展开成可能极大的网络范围。
            return str(ipaddress.ip_interface(candidate).ip).casefold()
        except ValueError:
            return None


def _expand_ipv4_range(start: str, end: str) -> list[str]:
    start_value = int(ipaddress.IPv4Address(start))
    end_value = int(ipaddress.IPv4Address(end))
    if end_value < start_value:
        raise BEAN02ExtractionError(f"备案 IP 范围起止顺序错误：{start}-{end}")
    count = end_value - start_value + 1
    if count > _MAX_EXPANDED_RANGE_SIZE:
        raise BEAN02ExtractionError(
            f"备案 IP 范围过大（{count}个）：{start}-{end}"
        )
    return [str(ipaddress.IPv4Address(value)) for value in range(start_value, end_value + 1)]


def expand_filing_ip_text(value: str) -> list[str]:
    """解析备案表中的单个 IP、IPv4 全写范围或末段简写范围。"""
    text = str(value or "").strip()
    if not text or text.casefold() in {item.casefold() for item in _NON_IP_VALUES}:
        return []

    expanded: list[str] = []
    remaining_parts: list[str] = []
    cursor = 0
    for match in _IPV4_RANGE_RE.finditer(text):
        remaining_parts.append(text[cursor : match.start()])
        start = match.group("start")
        end = match.group("end_full")
        if end is None:
            end = ".".join((*start.split(".")[:3], match.group("end_last")))
        expanded.extend(_expand_ipv4_range(start, end))
        cursor = match.end()
    remaining_parts.append(text[cursor:])

    for token in _IP_TOKEN_SPLIT_RE.split(" ".join(remaining_parts)):
        normalized = _normalize_ip(token)
        if normalized is not None:
            expanded.append(normalized)
    return sorted(set(expanded))


def _filing_ip_values(payload: Mapping[str, Any]) -> list[str]:
    target_keys = [
        key
        for key in payload
        if "网络单元使用的主要协议和端口" in _normalize_label(str(key))
    ]
    if not target_keys:
        return []
    if len(target_keys) > 1:
        raise BEAN02ExtractionError("备案信息表中存在多个“主要协议和端口（公网）”字段")
    records = payload[target_keys[0]]
    if not isinstance(records, list):
        raise BEAN02ExtractionError("主要协议和端口（公网）字段必须是数组")
    values: list[str] = []
    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise BEAN02ExtractionError(
                f"主要协议和端口（公网）第{index}条必须是对象"
            )
        ip_keys = [
            key for key in record if "对应IP" in _normalize_label(str(key))
        ]
        if len(ip_keys) > 1:
            raise BEAN02ExtractionError(
                f"主要协议和端口（公网）第{index}条存在多个“对应IP段”字段"
            )
        value = record[ip_keys[0]] if ip_keys else None
        if value is not None and str(value).strip():
            values.append(str(value).strip())
    return values


def _external_asset_ip_values(markdown: str) -> tuple[list[str], bool, list[str]]:
    lines = markdown.splitlines()
    values: list[str] = []
    found_header = False
    issues: list[str] = []
    for line_index, line in enumerate(lines):
        header = _split_markdown_row(line)
        if header is None:
            continue
        labels = [_normalize_label(cell) for cell in header]
        asset_ip_indexes = [
            index
            for index, label in enumerate(labels)
            if label == "资产IP"
        ]
        location_indexes = [
            index
            for index, label in enumerate(labels)
            if "网络位置" in label
        ]
        if not asset_ip_indexes or not location_indexes:
            continue
        if len(asset_ip_indexes) > 1 or len(location_indexes) > 1:
            issues.append("资产清单表头中的“资产IP”列或“网络位置”列重复")
            continue
        found_header = True
        asset_ip_index = asset_ip_indexes[0]
        location_index = location_indexes[0]
        for markdown_line, row_line in enumerate(
            lines[line_index + 1 :], start=line_index + 2
        ):
            row = _split_markdown_row(row_line)
            if row is None:
                break
            if _is_separator_row(row):
                continue
            row_labels = [_normalize_label(cell) for cell in row]
            if "资产IP" in row_labels and any(
                "网络位置" in label for label in row_labels
            ):
                continue
            if max(asset_ip_index, location_index) >= len(row):
                continue
            if _normalize_label(row[location_index]) != "外网":
                continue
            raw_asset_ip = row[asset_ip_index].strip()
            try:
                parsed_ips = expand_filing_ip_text(raw_asset_ip)
            except BEAN02ExtractionError as error:
                issues.append(
                    "资产清单外网资产IP格式错误："
                    + str(error).replace("备案 IP", "资产清单外网资产IP")
                )
                continue
            if not parsed_ips:
                issues.append(
                    f"资产清单第{markdown_line}行网络位置为外网，但资产IP为空或无有效IP"
                )
                continue
            values.extend(parsed_ips)

    # 没有同时包含“资产IP”和“网络位置”的表头时，外网资产集合为空。
    return sorted(set(values)), found_header, issues


def _extract_filing_data(
    filing_path: Path,
) -> tuple[list[str], list[str], list[str]]:
    try:
        payload = json.loads(filing_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        return [], [], [f"备案信息表 JSON 格式错误：{error}"]
    if not isinstance(payload, Mapping):
        return [], [], ["备案信息表 KV JSON 顶层必须是对象"]
    try:
        raw_values = _filing_ip_values(payload)
    except BEAN02ExtractionError as error:
        return [], [], [str(error)]
    ips: list[str] = []
    issues: list[str] = []
    for value in raw_values:
        try:
            ips.extend(expand_filing_ip_text(value))
        except BEAN02ExtractionError as error:
            issues.append(str(error))
    return raw_values, sorted(set(ips)), issues


def extract_bean_02_documents(
    filing_path: Path, asset_inventory_path: Path
) -> dict[str, Any]:
    for path in (filing_path, asset_inventory_path):
        if not path.is_file():
            raise FileNotFoundError(f"输入文件不存在：{path}")
    raw_filing_values, filing_ips, filing_issues = _extract_filing_data(filing_path)
    external_asset_ips, external_header_exists, asset_issues = _external_asset_ip_values(
        asset_inventory_path.read_text(encoding="utf-8-sig")
    )
    return {
        "备案IP原文": raw_filing_values,
        "备案IP": filing_ips,
        "资产清单外网资产IP": external_asset_ips,
        "备案IP条数": len(filing_ips),
        "资产清单外网资产IP条数": len(external_asset_ips),
        "外网资产筛选表头存在": external_header_exists,
        "数据问题": filing_issues + asset_issues,
        "备案IP是否含未列明项": any("等" in value for value in raw_filing_values),
    }


@register_extractor(
    "BEAN-02",
    document_name_contains=("备案信息表",),
    additional_document_name_groups=(("资产清单",),),
    file_suffixes=(".json",),
    additional_document_file_suffix_groups=((".md",),),
)
def extract_bean_02(paths: Path | tuple[Path, ...]) -> dict[str, Any]:
    if isinstance(paths, Path) or len(paths) != 2:
        raise BEAN02ExtractionError("BEAN-02 需要同时输入备案信息表 JSON 和资产清单 Markdown")
    return extract_bean_02_documents(paths[0], paths[1])
