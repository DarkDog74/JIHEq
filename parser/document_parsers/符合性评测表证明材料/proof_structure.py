# -*- coding: utf-8 -*-
"""Build ordered evidence-item sections from compliance proof documents."""
from __future__ import annotations

import re
from typing import Any

from source import DocumentBlock, DocumentStructure, ImageRef, TableCell, TableData, clean_text


PARSER_VERSION = "1.0.0"
AUDIT_CODE_RE = re.compile(r"(?<!\d)(\d{2}(?:-\d{2}){4})(?!\d)")


def _style_heading_level(block: DocumentBlock) -> int | None:
    style = f"{block.style_id} {block.style_name}".strip().casefold()
    if "toc" in style or "目录" in style:
        return None
    match = re.search(r"(?:heading|标题)\s*([1-9])(?:\D|$)", style)
    return int(match.group(1)) if match else None


def split_audit_paragraph(text: str) -> tuple[str, str] | None:
    """Return (leading body text, audit heading), preserving both parts."""
    value = clean_text(text)
    match = AUDIT_CODE_RE.search(value)
    if match is None:
        return None
    prefix = value[: match.start()]
    normalized_prefix = re.sub(r"[\s：:]", "", prefix)
    if normalized_prefix in {"", "检查点"}:
        return "", value
    return clean_text(prefix), clean_text(value[match.start() :])


def split_audit_heading(text: str) -> tuple[str, str]:
    """Split an audit line into its code label and descriptive content.

    A combined label such as ``29-01-01-01-01和29-01-01-01-02`` remains a
    single section title.  Text after the separating colon (or immediately
    after a lone code) becomes the first paragraph under that section.
    """
    value = clean_text(text)
    first = AUDIT_CODE_RE.search(value)
    if first is None:
        return value.rstrip("：:"), ""
    remainder = value[first.start() :]
    separator = re.search(r"[：:]", remainder)
    if separator is not None:
        title = clean_text(remainder[: separator.start()]).strip()
        description = clean_text(remainder[separator.end() :]).strip()
        return title or first.group(1), description
    title = first.group(1)
    description = clean_text(value[first.end() :]).lstrip("：: ")
    return title, description


def identify_heading(block: DocumentBlock) -> tuple[int, str] | None:
    if block.kind != "paragraph" or not clean_text(block.text):
        return None
    audit = split_audit_paragraph(block.text)
    if audit is not None:
        title, _ = split_audit_heading(audit[1])
        return 1, title
    level = _style_heading_level(block)
    if level is None:
        return None
    return level, clean_text(block.text).rstrip("：:")


def image_content(image: ImageRef) -> dict[str, Any]:
    value: dict[str, Any] = {
        "类型": "图片",
        "图片编号": image.image_id,
        "地址": image.relative_path,
    }
    if image.alt_text:
        value["替代文字"] = image.alt_text
    return value


def _paragraph_content(block: DocumentBlock, include_text: bool = True) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if block.parts:
        for part in block.parts:
            if part.kind == "text" and part.text and include_text:
                items.append({"类型": "段落", "文本": part.text})
            elif part.kind == "image" and part.image is not None:
                items.append(image_content(part.image))
        return items
    if include_text and clean_text(block.text):
        items.append({"类型": "段落", "文本": clean_text(block.text)})
    items.extend(image_content(image) for image in block.images)
    return items


def _cell_covering(table: TableData, row_index: int, column: int) -> TableCell | None:
    for origin_row in table.rows:
        for cell in origin_row:
            if (
                cell.row_index <= row_index < cell.row_index + cell.row_span
                and cell.column_start <= column < cell.column_end
            ):
                return cell
    return None


def _header_value(table: TableData, row_index: int, column: int) -> str:
    cell = _cell_covering(table, row_index, column)
    return cell.text if cell is not None else ""


def _data_value(table: TableData, row_index: int, column: int) -> str:
    cell = _cell_covering(table, row_index, column)
    if cell is None or column != cell.column_start:
        return ""
    return cell.text


def _field_label(text: str) -> str:
    return re.sub(r"\s+", "", clean_text(text)).strip("：:")


def _looks_like_header(text: str) -> bool:
    label = _field_label(text)
    return any(
        hint in label
        for hint in (
            "序号",
            "名称",
            "类型",
            "类别",
            "描述",
            "内容",
            "编号",
            "人员",
            "单位",
            "姓名",
            "结果",
            "结论",
            "证明",
        )
    )


