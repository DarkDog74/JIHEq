# -*- coding: utf-8 -*-
"""Build an ordered, heading-nested, business-oriented risk report JSON."""
from __future__ import annotations

import re
from typing import Any

from source import DocumentBlock, DocumentStructure, ImageRef, TableCell, TableData, clean_text


PARSER_VERSION = "1.0.0"

APPENDIX_A_SUBSECTIONS = {
    "业务资产赋值",
    "系统资产赋值表",
    "系统组件和单元资产赋值表",
    "人力资源赋值表",
    "其他资产赋值表",
}

PAIR_LABELS = {
    "系统名称",
    "系统版本",
    "委托单位",
    "生产单位",
    "检验类别",
    "检验来源",
    "检验项目",
    "检验依据",
    "检验结论",
    "检验开始",
    "检验结束",
    "到样日期",
    "检验地点",
    "签发日期",
    "编制",
    "审核",
    "批准",
}

SYSTEM_INFO_FIELDS = (
    "系统名称",
    "系统版本",
    "委托单位",
    "生产单位",
    "检验类别",
    "检验开始",
    "检验来源",
    "检验结束",
    "检验项目",
    "到样日期",
    "检验依据",
    "检验结论",
)


def _field_label(text: str) -> str:
    return re.sub(r"\s+", "", clean_text(text)).strip("：:")


def _display_title(text: str) -> str:
    value = clean_text(text)
    patterns = (
        r"^第[一二三四五六七八九十0-9]+章[\s　]*",
        r"^[0-9]+(?:\.[0-9]+){0,5}[\s　、.．]+",
        r"^[一二三四五六七八九十]+[、.．][\s　]*",
        r"^[（(][一二三四五六七八九十0-9]+[）)][\s　]*",
    )
    for pattern in patterns:
        value = re.sub(pattern, "", value, count=1)
    return value.rstrip("：:")


def identify_heading(block: DocumentBlock) -> tuple[int, str] | None:
    if block.kind != "paragraph" or not clean_text(block.text):
        return None
    style = f"{block.style_id} {block.style_name}".strip().casefold()
    if "toc" in style or "目录" in style or "正文表标题" in style or "caption" in style:
        return None
    title = _display_title(block.text)
    if not title:
        return None

    match = re.search(r"(?:heading|标题)\s*([1-9])(?:\D|$)", style)
    if match:
        return int(match.group(1)), title

    if "附录一级标题" in style:
        if _field_label(title) in APPENDIX_A_SUBSECTIONS:
            return 2, title
        return 1, title

    # Fallbacks are intentionally conservative. Single numbered body-list
    # paragraphs are not promoted to headings.
    if re.match(r"^附录[A-ZＡ-Ｚ一二三四五六七八九十0-9]", clean_text(block.text)):
        return 1, title
    dotted = re.match(r"^([0-9]+(?:\.[0-9]+)+)[\s　、.．]+", clean_text(block.text))
    if dotted:
        level = min(dotted.group(1).count(".") + 1, 9)
        return level, title
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


def _paragraph_content(block: DocumentBlock, heading: bool = False) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if block.parts:
        for part in block.parts:
            if part.kind == "text" and part.text and not heading:
                items.append({"类型": "段落", "文本": part.text})
            elif part.kind == "image" and part.image is not None:
                items.append(image_content(part.image))
        return items
    if block.text and not heading:
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
    if cell is None:
        return ""
    # A horizontal merge is one value, so write it once rather than cloning it
    # into several unrelated business fields. A vertical merge is carried to
    # every covered record so later rows do not appear to lose the value.
    if column != cell.column_start:
        return ""
    return cell.text


def _raw_nonempty(table: TableData, row_index: int) -> list[str]:
    if row_index >= len(table.matrix):
        return []
    return [clean_text(value) for value in table.matrix[row_index] if clean_text(value)]


def _looks_like_header(text: str) -> bool:
    label = _field_label(text)
    hints = (
        "序号",
        "名称",
        "类型",
        "类别",
        "描述",
        "定义",
        "标识",
        "赋值",
        "等级",
        "日期",
        "角色",
        "姓名",
        "来源",
        "行为",
        "资产",
        "业务",
        "系统",
        "措施",
        "风险",
        "用途",
        "数量",
        "权值",
        "功能",
    )
    return any(hint in label for hint in hints)


def _header_depth(table: TableData) -> int:
    if not table.rows or table.column_count <= 1:
        return 0
    first = _raw_nonempty(table, 0)
    if not first or not any(_looks_like_header(value) for value in first):
        return 0

    if len(table.rows) > 1:
        second_raw = _raw_nonempty(table, 1)
        first_has_group = any(
            cell.column_span > 1 or cell.row_span > 1 for cell in table.rows[0]
        ) or len(first) < table.column_count
        if first_has_group and len(second_raw) >= 2:
            return 2
    return 1


def _unique_headers(table: TableData, depth: int) -> list[str]:
    headers: list[str] = []
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
        headers.append(base if count == 1 else f"{base}（{count}）")
    return headers


def _add_unique_field(target: dict[str, str], label: str, value: str) -> None:
    if label not in target:
        target[label] = value
        return
    number = 2
    while f"{label}（{number}）" in target:
        number += 1
    target[f"{label}（{number}）"] = value


