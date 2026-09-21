# -*- coding: utf-8 -*-
"""Adapt :mod:`pdf_light` output to the existing Word-source data model."""
from __future__ import annotations

from pathlib import Path
from types import ModuleType

from .pdf_light import PdfDocument, extract_pdf


def _contains(outer: tuple[float, float, float, float], inner: tuple[float, float, float, float]) -> bool:
    cx = (inner[0] + inner[2]) / 2
    cy = (inner[1] + inner[3]) / 2
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def to_word_structure(pdf: PdfDocument, source_module: ModuleType):
    image_refs = {
        item.file_name: source_module.ImageRef(
            item.file_name.rsplit(".", 1)[0],
            item.file_name,
            item.relative_path,
            f"PDF第{item.page}页图片",
        )
        for item in pdf.images
    }
    tables = []
    table_map = {}
    for table_index, item in enumerate(pdf.tables):
        rows = []
        for row_index, values in enumerate(item.matrix):
            row = [
                source_module.TableCell(
                    column_start=column,
                    column_span=1,
                    row_merge="",
                    text=value,
                    row_index=row_index,
                    row_span=1,
                    paragraphs=[value] if value else [],
                )
                for column, value in enumerate(values)
            ]
            rows.append(row)
        occurrences = []
        for pdf_image in pdf.images:
            if pdf_image.page == item.page and _contains(item.bbox, pdf_image.bbox):
                # Map image centre onto the closest matrix row/column.  PDF has no
                # OOXML merge metadata, so this remains an evidence position only.
                rel_x = ((pdf_image.bbox[0] + pdf_image.bbox[2]) / 2 - item.bbox[0]) / max(1, item.bbox[2] - item.bbox[0])
                rel_y = ((pdf_image.bbox[1] + pdf_image.bbox[3]) / 2 - item.bbox[1]) / max(1, item.bbox[3] - item.bbox[1])
                column = min(max(0, int(rel_x * max(1, len(item.matrix[0])))), max(0, len(item.matrix[0]) - 1))
                row = min(max(0, int(rel_y * max(1, len(item.matrix)))), max(0, len(item.matrix) - 1))
                occurrences.append(source_module.TableImage(row, column, image_refs[pdf_image.file_name]))
        table_data = source_module.TableData(
            table_index,
            rows,
            [list(row) for row in item.matrix],
            max((len(row) for row in item.matrix), default=0),
            occurrences,
        )
        tables.append(table_data)
        table_map[id(item)] = table_data

    blocks = []
    for block_index, item in enumerate(pdf.blocks):
        if item.kind == "paragraph" and item.line is not None:
            line = item.line
            style = f"Heading {line.heading_level}" if line.heading_level else ""
            blocks.append(
                source_module.DocumentBlock(
                    block_index=block_index,
                    kind="paragraph",
                    text=line.text,
                    style_id=style.replace(" ", ""),
                    style_name=style,
                    parts=[source_module.ParagraphPart(kind="text", text=line.text)],
                )
            )
        elif item.kind == "table" and item.table is not None:
            blocks.append(
                source_module.DocumentBlock(
                    block_index=block_index,
                    kind="table",
                    table=table_map[id(item.table)],
                )
            )
        elif item.kind == "image" and item.image is not None:
            ref = image_refs[item.image.file_name]
            # Images captured inside a table are emitted by the table occurrence.
            if any(_contains(table.bbox, item.image.bbox) and table.page == item.page for table in pdf.tables):
                continue
            blocks.append(
                source_module.DocumentBlock(
                    block_index=block_index,
                    kind="paragraph",
                    images=[ref],
                    parts=[source_module.ParagraphPart(kind="image", image=ref)],
                )
            )
    for index, block in enumerate(blocks):
        block.block_index = index
    return source_module.DocumentStructure(
        blocks=blocks,
        tables=tables,
        images=list(image_refs.values()),
        paragraph_count=sum(1 for block in blocks if block.kind == "paragraph"),
    )


def extract_as_word_structure(
    pdf_path: str | Path,
    assets_dir: str | Path,
    assets_relative_dir: str,
    source_module: ModuleType,
    **pdf_options,
):
    document = extract_pdf(pdf_path, assets_dir, assets_relative_dir, **pdf_options)
    return to_word_structure(document, source_module), document
