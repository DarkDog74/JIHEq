# -*- coding: utf-8 -*-
"""PDF fallback for the fixed compliance-assessment table template."""
from __future__ import annotations

import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pdfplumber

from business_parser import CHECK_FIELDS, ParsedBusiness, SheetData, normal_header, parse_sheets
from image_extractor import RawImageOccurrence

SHARED_ROOT = Path(__file__).resolve().parents[1]
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))
from _shared.pdf_light import PdfDocument, clean_text, extract_pdf
from _shared.mineru_source import extract_mineru


@dataclass
class ParsedPdfAssessment:
    business: ParsedBusiness
    images: list[RawImageOccurrence]
    document: PdfDocument
    warnings: list[str]


def _table_settings() -> dict:
    return {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "snap_tolerance": 3,
        "join_tolerance": 3,
        "intersection_tolerance": 4,
    }


def _clean_matrix(raw_matrix) -> list[list[str]]:
    matrix = [[clean_text(value) for value in row] for row in (raw_matrix or [])]
    width = max((len(row) for row in matrix), default=0)
    return [row + [""] * (width - len(row)) for row in matrix]


def _header(matrix: list[list[str]]) -> tuple[int, dict[str, int]] | None:
    aliases = {normal_header(field): field for field in CHECK_FIELDS}
    for row_index, row in enumerate(matrix[:8]):
        mapping = {}
        for column, value in enumerate(row):
            canonical = aliases.get(normal_header(value))
            if canonical and canonical not in mapping:
                mapping[canonical] = column
        if {"序号", "检查点", "答案"}.issubset(mapping):
            return row_index, mapping
    return None


def _vertical_merges(matrix: list[list[str]], header_row: int, mapping: dict[str, int]) -> list[tuple[int, int, int, int]]:
    ranges: list[tuple[int, int, int, int]] = []
    for field in ("适用范围", "检查类型", "检查内容", "适用网络属性"):
        column = mapping.get(field)
        if column is None:
            continue
        index = header_row + 1
        while index < len(matrix):
            if not matrix[index][column]:
                index += 1
                continue
            end = index + 1
            while end < len(matrix) and not matrix[end][column] and not _header([matrix[end]]):
                # A group-label row in the serial-number column ends inheritance.
                serial = matrix[end][mapping["序号"]]
                if serial and not re.fullmatch(r"[0-9A-Za-z]+(?:[-－][0-9A-Za-z]+)+", serial):
                    break
                end += 1
            if end > index + 1:
                ranges.append((index, end, column, column + 1))
            index = end
    return ranges


def _bbox_overlaps(a, b) -> bool:
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _crop_payload(page, bbox) -> bytes:
    safe_bbox = (
        max(float(page.bbox[0]), float(bbox[0])),
        max(float(page.bbox[1]), float(bbox[1])),
        min(float(page.bbox[2]), float(bbox[2])),
        min(float(page.bbox[3]), float(bbox[3])),
    )
    if safe_bbox[2] <= safe_bbox[0] or safe_bbox[3] <= safe_bbox[1]:
        raise ValueError(f"无效PDF裁剪区域: {bbox}")
    image = page.crop(safe_bbox).to_image(resolution=180, antialias=True).original.convert("RGB")
    stream = io.BytesIO()
    image.save(stream, "PNG", optimize=True)
    return stream.getvalue()