def _key_value_rows(table: TableData) -> tuple[dict[str, str], list[dict[str, str]]] | None:
    label_count = sum(
        1
        for row in table.rows
        for cell in row
        if _field_label(cell.text) in PAIR_LABELS
    )
    if label_count < 3:
        return None

    fields: dict[str, str] = {}
    other_rows: list[dict[str, str]] = []
    for row_index, row in enumerate(table.rows):
        cells = sorted(row, key=lambda item: item.column_start)
        consumed: set[int] = set()
        found = False
        for index, cell in enumerate(cells):
            label = _field_label(cell.text)
            if label not in PAIR_LABELS:
                continue
            value = ""
            for candidate_index in range(index + 1, len(cells)):
                candidate = cells[candidate_index]
                candidate_label = _field_label(candidate.text)
                if candidate_label in PAIR_LABELS:
                    break
                if candidate.column_start >= cell.column_end:
                    value = candidate.text
                    consumed.add(candidate_index)
                    break
            _add_unique_field(fields, label, value)
            consumed.add(index)
            found = True
        leftovers = [
            cell.text
            for index, cell in enumerate(cells)
            if index not in consumed and clean_text(cell.text)
        ]
        if leftovers or not found:
            other_rows.append(
                {
                    "原表行号": str(row_index + 1),
                    "内容": " | ".join(leftovers),
                }
            )
    return fields, other_rows


def _record_type(headers: list[str], caption: str, section_titles: list[str]) -> str:
    joined = "|".join(headers)
    rules = (
        (("主要业务", "业务功能"), "业务资产"),
        (("业务应用系统/平台名称",), "系统资产"),
        (("设备名称", "所属业务应用系统/平台"), "设备资产"),
        (("系统软件类别",), "系统软件"),
        (("系统组件和单元资产名称",), "系统组件和单元资产"),
        (("资产IP", "资产类型"), "系统组件和单元资产"),
        (("人员类型",), "人力资源"),
        (("资产类型", "资产名称"), "其他资产"),
        (("文档名称", "主要内容"), "管理制度文档"),
        (("工具名称",), "评估工具"),
        (("角色", "姓名"), "评估人员"),
        (("威胁种类", "威胁行为"), "威胁信息"),
        (("已有安全措施",), "已有安全措施"),
        (("脆弱性名称",), "脆弱性信息"),
        (("资产风险等级",), "资产风险"),
        (("整改建议",), "风险处置建议"),
        (("整改情况",), "风险整改情况"),
    )
    for required, name in rules:
        if all(value in joined for value in required):
            return name
    if caption:
        return caption.rstrip("：:")
    if section_titles:
        title = section_titles[-1]
        return title if title.endswith("表") else f"{title}表"
    return "数据记录"


def _table_images(table: TableData) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for occurrence in table.images:
        value = image_content(occurrence.image)
        value["所在行"] = occurrence.row_index + 1
        value["所在列"] = occurrence.column_start + 1
        result.append(value)
    return result


def table_content(
    table: TableData,
    section_titles: list[str] | None = None,
    caption: str = "",
) -> dict[str, Any]:
    """Convert one structural table to named business fields and records."""
    section_titles = section_titles or []
    value: dict[str, Any] = {
        "类型": "表格",
        "表格编号": f"T{table.table_index + 1:03d}",
    }
    if caption:
        value["表格名称"] = caption

    key_values = _key_value_rows(table)
    if key_values is not None:
        fields, other_rows = key_values
        value["表格类型"] = "系统检验信息"
        value.update({field: "" for field in SYSTEM_INFO_FIELDS})
        value.update(fields)
        if other_rows:
            value["其他记录"] = other_rows
    else:
        depth = _header_depth(table)
        headers = _unique_headers(table, depth) if depth else [
            f"列{column + 1}" for column in range(table.column_count)
        ]
        start_row = depth
        records = [
            {
                header: _data_value(table, row_index, column)
                for column, header in enumerate(headers)
            }
            for row_index in range(start_row, len(table.rows))
        ]
        if table.column_count == 1 and not section_titles and not caption:
            record_type = "封面信息"
        else:
            record_type = _record_type(headers, caption, section_titles)
        value["表格类型"] = record_type
        value[record_type] = records

    images = _table_images(table)
    if images:
        value["图片"] = images
    return value


def _is_table_caption(block: DocumentBlock) -> bool:
    if block.kind != "paragraph" or not clean_text(block.text):
        return False
    style = f"{block.style_id} {block.style_name}".casefold()
    text = clean_text(block.text)
    if text.startswith(("图", "Figure", "figure")):
        return False
    return "正文表标题" in style or ("caption" in style and "表" in text)


def parse_nested_report(structure: DocumentStructure, document_name: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "文档名称": document_name,
        "前置内容": [],
        "章节": [],
    }
    stack: list[dict[str, Any]] = []
    pending_caption = ""

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
            section["内容"].extend(_paragraph_content(block, heading=True))
            pending_caption = ""
            continue

        if block.kind == "paragraph":
            content = _paragraph_content(block)
            if _is_table_caption(block):
                pending_caption = clean_text(block.text)
            elif content and not block.images:
                pending_caption = ""
        elif block.kind == "table" and block.table is not None:
            content = [
                table_content(
                    block.table,
                    [item["标题"] for item in stack],
                    pending_caption,
                )
            ]
            pending_caption = ""
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
