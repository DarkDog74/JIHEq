# -*- coding: utf-8 -*-
"""Business-field extraction for the 三同步 report template family."""
from __future__ import annotations

import re
from collections import Counter

from word_source import DocumentStructure, TableData, clean_text


PARSER_VERSION = "sansync-report-ooxml-1.0.0"


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", clean_text(text))


def _normal_date(text: str) -> str:
    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if not match:
        return ""
    return f"{match.group(1)}年{int(match.group(2))}月{int(match.group(3))}日"


def _paragraphs(structure: DocumentStructure) -> list[str]:
    return [block.text for block in structure.blocks if block.kind == "paragraph" and block.text]


def _title(paragraphs: list[str]) -> str:
    for paragraph in paragraphs:
        if "三同步" in paragraph and "报告" in paragraph:
            return paragraph
    return paragraphs[0] if paragraphs else ""


def _body_sentence(paragraphs: list[str]) -> str:
    for paragraph in paragraphs:
        value = _compact(paragraph)
        if "我公司" in value and "年度" in value and "三同步" in value:
            return paragraph
    for paragraph in paragraphs:
        value = _compact(paragraph)
        if "我公司" in value and "年度" in value:
            return paragraph
    return ""


def _business_from_body(body: str, title: str) -> dict:
    values = {
        "网络/系统单元名称": "",
        "报告年度": "",
        "是否发生新建、改建或扩建": "",
        "建设变更说明": "",
        "网络安全措施落实情况": "",
        "附件说明": "",
    }
    compact = _compact(body)
    match = re.search(r"我公司(.*?)[，,]在(20\d{2})年度(.*)", compact)
    if match:
        values["网络/系统单元名称"] = match.group(1).strip("，,。；;：:")
        values["报告年度"] = f"{match.group(2)}年度"
        tail = match.group(3).strip("，,。；;：:")
    else:
        tail = ""

    if not values["网络/系统单元名称"]:
        fallback = re.sub(r"(?:网管中心)?三同步(?:情况)?报告.*$", "", _compact(title))
        values["网络/系统单元名称"] = fallback
    if not values["报告年度"]:
        year = re.search(r"(20\d{2})年度", compact)
        values["报告年度"] = f"{year.group(1)}年度" if year else ""

    if "未开展新建" in compact or "未发生新建" in compact:
        values["是否发生新建、改建或扩建"] = "否"
    elif tail:
        values["是否发生新建、改建或扩建"] = "是"

    marker_match = re.search(r"(?:均)?已同步配套|原有网络安全措施", tail)
    if marker_match:
        before = tail[: marker_match.start()].rstrip("，,")
        after = tail[marker_match.start() :].strip("，,")
        values["建设变更说明"] = before
        values["网络安全措施落实情况"] = after.rstrip("。")
    else:
        values["建设变更说明"] = tail.rstrip("。")

    if "详细信息见附件" in compact:
        values["附件说明"] = "详细信息见附件"
    return values


def _unique_headers(row: list[str]) -> list[str]:
    result: list[str] = []
    counts: Counter[str] = Counter()
    for index, value in enumerate(row, start=1):
        base = clean_text(value) or f"第{index}列"
        counts[base] += 1
        result.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return result


def table_to_dict(table: TableData) -> dict:
    matrix = [list(row) for row in table.matrix]
    header_index = next((index for index, row in enumerate(matrix) if any(row)), None)
    headers = _unique_headers(matrix[header_index]) if header_index is not None else []
    records: list[dict] = []
    if header_index is not None:
        for row_number, row in enumerate(matrix[header_index + 1 :], start=header_index + 2):
            padded = row + [""] * max(0, len(headers) - len(row))
            records.append({"行号": row_number, **dict(zip(headers, padded))})

    merges: list[dict] = []
    for row in table.rows:
        for cell in row:
            if cell.row_span > 1 or cell.column_span > 1:
                merges.append(
                    {
                        "起始行": cell.row_index + 1,
                        "起始列": cell.column_start + 1,
                        "跨行数": cell.row_span,
                        "跨列数": cell.column_span,
                    }
                )
    table_images = [
        {
            **item.image.to_dict(),
            "所在行": item.row_index + 1,
            "所在列": item.column_start + 1,
        }
        for item in table.images
    ]
    return {
        "类型": "表格",
        "表格编号": f"T{table.table_index + 1:03d}",
        "字段": headers,
        "记录": records,
        "原始数据": matrix,
        "合并单元格": merges,
        "图片": table_images,
    }


def _ordered_content(structure: DocumentStructure) -> list[dict]:
    content: list[dict] = []
    for block in structure.blocks:
        if block.kind == "paragraph":
            if block.parts:
                for part in block.parts:
                    if part.kind == "text" and part.text:
                        content.append({"类型": "段落", "文本": part.text})
                    elif part.kind == "image" and part.image is not None:
                        content.append({"类型": "图片", **part.image.to_dict()})
            elif block.text:
                content.append({"类型": "段落", "文本": block.text})
        elif block.kind == "table" and block.table is not None:
            content.append(table_to_dict(block.table))
    return content


def parse_report(structure: DocumentStructure, document_name: str) -> tuple[dict, list[str]]:
    paragraphs = _paragraphs(structure)
    title = _title(paragraphs)
    body = _body_sentence(paragraphs)
    fields = _business_from_body(body, title)

    conclusion = next((text for text in paragraphs if "特此报告" in _compact(text)), "")
    company = ""
    for index, text in enumerate(paragraphs):
        if "公司名称" in _compact(text) and "盖章" in _compact(text):
            company = next((value for value in paragraphs[index + 1 :] if value), "")
            break
    report_date = next(
        (value for value in (_normal_date(text) for text in reversed(paragraphs)) if value),
        "",
    )

    result = {
        "文档名称": document_name,
        "报告标题": title,
        **fields,
        "报告结论": conclusion,
        "公司名称（盖章）": company,
        "报告日期": report_date,
        "报告正文原文": body,
        "内容": _ordered_content(structure),
        "图片": [image.to_dict() for image in structure.images],
        "表格": [table_to_dict(table) for table in structure.tables],
    }

    warnings: list[str] = []
    required = ["报告标题", "网络/系统单元名称", "报告年度", "报告日期"]
    for field in required:
        if not result[field]:
            warnings.append(f"未识别字段：{field}")
    if not body:
        warnings.append("未识别三同步报告正文")
    if not paragraphs:
        warnings.append("正文没有可用段落")
    file_name_compact = _compact(document_name)
    change_status = result["是否发生新建、改建或扩建"]
    if "未发生新建" in file_name_compact and change_status == "是":
        warnings.append("文件名标注未发生建设变更，但报告正文解析结果为发生建设变更")
    if (
        "发生新建" in file_name_compact
        and "未发生新建" not in file_name_compact
        and change_status == "否"
    ):
        warnings.append("文件名标注发生建设变更，但报告正文写明未开展新建、扩建或改建")
    if result["附件说明"] and not result["图片"] and not result["表格"]:
        warnings.append("正文提及附件，但当前 Word 正文内没有图片或表格；附件可能为外部文件")
    return result, warnings


def content_statistics(result: dict) -> dict:
    return {
        "段落数": sum(1 for item in result.get("内容", []) if item.get("类型") == "段落"),
        "图片数": len(result.get("图片", [])),
        "表格数": len(result.get("表格", [])),
    }