def _header_depth(table: TableData) -> int:
    if not table.rows or table.column_count <= 1:
        return 0
    first = [clean_text(value) for value in table.matrix[0] if clean_text(value)]
    if not first or not any(_looks_like_header(value) for value in first):
        return 0
    if len(table.rows) > 1:
        second = [clean_text(value) for value in table.matrix[1] if clean_text(value)]
        grouped = any(cell.column_span > 1 or cell.row_span > 1 for cell in table.rows[0])
        if grouped and len(second) >= 2:
            return 2
    return 1


def _headers(table: TableData, depth: int) -> list[str]:
    result: list[str] = []
    used: dict[str, int] = {}
    for column in range(table.column_count):
        parts: list[str] = []
        for row_index in range(depth):
            part = _field_label(_header_value(table, row_index, column))
            if part and part not in parts:
                parts.append(part)
        base = "·".join(parts) or f"列{column + 1}"
        count = used.get(base, 0) + 1
        used[base] = count
        result.append(base if count == 1 else f"{base}（{count}）")
    return result


def _person_evidence_table(table: TableData) -> dict[str, Any] | None:
    if table.column_count != 3 or not table.rows:
        return None
    first = _header_value(table, 0, 0)
    if "维护人员" not in first:
        return None
    records = [
        {
            "人员类别": _header_value(table, row_index, 0),
            "所属单位/角色": _header_value(table, row_index, 1),
            "姓名": _header_value(table, row_index, 2),
        }
        for row_index in range(len(table.rows))
    ]
    return {
        "类型": "表格",
        "表格编号": f"T{table.table_index + 1:03d}",
        "表格类型": "人员信息",
        "人员信息": records,
    }


def _table_images(table: TableData) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for occurrence in table.images:
        value = image_content(occurrence.image)
        value["所在行"] = occurrence.row_index + 1
        value["所在列"] = occurrence.column_start + 1
        result.append(value)
    return result


def table_content(table: TableData) -> dict[str, Any]:
    person_table = _person_evidence_table(table)
    if person_table is not None:
        value = person_table
    else:
        depth = _header_depth(table)
        headers = _headers(table, depth) if depth else [
            f"列{column + 1}" for column in range(table.column_count)
        ]
        records = [
            {
                header: _data_value(table, row_index, column)
                for column, header in enumerate(headers)
            }
            for row_index in range(depth, len(table.rows))
        ]
        value = {
            "类型": "表格",
            "表格编号": f"T{table.table_index + 1:03d}",
            "表格类型": "数据记录",
            "数据记录": records,
        }
    images = _table_images(table)
    if images:
        value["图片"] = images
    return value


def _append_items(result: dict[str, Any], stack: list[dict[str, Any]], items: list[dict[str, Any]]) -> None:
    if not items:
        return
    target = stack[-1]["内容"] if stack else result["前置内容"]
    target.extend(items)


def _start_section(
    result: dict[str, Any],
    stack: list[dict[str, Any]],
    level: int,
    title: str,
) -> dict[str, Any]:
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
    return section


def parse_nested_proof(structure: DocumentStructure, document_name: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "文档名称": document_name,
        "前置内容": [],
        "章节": [],
    }
    stack: list[dict[str, Any]] = []

    for block in structure.blocks:
        if block.kind == "paragraph":
            audit = split_audit_paragraph(block.text)
            if audit is not None:
                leading_text, raw_heading = audit
                if leading_text:
                    _append_items(
                        result,
                        stack,
                        [{"类型": "段落", "文本": leading_text}],
                    )
                title, description = split_audit_heading(raw_heading)
                section = _start_section(result, stack, 1, title)
                if description:
                    section["内容"].append({"类型": "段落", "文本": description})
                section["内容"].extend(_paragraph_content(block, include_text=False))
                continue

            heading = identify_heading(block)
            if heading is not None:
                level, title = heading
                section = _start_section(result, stack, level, title)
                section["内容"].extend(_paragraph_content(block, include_text=False))
                continue
            _append_items(result, stack, _paragraph_content(block))
        elif block.kind == "table" and block.table is not None:
            _append_items(result, stack, [table_content(block.table)])
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
