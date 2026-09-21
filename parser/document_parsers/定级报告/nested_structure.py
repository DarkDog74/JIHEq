# -*- coding: utf-8 -*-
"""Build an ordered, heading-nested representation of a grading report."""
from __future__ import annotations

import re
from typing import Any

from source import DocumentBlock, DocumentStructure, ImageRef, TableData, clean_text


PARSER_VERSION = "2.0.0"


LEVEL_2_TITLES = {
    "系统概述",
    "管理情况",
    "技术情况",
    "赋值理由",
    "采用定级方法及选择权重理由",
}

LEVEL_3_TITLES = {
    "业务/应用范围",
    "业务应用范围",
    "服务范围",
    "用户类型",
    "所属单位基本情况",
    "安全管理架构",
    "管理架构",
    "物理环境",
    "网络拓扑结构",
    "网络结构",
    "硬件设备部署情况",
    "网络边界划分",
    "云服务平台使用情况",
    "社会影响力赋值",
    "规模和服务范围赋值",
    "提供服务的重要性赋值",
    "所提供服务的重要性赋值",
}


def _display_title(text: str) -> str:
    value = clean_text(text)
    # Converted legacy .doc files may contain literal numbering while native
    # DOCX files often store numbering separately. Remove only a leading
    # heading marker so both sources produce the same title text.
    patterns = (
        r"^[（(][一二三四五六七八九十0-9]+[）)]\s*",
        r"^[一二三四五六七八九十]+[、.．]\s*",
        r"^[0-9]+\s*[、.．)）]\s*",
    )
    for pattern in patterns:
        value = re.sub(pattern, "", value, count=1)
    return value.rstrip("：:")


def _normalized_title(text: str) -> str:
    return re.sub(r"\s+", "", _display_title(text))


def _heading_style_level(block: DocumentBlock) -> int | None:
    style = f"{block.style_id} {block.style_name}".strip().lower()
    if "toc" in style or "目录" in style:
        return None
    match = re.search(r"(?:heading|标题)\s*([123])(?:\D|$)", style)
    return int(match.group(1)) if match else None


def identify_heading(block: DocumentBlock) -> tuple[int, str] | None:
    """Return (level, title) for supported grading-report headings."""
    if block.kind != "paragraph" or not clean_text(block.text):
        return None

    style = f"{block.style_id} {block.style_name}".strip().lower()
    # Table-of-contents paragraphs repeat real headings but are front matter.
    if "toc" in style or "目录" in style:
        return None
    if "HYPERLINK" in block.text or "PAGEREF" in block.text:
        return None

    title = _display_title(block.text)
    if not title:
        return None
    normalized = _normalized_title(title)

    style_level = _heading_style_level(block)
    if style_level is not None:
        return style_level, title

    # Fixed-template aliases recover pseudo-headings stored with body styles,
    # including the 4A report whose converted OOXML has no usable styles.
    if normalized in LEVEL_3_TITLES:
        return 3, title
    if normalized in LEVEL_2_TITLES or normalized.endswith("安全保护等级"):
        return 2, title
    if normalized.endswith("基本情况") and normalized != "所属单位基本情况":
        return 1, title
    if (
        ("安全等级的确定" in normalized or normalized.endswith("安全等级确定"))
        and "保护等级" not in normalized
    ):
        return 1, title
    return None


def image_content(image: ImageRef) -> dict[str, Any]:
    value: dict[str, Any] = {
        "类型": "图片",
        "图片编号": image.image_id,
        "地址": image.relative_path,
    }
    if image.alt_text:
        value["替代文字"] = image.alt_text
    return value


def table_merge_ranges(table: TableData) -> list[dict[str, int]]:
    """Return 1-based rectangular merge ranges without expanding cell text."""
    ranges: list[dict[str, int]] = []
    for row_index, row in enumerate(table.rows):
        for cell in row:
            if cell.column_span > 1 or cell.row_span > 1:
                ranges.append(
                    {
                        "起始行": row_index + 1,
                        "结束行": row_index + cell.row_span,
                        "起始列": cell.column_start + 1,
                        "结束列": cell.column_start + cell.column_span,
                    }
                )
    return ranges