def parse_pdf_assessment(pdf_path: str | Path, work_dir: str | Path) -> ParsedPdfAssessment:
    source = Path(pdf_path).expanduser().resolve()
    work = Path(work_dir)
    document = extract_pdf(
        source,
        work / "layout-assets",
        "unused.assets",
        extract_images=False,
        ocr_missing=False,
    )
    sheets: list[SheetData] = []
    occurrences: list[RawImageOccurrence] = []
    warnings: list[str] = []
    combined: list[list[str]] = []
    combined_header_row = -1
    combined_mapping: dict[str, int] | None = None
    with pdfplumber.open(source) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            page_images = [
                (float(item.get("x0", 0)), float(item.get("top", 0)), float(item.get("x1", 0)), float(item.get("bottom", 0)))
                for item in page.images
            ]
            page_had_check_table = False
            for table_number, table in enumerate(page.find_tables(table_settings=_table_settings()), start=1):
                matrix = _clean_matrix(table.extract())
                found = _header(matrix)
                if found is not None:
                    header_row, mapping = found
                    if combined_mapping is None:
                        combined.extend(matrix[: header_row + 1])
                        combined_header_row = len(combined) - 1
                        combined_mapping = mapping
                    data_start = header_row + 1
                elif combined_mapping is not None and matrix and len(matrix[0]) > max(combined_mapping.values()):
                    # Most exported evaluation PDFs repeat the column grid but omit
                    # the labels after page one. Continue under the first fixed header.
                    mapping = combined_mapping
                    data_start = 0
                else:
                    continue
                page_had_check_table = True
                global_start = len(combined)
                combined.extend(matrix[data_start:])
                known_columns = set(mapping.values())
                for row_index in range(data_start, len(matrix)):
                    serial = matrix[row_index][mapping["序号"]] if mapping["序号"] < len(matrix[row_index]) else ""
                    if not re.fullmatch(r"[0-9A-Za-z]+(?:[-－][0-9A-Za-z]+)+", serial):
                        continue
                    if row_index >= len(table.rows):
                        continue
                    row_cells = table.rows[row_index].cells
                    for column, cell_bbox in enumerate(row_cells):
                        if column in known_columns or cell_bbox is None:
                            continue
                        visible = any(_bbox_overlaps(cell_bbox, image_bbox) for image_bbox in page_images)
                        cell_text = matrix[row_index][column] if column < len(matrix[row_index]) else ""
                        # In these PDFs a nonempty extra column is evidence text, while
                        # an image overlap is a visible screenshot. Crop only the latter.
                        if not visible:
                            continue
                        occurrences.append(
                            RawImageOccurrence(
                                sheet_index=0,
                                row=global_start + (row_index - data_start) + 1,
                                column=column + 1,
                                source=f"PDF第{page_number}页佐证单元格",
                                payload=_crop_payload(page, cell_bbox),
                                extension=".png",
                                alt_text=cell_text,
                            )
                        )
            if not page_had_check_table and page_images:
                for image_number, image_bbox in enumerate(page_images, start=1):
                    area = (image_bbox[2] - image_bbox[0]) * (image_bbox[3] - image_bbox[1])
                    if area < float(page.width) * float(page.height) * 0.02:
                        continue
                    occurrences.append(
                        RawImageOccurrence(
                            sheet_index=0,
                            row=0,
                            column=0,
                            source=f"PDF第{page_number}页未绑定佐证图片{image_number}",
                            payload=_crop_payload(page, image_bbox),
                            extension=".png",
                            alt_text="PDF中无表格坐标可关联的佐证图片",
                        )
                    )
                    warnings.append(f"第{page_number}页佐证图片无法可靠绑定到检查项，已保存在其他图片")
    if combined_mapping is not None and combined:
        sheets.append(
            SheetData(
                "PDF合并检查项目",
                combined,
                _vertical_merges(combined, combined_header_row, combined_mapping),
            )
        )
    if not sheets:
        raise ValueError(f"PDF中未找到符合性评测表固定表头：{source.name}")
    parsed = parse_sheets(sheets, str(source))
    valid_records = [
        record
        for record in parsed.result["检查项目"]
        if re.fullmatch(r"[0-9A-Za-z]+(?:[-－][0-9A-Za-z]+)+", re.sub(r"\s+", "", record.get("序号", "")))
    ]
    valid_ids = {id(record) for record in valid_records}
    parsed.result["检查项目"] = valid_records
    parsed.row_records = {
        position: record
        for position, record in parsed.row_records.items()
        if id(record) in valid_ids
    }
    if not parsed.result["评测表名称"]:
        parsed.result["评测表名称"] = source.stem
    if document.ocr_pages:
        warnings.append(f"文字层不足，已对第{document.ocr_pages}页执行本地OCR")
    return ParsedPdfAssessment(parsed, occurrences, document, warnings)


def parse_mineru_assessment(pdf_path: str | Path, work_dir: str | Path) -> ParsedPdfAssessment:
    source = Path(pdf_path).expanduser().resolve()
    work = Path(work_dir)
    mineru_assets = work / "mineru-assets"
    document = extract_mineru(source, work, mineru_assets, "unused.assets")
    combined: list[list[str]] = []
    mapping: dict[str, int] | None = None
    header_row = -1
    for table in document.tables:
        found = _header(table.matrix)
        if found is not None:
            local_header, local_mapping = found
            if mapping is None:
                combined.extend(table.matrix[: local_header + 1])
                mapping = local_mapping
                header_row = len(combined) - 1
            combined.extend(table.matrix[local_header + 1 :])
        elif mapping is not None and table.matrix and len(table.matrix[0]) > max(mapping.values()):
            combined.extend(table.matrix)
    if mapping is None or not combined:
        raise ValueError(f"MinerU结果中未找到符合性评测表固定表头：{source.name}")
    sheet = SheetData("MinerU合并检查项目", combined, _vertical_merges(combined, header_row, mapping))
    parsed = parse_sheets([sheet], str(source))
    valid_records = [
        record
        for record in parsed.result["检查项目"]
        if re.fullmatch(r"[0-9A-Za-z]+(?:[-－][0-9A-Za-z]+)+", re.sub(r"\s+", "", record.get("序号", "")))
    ]
    valid_ids = {id(record) for record in valid_records}
    parsed.result["检查项目"] = valid_records
    parsed.row_records = {
        position: record for position, record in parsed.row_records.items() if id(record) in valid_ids
    }
    if not parsed.result["评测表名称"]:
        parsed.result["评测表名称"] = source.stem
    occurrences = [
        RawImageOccurrence(
            sheet_index=0,
            row=0,
            column=0,
            source=f"MinerU第{image.page}页未绑定佐证图片",
            payload=image.path.read_bytes(),
            extension=image.path.suffix or ".png",
            alt_text="MinerU识别的佐证图片；当前无可靠单元格坐标",
        )
        for image in document.images
    ]
    warnings = (
        [f"{len(occurrences)}张MinerU佐证图片无法可靠绑定到检查项，已保存在其他图片"]
        if occurrences else []
    )
    return ParsedPdfAssessment(parsed, occurrences, document, warnings)
