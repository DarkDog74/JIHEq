#!/usr/bin/env python3
"""将“网络单元备案信息表”Word/Markdown解析为KV JSON。"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from word_header import convert_doc_to_docx_bytes


SCALAR_LABELS = {
    "网络/系统单元名称",
    "投入运行时间",
    "填表人",
    "填表时间",
    "网络/系统单元类型",
    "联系人信息",
    "姓名",
    "职务/职称",
    "办公电话",
    "电子邮件",
    "移动电话",
    "服务范围",
    "服务对象",
    "专家评审",
    "所要申报的安全等级",
    "定级时间",
    "IPV6改造情况",
    "IPV6改造情况说明",
    "是否拥有公网IP地址",
    "是否存在主要的协议和端口（公网）",
    "网络用户信息存储处理情况",
    "接入带宽",
    "信息服务系统是否使用域名",
    "是否使用.cn域名",
    "网络单元所用域名",
}


def clean_cell(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = value.replace("**", "").replace("\\*", "*")
    value = value.replace("\\|", "|")
    return re.sub(r"\s+", " ", value).strip()


def normalized(value: str) -> str:
    return re.sub(r"[\s*]", "", clean_cell(value)).upper()


def parse_table_rows(markdown: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in markdown.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [clean_cell(cell) for cell in line.strip().strip("|").split("|")]
        if cells and all(not cell or re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    if not rows:
        raise ValueError("Markdown中未找到表格")
    return rows


def find_row_and_column(rows: list[list[str]], label: str) -> tuple[list[str], int] | None:
    wanted = normalized(label)
    for row in rows:
        for index, cell in enumerate(row):
            if normalized(cell) == wanted:
                return row, index
    return None


def value_after_label(rows: list[list[str]], label: str) -> str | None:
    located = find_row_and_column(rows, label)
    if located is None:
        return None
    row, index = located
    known = {normalized(item) for item in SCALAR_LABELS}
    for cell in row[index + 1 :]:
        if not cell:
            continue
        if normalized(cell) in known:
            return None
        return cell
    return None


def marked_options(value: str | None) -> list[str]:
    if not value:
        return []
    matches = re.findall(r"☑\s*(.*?)(?=\s*[☑□]|$)", value)
    return [re.sub(r"\s+", " ", item).strip(" ，,;；") for item in matches if item.strip()]


def marked_value(value: str | None) -> str | None:
    values = marked_options(value)
    return values[0] if values else None


def marked_bool(value: str | None) -> bool | None:
    selected = marked_value(value)
    if selected is None:
        return None
    if selected.startswith("是") or selected in {"已完成改造", "已评审"}:
        return True
    if selected.startswith("否") or selected in {"不适用", "未评审"}:
        return False
    return None


def normalize_date(value: str | None) -> str | None:
    if not value:
        return None
    match = re.search(r"(\d{2,4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", value)
    if not match:
        return value
    year, month, day = (int(item) for item in match.groups())
    if year < 100:
        year += 2000
    return f"{year:04d}-{month:02d}-{day:02d}"


def to_int(value: str | None) -> int | None:
    if value and re.fullmatch(r"\d+", value.strip()):
        return int(value)
    return None


def header_column(row: list[str], label: str) -> int | None:
    wanted = normalized(label)
    for index, cell in enumerate(row):
        if normalized(cell).startswith(wanted):
            return index
    return None


def parse_repeated_section(
    rows: list[list[str]],
    section: str,
    columns: dict[str, str],
    required_key: str,
) -> list[dict[str, Any]]:
    start = None
    for index, row in enumerate(rows):
        if row and normalized(row[0]).startswith(normalized(section)):
            start = index
            break
    if start is None:
        return []
    header = rows[start]
    positions = {key: header_column(header, label) for key, label in columns.items()}
    result: list[dict[str, Any]] = []
    for row in rows[start + 1 :]:
        if row and row[0]:
            break
        item = {
            key: (row[position] if position is not None and position < len(row) and row[position] else None)
            for key, position in positions.items()
        }
        if not item.get(required_key):
            continue
        if "maintenance_method" in item:
            item["maintenance_method"] = marked_value(item["maintenance_method"])
        for number_key in ("quantity", "running_days"):
            if number_key in item:
                item[number_key] = to_int(item[number_key])
        result.append(item)
    return result


def parse_operations_contacts(rows: list[list[str]]) -> list[dict[str, str | None]]:
    start = None
    for index, row in enumerate(rows):
        if row and normalized(row[0]).startswith(normalized("网络运维负责人信息")):
            start = index
            break
    if start is None or start + 1 >= len(rows):
        return []
    header, data = rows[start], rows[start + 1]
    positions = {
        "name": header_column(header, "网络运维责任人"),
        "department": header_column(header, "部门名称"),
        "contact": header_column(header, "联系方式"),
    }
    item = {
        key: (data[position] if position is not None and position < len(data) and data[position] else None)
        for key, position in positions.items()
    }
    return [item] if any(item.values()) else []


def parse_public_ports(rows: list[list[str]]) -> list[dict[str, Any]]:
    section = "网络单元使用的主要协议和端口"
    start = None
    for index, row in enumerate(rows):
        if row and normalized(row[0]).startswith(normalized(section)):
            start = index
            break
    if start is None:
        return []
    header = rows[start]
    port_col = header_column(header, "端口")
    protocol_col = header_column(header, "端口描述（协议）")
    ip_col = header_column(header, "对应IP段")
    result = []
    for row in rows[start + 1 :]:
        if row and row[0]:
            break
        port = row[port_col] if port_col is not None and port_col < len(row) else ""
        if not port:
            continue
        protocol = row[protocol_col] if protocol_col is not None and protocol_col < len(row) else None
        ip_ranges = row[ip_col] if ip_col is not None and ip_col < len(row) else None
        result.append(
            {
                "ports": [item.strip() for item in re.split(r"[;；]", port) if item.strip()],
                "protocols": [item.strip() for item in re.split(r"[,，、]", protocol or "") if item.strip()],
                "ip_ranges_raw": ip_ranges,
            }
        )
    return result


def translate_item_keys(
    items: list[dict[str, Any]],
    key_names: dict[str, str],
) -> list[dict[str, Any]]:
    return [
        {key_names.get(key, key): value for key, value in item.items()}
        for item in items
    ]


def chinese_yes_no(value: bool | None) -> str | None:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return None


def chinese_result(result: dict[str, Any]) -> dict[str, Any]:
    """将内部处理字段转换为备案表中对应的中文字段名。"""
    contact = result["contact"]
    operations_contacts = translate_item_keys(
        result["operations_contacts"],
        {"name": "姓名", "department": "部门名称", "contact": "联系方式"},
    )
    hardware = translate_item_keys(
        result["hardware_usage"],
        {
            "type": "类型",
            "brand": "品牌",
            "model": "型号",
            "quantity": "数量（台）",
            "maintenance_method": "维护方式",
            "running_days": "已运行时间（天）",
            "machine_room": "所在机房位置",
            "responsible_person": "负责人",
            "contact": "联系方式",
            "issues": "存在问题",
        },
    )
    security_systems = translate_item_keys(
        result["security_system_usage"],
        {"type": "类型", "brand": "品牌", "model": "型号", "quantity": "数量（台）"},
    )
    base_software = translate_item_keys(
        result["base_software_usage"],
        {
            "type": "类型",
            "vendor": "厂商",
            "version": "当前版本",
            "quantity": "数量（套）",
            "maintenance_method": "维护方式",
            "running_days": "已运行时间（天）",
            "machine_room": "所在机房位置",
            "host_device": "所在设备",
            "genuine_software": "正版软件",
            "issues": "存在问题",
        },
    )
    public_ports = translate_item_keys(
        result["public_protocol_ports"],
        {"ports": "端口", "protocols": "协议", "ip_ranges_raw": "对应IP段原文"},
    )
    translated: dict[str, Any] = {
        "文档类型": "网络单元备案信息表",
        "网络/系统单元名称": result["network_system_name"],
        "投入运行时间": result["operation_date"],
        "填表人": result["form_filler"],
        "填表时间": result["form_date"],
        "网络/系统单元类型": result["network_system_type"],
        "联系人信息": {
            "姓名": contact["name"],
            "职务/职称": contact["title"],
            "办公电话": contact["office_phone"],
            "移动电话": contact["mobile_phone"],
            "电子邮件": contact["email"],
        },
        "网络运维负责人信息": operations_contacts,
        "服务范围": result["service_scope"],
        "服务对象": result["service_target"],
        "专家评审": result["expert_review"],
        "所要申报的安全等级": result["declared_security_level"],
        "定级时间": result["classification_date"],
        "主要硬件使用情况": hardware,
        "安全系统使用情况": security_systems,
        "基础软件使用情况": base_software,
        "IPV6改造情况": result["ipv6_status"],
        "IPV6改造情况说明": result["ipv6_description"],
        "是否拥有公网IP地址": chinese_yes_no(result["has_public_ip"]),
        "是否存在主要的协议和端口（公网）": chinese_yes_no(result["has_public_protocol_ports"]),
        "网络单元使用的主要协议和端口": public_ports,
        "网络用户信息存储处理情况": result["network_user_information_status"],
        "接入带宽": result["access_bandwidth"],
        "信息服务系统是否使用域名": chinese_yes_no(result["uses_domain"]),
        "是否使用.cn域名": chinese_yes_no(result["uses_cn_domain"]),
        "网络单元所用域名": result["domains"],
    }
    if result.get("source"):
        translated["来源文件"] = result["source"]
    return translated


@dataclass(frozen=True)
class WordGridCell:
    row: int
    col: int
    row_span: int
    col_span: int
    text: str

    @property
    def required(self) -> bool:
        return "*" in self.text


def word_inline_text(inlines: Any) -> str:
    values = []
    for inline in inlines or []:
        if inline.kind == "text":
            values.append(inline.text or "")
        elif inline.kind == "link":
            values.append(word_inline_text(inline.content))
        elif inline.kind == "line_break":
            values.append(" ")
    return "".join(values)


def word_blocks_text(blocks: Any) -> str:
    values = []
    for block in blocks or []:
        if block.kind in {"paragraph", "heading"}:
            values.append(word_inline_text(block.content))
        elif block.kind == "list":
            for item in block.list.items:
                values.append(word_blocks_text(item.blocks))
        elif block.kind == "table":
            for row in block.table.grid:
                for slot in row:
                    if slot.kind == "origin":
                        values.append(word_blocks_text(slot.cell.blocks))
    return re.sub(r"\s+", " ", " ".join(value for value in values if value)).strip()


def load_word_grid(source: Path, docx_data: bytes | None = None) -> list[list[WordGridCell]]:
    try:
        import anydoc  # type: ignore
    except (ImportError, ModuleNotFoundError) as error:
        raise RuntimeError("未安装firecrawl-anydoc") from error

    if docx_data is not None:
        data = docx_data
    elif source.suffix.lower() in {".doc", ".wps"}:
        data, warnings = convert_doc_to_docx_bytes(source)
        if data is None:
            raise ValueError("旧版Word/WPS转换失败：" + "；".join(warnings))
    elif source.suffix.lower() == ".docx":
        data = source.read_bytes()
    else:
        raise ValueError("完整合并单元格解析仅支持.doc、.docx和.wps")

    document = anydoc.to_document(data, "docx")
    tables = [block.table for block in document.blocks if block.kind == "table"]
    if not tables:
        raise ValueError("Word中未找到备案信息表")
    table = max(tables, key=lambda item: len(item.grid))
    result: list[list[WordGridCell]] = []
    for row_index, row in enumerate(table.grid):
        values = []
        for col_index, slot in enumerate(row):
            if slot.kind != "origin":
                continue
            values.append(
                WordGridCell(
                    row=row_index,
                    col=col_index,
                    row_span=slot.cell.row_span,
                    col_span=slot.cell.col_span,
                    text=word_blocks_text(slot.cell.blocks),
                )
            )
        result.append(values)
    return result


def word_label(value: str) -> str:
    return re.sub(r"[\s*]", "", value).upper()


def find_word_cell(
    rows: list[list[WordGridCell]],
    label: str,
    row_index: int | None = None,
) -> WordGridCell | None:
    wanted = word_label(label)
    candidates = rows if row_index is None else [rows[row_index]]
    for row in candidates:
        for cell in row:
            if word_label(cell.text).startswith(wanted):
                return cell
    return None


def cell_at(rows: list[list[WordGridCell]], row: int, col: int) -> WordGridCell | None:
    if row < 0 or row >= len(rows):
        return None
    return next((cell for cell in rows[row] if cell.col == col), None)


def value_right(rows: list[list[WordGridCell]], label_cell: WordGridCell) -> str | None:
    value_cell = cell_at(rows, label_cell.row, label_cell.col + label_cell.col_span)
    return value_cell.text or None if value_cell else None


def field_value(
    rows: list[list[WordGridCell]],
    label: str,
    transform: Any = None,
    include_raw: bool = False,
    row_index: int | None = None,
) -> dict[str, Any]:
    label_cell = find_word_cell(rows, label, row_index)
    if label_cell is None:
        return {"值": None, "必填": False, "解析状态": "未找到字段"}
    raw = value_right(rows, label_cell)
    value = transform(raw) if transform else raw
    result: dict[str, Any] = {"值": value, "必填": label_cell.required}
    if include_raw and raw:
        result["原文"] = raw
    return result


def repeated_word_section(
    rows: list[list[WordGridCell]],
    section_label: str,
    fields: dict[str, tuple[str, Any]],
    identity_field: str,
) -> dict[str, Any]:
    section = find_word_cell(rows, section_label)
    if section is None:
        return {"必填": False, "字段定义": {}, "记录": [], "解析状态": "未找到分组"}
    headers: dict[str, WordGridCell | None] = {
        output_name: find_word_cell(rows, source_label, section.row)
        for output_name, (source_label, _transform) in fields.items()
    }
    definitions = {
        output_name: {"必填": bool(header and header.required)}
        for output_name, header in headers.items()
    }
    records = []
    for row_index in range(section.row + 1, min(len(rows), section.row + section.row_span)):
        record: dict[str, Any] = {}
        for output_name, (_source_label, transform) in fields.items():
            header = headers[output_name]
            source_cell = cell_at(rows, row_index, header.col) if header else None
            raw = source_cell.text if source_cell and source_cell.text else None
            record[output_name] = transform(raw) if transform else raw
        if record.get(identity_field):
            records.append(record)
    return {"必填": section.required, "字段定义": definitions, "记录": records}


def parse_registration_word(source: Path, docx_data: bytes | None = None) -> dict[str, Any]:
    rows = load_word_grid(source, docx_data)
    checkbox = lambda value: marked_value(value)
    number = lambda value: to_int(value)
    date = lambda value: normalize_date(value)

    contact_section = find_word_cell(rows, "联系人信息")
    service_section = find_word_cell(rows, "服务情况")

    result: dict[str, Any] = {
        "文档类型": "网络单元备案信息表",
        "来源文件": str(source),
        "网络/系统单元名称": field_value(rows, "网络/系统单元名称"),
        "投入运行时间": field_value(rows, "投入运行时间", date),
        "填表人": field_value(rows, "填表人"),
        "填表时间": field_value(rows, "填表时间", date),
        "网络/系统单元类型": field_value(rows, "网络/系统单元类型"),
        "联系人信息": {
            "必填": bool(contact_section and contact_section.required),
            "字段": {
                "姓名": field_value(rows, "姓名"),
                "职务/职称": field_value(rows, "职务/职称"),
                "办公电话": field_value(rows, "办公电话"),
                "电子邮件": field_value(rows, "电子邮件"),
                "移动电话": field_value(rows, "移动电话"),
            },
        },
        "网络运维负责人信息": repeated_word_section(
            rows,
            "网络运维负责人信息",
            {
                "网络运维责任人": ("网络运维责任人", None),
                "部门名称": ("部门名称", None),
                "联系方式": ("联系方式", None),
            },
            "网络运维责任人",
        ),
        "服务情况": {
            "必填": bool(service_section and service_section.required),
            "字段": {
                "服务范围": field_value(rows, "服务范围", checkbox, True),
                "服务对象": field_value(rows, "服务对象", checkbox, True),
            },
        },
        "专家评审": field_value(rows, "专家评审", checkbox, True),
        "所要申报的安全等级": field_value(rows, "所要申报的安全等级", checkbox, True),
        "定级时间": field_value(rows, "定级时间", date),
        "主要硬件使用情况": repeated_word_section(
            rows,
            "主要硬件使用情况",
            {
                "类型": ("类型", None), "品牌": ("品牌", None), "型号": ("型号", None),
                "数量（台）": ("数量(台)", number), "维护方式": ("维护方式", checkbox),
                "已运行时间（天）": ("已运行时间（天）", number),
                "所在机房位置": ("所在机房位置", None), "负责人": ("负责人", None),
                "联系方式": ("联系方式", None), "存在问题": ("存在问题", None),
            },
            "类型",
        ),
        "安全系统使用情况": repeated_word_section(
            rows,
            "安全系统使用情况",
            {
                "类型": ("类型", None), "品牌": ("品牌", None), "型号": ("型号", None),
                "数量（台）": ("数量(台)", number), "维护方式": ("维护方式", checkbox),
                "已运行时间（天）": ("已运行时间（天）", number),
                "所在机房位置": ("所在机房位置", None), "负责人": ("负责人", None),
                "联系方式": ("联系方式", None), "存在问题": ("存在问题", None),
            },
            "类型",
        ),
        "基础软件使用情况": repeated_word_section(
            rows,
            "基础软件使用情况",
            {
                "类型": ("类型", None), "厂商": ("厂商", None), "当前版本": ("当前版本", None),
                "数量（套）": ("数量（套）", number), "维护方式": ("维护方式", checkbox),
                "已运行时间（天）": ("已运行时间（天）", number),
                "所在机房位置": ("所在机房位置", None), "所在设备": ("所在设备", None),
                "正版软件": ("正版软件", None), "存在问题": ("存在问题", None),
            },
            "类型",
        ),
        "应用软件情况": repeated_word_section(
            rows,
            "应用软件情况",
            {
                "名称": ("名称", None), "当前版本": ("当前版本", None), "研发方": ("研发方", checkbox),
                "所在机房位置": ("所在机房位置", None), "所在设备": ("所在设备", None),
                "数量（套）": ("数量（套）", number), "维护方式": ("维护方式", checkbox),
                "已运行时间（天）": ("已运行时间（天）", number), "域名": ("域名", checkbox),
                "端口": ("端口", checkbox), "URL": ("url", checkbox), "中间件类型": ("中间件类型", checkbox),
                "存在问题": ("存在问题", None),
            },
            "名称",
        ),
        "IPV6改造情况": field_value(rows, "IPV6改造情况", checkbox, True),
        "IPV6改造情况说明": field_value(rows, "IPV6改造情况说明"),
        "是否拥有公网IP地址": field_value(rows, "是否拥有公网IP地址", checkbox, True),
        "是否存在主要的协议和端口（公网）": field_value(rows, "是否存在主要的协议和端口（公网）", checkbox, True),
        "网络单元使用的主要协议和端口": repeated_word_section(
            rows,
            "网络单元使用的主要协议和端口",
            {"端口": ("端口", None), "端口描述（协议）": ("端口描述（协议）", None), "对应IP段": ("对应IP段", None)},
            "端口",
        ),
        "处理网络数据信息": repeated_word_section(
            rows,
            "处理网络数据信息",
            {"网络数据类型": ("网络数据类型", None), "数量（M/兆）": ("数量（M/兆）", number)},
            "网络数据类型",
        ),
        "网络用户信息存储处理情况": field_value(rows, "网络用户信息存储处理情况", checkbox, True),
        "接入带宽": field_value(rows, "接入带宽"),
        "信息服务系统是否使用域名": field_value(rows, "信息服务系统是否使用域名", checkbox, True),
        "是否使用.cn域名": field_value(rows, "是否使用.cn域名", checkbox, True),
        "网络单元所用域名": field_value(rows, "网络单元所用域名"),
        ".cn域名相关记录": {
            "必填": False,
            "字段": {
                "NS记录": field_value(rows, "NS记录"),
                "A记录": field_value(rows, "A记录"),
            },
        },
        "域名注册服务机构信息": {
            "必填": False,
            "字段": {
                "机构名称": field_value(rows, "机构名称"),
                "办公电话": field_value(rows, "办公电话", row_index=41),
            },
        },
    }
    return result


def parse_registration_markdown(markdown: str, source: str | None = None) -> dict[str, Any]:
    rows = parse_table_rows(markdown)
    service_scope_raw = value_after_label(rows, "服务范围")
    service_target_raw = value_after_label(rows, "服务对象")
    expert_review_raw = value_after_label(rows, "专家评审")
    level_raw = value_after_label(rows, "所要申报的安全等级")
    ipv6_raw = value_after_label(rows, "IPV6改造情况")
    public_ip_raw = value_after_label(rows, "是否拥有公网IP地址")
    public_port_raw = value_after_label(rows, "是否存在主要的协议和端口（公网）")
    domain_raw = value_after_label(rows, "信息服务系统是否使用域名")
    cn_domain_raw = value_after_label(rows, "是否使用.cn域名")
    network_user_raw = value_after_label(rows, "网络用户信息存储处理情况")

    result: dict[str, Any] = {
        "document_type": "network_unit_registration",
        "network_system_name": value_after_label(rows, "网络/系统单元名称"),
        "operation_date": normalize_date(value_after_label(rows, "投入运行时间")),
        "form_filler": value_after_label(rows, "填表人"),
        "form_date": normalize_date(value_after_label(rows, "填表时间")),
        "network_system_type": value_after_label(rows, "网络/系统单元类型"),
        "contact": {
            "name": value_after_label(rows, "姓名"),
            "title": value_after_label(rows, "职务/职称"),
            "office_phone": value_after_label(rows, "办公电话"),
            "mobile_phone": value_after_label(rows, "移动电话"),
            "email": value_after_label(rows, "电子邮件"),
        },
        "operations_contacts": parse_operations_contacts(rows),
        "service_scope": marked_value(service_scope_raw),
        "service_target": marked_value(service_target_raw),
        "expert_review": marked_value(expert_review_raw),
        "declared_security_level": marked_value(level_raw),
        "classification_date": normalize_date(value_after_label(rows, "定级时间")),
        "hardware_usage": parse_repeated_section(
            rows,
            "主要硬件使用情况",
            {
                "type": "类型",
                "brand": "品牌",
                "model": "型号",
                "quantity": "数量",
                "maintenance_method": "维护方式",
                "running_days": "已运行时间",
                "machine_room": "所在机房位置",
                "responsible_person": "负责人",
                "contact": "联系方式",
                "issues": "存在问题",
            },
            "type",
        ),
        "security_system_usage": parse_repeated_section(
            rows,
            "安全系统使用情况",
            {"type": "类型", "brand": "品牌", "model": "型号", "quantity": "数量"},
            "type",
        ),
        "base_software_usage": parse_repeated_section(
            rows,
            "基础软件使用情况",
            {
                "type": "类型",
                "vendor": "厂商",
                "version": "当前版本",
                "quantity": "数量",
                "maintenance_method": "维护方式",
                "running_days": "已运行时间",
                "machine_room": "所在机房位置",
                "host_device": "所在设备",
                "genuine_software": "正版软件",
                "issues": "存在问题",
            },
            "type",
        ),
        "ipv6_status": marked_value(ipv6_raw),
        "ipv6_description": value_after_label(rows, "IPV6改造情况说明"),
        "has_public_ip": marked_bool(public_ip_raw),
        "has_public_protocol_ports": marked_bool(public_port_raw),
        "public_protocol_ports": parse_public_ports(rows),
        "network_user_information_status": marked_value(network_user_raw),
        "access_bandwidth": value_after_label(rows, "接入带宽"),
        "uses_domain": marked_bool(domain_raw),
        "uses_cn_domain": marked_bool(cn_domain_raw),
        "domains": [value_after_label(rows, "网络单元所用域名")]
        if value_after_label(rows, "网络单元所用域名")
        else [],
    }
    if source:
        result["source"] = source
    return chinese_result(result)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将备案信息表Word/Markdown解析为KV JSON。")
    parser.add_argument("input", type=Path, help="备案信息表.doc/.docx/.wps，或anydoc生成的Markdown")
    parser.add_argument("-o", "--output", type=Path, help="JSON输出路径；不指定则打印到终端")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source = args.input.expanduser().resolve()
    if not source.is_file():
        print(f"输入文件不存在：{source}", file=sys.stderr)
        return 2
    try:
        if source.suffix.lower() in {".doc", ".docx", ".wps"}:
            result = parse_registration_word(source)
        elif source.suffix.lower() in {".md", ".markdown"}:
            result = parse_registration_markdown(source.read_text(encoding="utf-8"), str(source))
        else:
            raise ValueError("仅支持.doc、.docx、.md和.markdown格式")
        content = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            output = args.output.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(content, encoding="utf-8")
            print(output)
        else:
            print(content, end="")
        return 0
    except Exception as error:
        print(f"解析失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