def _table_label(text: str) -> str:
    return re.sub(r"[\s：:]", "", clean_text(text)).casefold()


def _all_table_labels(table: TableData) -> set[str]:
    return {
        _table_label(cell.text)
        for row in table.rows
        for cell in row
        if clean_text(cell.text)
    }


def _header_coordinates(
    table: TableData,
    aliases: dict[str, str],
    required: set[str],
) -> tuple[int, list[tuple[str, int, int]]] | None:
    for row_index, row in enumerate(table.rows):
        fields: list[tuple[str, int, int]] = []
        found: set[str] = set()
        for cell in sorted(row, key=lambda item: item.column_start):
            canonical = aliases.get(_table_label(cell.text))
            if canonical is None:
                continue
            fields.append((canonical, cell.column_start, cell.column_end))
            found.add(canonical)
        if required <= found:
            return row_index, fields
    return None


def _value_for_bounds(row: list, start: int, end: int) -> str:
    candidates = [
        cell
        for cell in row
        if max(0, min(cell.column_end, end) - max(cell.column_start, start)) > 0
    ]
    if not candidates:
        return ""
    return max(
        candidates,
        key=lambda item: (
            max(0, min(item.column_end, end) - max(item.column_start, start)),
            -abs(item.column_start - start),
        ),
    ).text


def _records_from_header(
    table: TableData,
    header: tuple[int, list[tuple[str, int, int]]],
) -> list[dict[str, str]]:
    row_index, fields = header
    records: list[dict[str, str]] = []
    for row in table.rows[row_index + 1 :]:
        records.append(
            {
                field: _value_for_bounds(row, start, end)
                for field, start, end in fields
            }
        )
    return records


REVISION_ALIASES = {
    "版本号": "版本号",
    "日期": "日期",
    "作者": "作者",
    "修改人": "作者",
    "审阅人": "审阅人",
    "摘要": "摘要",
}

ASSET_ALIASES = {
    "资产ip": "资产IP",
    "映射ip": "映射IP",
    "资产类型": "资产类型",
    "资产型号": "资产型号",
    "设备厂商": "设备厂商",
    "网络位置": "网络位置",
    "责任人": "责任人",
}

LEVEL_ALIASES = {
    "安全等级值k": "安全等级值k",
    "安全等级": "安全等级",
}

DOCUMENT_INFO_ALIASES = {
    "文档名称": "文档名称",
    "作者": "作者",
    "类别": "类别",
    "子类别": "子类别",
    "文件名称": "文件名称",
    "摘要": "摘要",
}


def _document_information(table: TableData) -> dict[str, str]:
    result = {field: "" for field in DOCUMENT_INFO_ALIASES.values()}
    revision_header = _header_coordinates(
        table,
        REVISION_ALIASES,
        {"版本号", "日期"},
    )
    revision_row = revision_header[0] if revision_header is not None else len(table.rows)
    for row in table.rows[:revision_row]:
        cells = sorted(row, key=lambda item: item.column_start)
        for index, cell in enumerate(cells):
            field = DOCUMENT_INFO_ALIASES.get(_table_label(cell.text))
            if field is None:
                continue
            value = ""
            for candidate in cells[index + 1 :]:
                if candidate.column_start >= cell.column_end:
                    value = candidate.text
                    break
            result[field] = value
            break
    return result


def _column_value(row: list, column: int) -> str:
    for cell in row:
        if cell.column_start <= column < cell.column_end:
            return cell.text
    return ""


def _cloud_records(table: TableData) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    for row in table.rows:
        if table.column_count <= 1:
            records.append({"云平台名称": _column_value(row, 0)})
        else:
            record = {
                "云平台物理位置": _column_value(row, 0),
                "云平台名称": _column_value(row, 1),
            }
            for column in range(2, table.column_count):
                record[f"其他列{column + 1}"] = _column_value(row, column)
            records.append(record)
    return records


def _generic_records(table: TableData) -> list[dict[str, str]]:
    return [
        {
            f"列{column + 1}": next(
                (
                    cell.text
                    for cell in row
                    if cell.column_start == column
                ),
                "",
            )
            for column in range(table.column_count)
        }
        for row in table.rows
    ]


