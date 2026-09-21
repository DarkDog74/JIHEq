# -*- coding: utf-8 -*-
"""Pure-scan PDF adapter for 三同步 reports."""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pdfplumber

from word_source import DocumentBlock, DocumentStructure, ImageRef, ParagraphPart

SHARED_ROOT = Path(__file__).resolve().parents[1]
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))
from _shared.pdf_light import PdfDocument, clean_text, extract_pdf


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", clean_text(text))


def _best_lines(document: PdfDocument) -> list[str]:
    values = [
        block.line.text
        for block in document.blocks
        if block.kind == "paragraph" and block.line is not None and block.line.text
    ]
    # OCR two-pass results may differ only in punctuation; keep the more complete
    # variant when one normalized string contains the other.
    result: list[str] = []
    for text in values:
        compact = _compact(text)
        if not compact:
            continue
        replaced = False
        for index, existing in enumerate(result):
            old = _compact(existing)
            if compact in old:
                replaced = True
                break
            if old in compact:
                result[index] = text
                replaced = True
                break
        if not replaced:
            result.append(text)
    return result


def _logical_paragraphs(document: PdfDocument) -> list[str]:
    lines = _best_lines(document)
    title = next((line for line in lines if "三同步" in _compact(line) and "报告" in _compact(line)), "")
    if not title:
        prefix: list[str] = []
        for line in lines:
            if "我公司" in _compact(line):
                break
            prefix.append(line)
        joined_prefix = clean_text("".join(prefix))
        if "三同步" in _compact(joined_prefix) and "报告" in _compact(joined_prefix):
            title = joined_prefix
    conclusion = next((line for line in lines if "特此报告" in _compact(line)), "")
    company_label = next((line for line in lines if "公司名称" in _compact(line) and "盖章" in _compact(line)), "")
    date = next((line for line in reversed(lines) if re.search(r"20\d{2}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日", line)), "")

    body_parts: list[str] = []
    in_body = False
    for line in lines:
        compact = _compact(line)
        if "我公司" in compact:
            in_body = True
        if in_body and "特此报告" not in compact:
            body_parts.append(line)
        if in_body and ("有效落实" in compact or "见附件" in compact):
            break
    body = clean_text("".join(body_parts))

    company_candidates = [
        line for line in lines
        if "中国移动通信集团浙江有限公司" in _compact(line)
        and line != title
        and line not in body_parts
        and "三同步" not in _compact(line)
    ]
    company = max(company_candidates, key=len, default="")
    ordered = [title, body, conclusion, company_label, company, date]
    return [clean_text(value) for value in ordered if clean_text(value)]


def extract_pdf_report(
    pdf_path: str | Path,
    assets_dir: str | Path,
    assets_relative_dir: str,
) -> tuple[DocumentStructure, PdfDocument]:
    source = Path(pdf_path).expanduser().resolve()
    staged = Path(assets_dir)
    staged.mkdir(parents=True, exist_ok=True)
    document = extract_pdf(source, staged, assets_relative_dir, extract_images=False, force_ocr=True)
    if not document.ocr_pages:
        document.warnings.append("PDF未进入OCR页，需人工确认是否为预期扫描件")

    image_name = "IMG001.jpg"
    image_path = staged / image_name
    with pdfplumber.open(source) as pdf:
        if not pdf.pages:
            raise ValueError("PDF没有页面")
        pdf.pages[0].to_image(resolution=180, antialias=True).original.convert("RGB").save(
            image_path, "JPEG", quality=90, optimize=True
        )
    image = ImageRef("IMG001", image_name, f"{assets_relative_dir}/{image_name}", "原始整页扫描图（第1页）")

    paragraphs = _logical_paragraphs(document)
    blocks = [
        DocumentBlock(
            block_index=index,
            kind="paragraph",
            text=text,
            parts=[ParagraphPart(kind="text", text=text)],
        )
        for index, text in enumerate(paragraphs)
    ]
    blocks.append(
        DocumentBlock(
            block_index=len(blocks),
            kind="paragraph",
            parts=[ParagraphPart(kind="image", image=image)],
        )
    )
    return DocumentStructure(blocks, [], [image], len(paragraphs) + 1), document