def _table_images(table: TableData) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for occurrence in table.images:
        image = image_content(occurrence.image)
        image["所在行"] = occurrence.row_index + 1
        image["所在列"] = occurrence.column_start + 1
        result.append(image)
    return result


def table_content(table: TableData, section_titles: list[str] | None = None) -> dict[str, Any]:
    """Return business fields/records instead of OOXML cell coordinates."""
    labels = _all_table_labels(table)
    value: dict[str, Any] = {
        "类型": "表格",
        "表格编号": f"T{table.table_index + 1:03d}",
    }

    if "文档名称" in labels and "版本号" in labels:
        value["表格类型"] = "文档信息及修订记录"
        value.update(_document_information(table))
        header = _header_coordinates(table, REVISION_ALIASES, {"版本号", "日期"})
        value["修订记录"] = _records_from_header(table, header) if header else []
    elif "资产ip" in labels:
        value["表格类型"] = "资产信息"
        header = _header_coordinates(table, ASSET_ALIASES, {"资产IP"})
        value["资产信息"] = _records_from_header(table, header) if header else []
    elif {"安全等级值k", "安全等级"} <= labels:
        value["表格类型"] = "安全等级映射关系"
        header = _header_coordinates(
            table,
            LEVEL_ALIASES,
            {"安全等级值k", "安全等级"},
        )
        value["安全等级映射关系"] = (
            _records_from_header(table, header) if header else []
        )
    elif section_titles and _normalized_title(section_titles[-1]) == "云服务平台使用情况":
        value["表格类型"] = "云服务平台使用情况"
        value["云服务平台使用情况"] = _cloud_records(table)
    else:
        value["表格类型"] = "普通表格"
        value["记录"] = _generic_records(table)

    images = _table_images(table)
    if images:
        value["图片"] = images
    return value


def _paragraph_content(block: DocumentBlock, heading: bool = False) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if block.parts:
        for part in block.parts:
            if part.kind == "text" and part.text and not heading:
                items.append({"类型": "段落", "文本": part.text})
            elif part.kind == "image" and part.image is not None:
                items.append(image_content(part.image))
        return items

    # Backward-compatible fallback for synthetic structures and older callers.
    if block.text and not heading:
        items.append({"类型": "段落", "文本": clean_text(block.text)})
    items.extend(image_content(image) for image in block.images)
    return items


def parse_nested_report(structure: DocumentStructure, document_name: str) -> dict[str, Any]:
    """Convert ordered OOXML blocks into an ordered heading tree."""
    result: dict[str, Any] = {
        "文档名称": document_name,
        "前置内容": [],
        "章节": [],
    }
    stack: list[dict[str, Any]] = []

    for block in structure.blocks:
        heading = identify_heading(block)
        if heading is not None:
            level, title = heading
            while stack and stack[-1]["标题级别"] >= level:
                stack.pop()
            section: dict[str, Any] = {
                "类型": "章节",
                "标题级别": level,
                "标题": title,
                "内容": [],
            }
            if stack:
                stack[-1]["内容"].append(section)
            else:
                result["章节"].append(section)
            stack.append(section)
            # A heading paragraph can still contain an inline image.
            section["内容"].extend(_paragraph_content(block, heading=True))
            continue

        if block.kind == "paragraph":
            content = _paragraph_content(block)
        elif block.kind == "table" and block.table is not None:
            content = [
                table_content(
                    block.table,
                    [item["标题"] for item in stack],
                )
            ]
        else:
            content = []
        if not content:
            continue
        target = stack[-1]["内容"] if stack else result["前置内容"]
        target.extend(content)
    return result


def content_statistics(report: dict[str, Any]) -> dict[str, int]:
    counts = {"章节": 0, "段落": 0, "图片": 0, "表格": 0}

    def visit(items: list[dict[str, Any]]) -> None:
        for item in items:
            kind = item.get("类型")
            if kind in counts:
                counts[kind] += 1
            if kind == "表格":
                counts["图片"] += len(item.get("图片", []))
            if kind == "章节":
                visit(item.get("内容", []))

    visit(report.get("前置内容", []))
    visit(report.get("章节", []))
    return counts
