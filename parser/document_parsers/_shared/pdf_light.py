# -*- coding: utf-8 -*-
"""Local, lightweight PDF layout extraction used by the category adapters.

The module intentionally has no dependency on Tika, Java, a network service or
MinerU.  It prefers the PDF text layer and vector table geometry, and only uses
RapidOCR for pages/regions whose text is not otherwise available.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import cv2
import numpy as np
import pdfplumber


class PdfSourceError(RuntimeError):
    pass


@dataclass
class PdfLine:
    page: int
    top: float
    bottom: float
    x0: float
    x1: float
    text: str
    font_size: float = 0.0
    font_name: str = ""
    heading_level: int | None = None
    confidence: float = 1.0
    source: str = "pdf_text"


@dataclass
class PdfCell:
    row: int
    column: int
    text: str
    bbox: tuple[float, float, float, float] | None = None
    row_span: int = 1
    column_span: int = 1


@dataclass
class PdfTable:
    page: int
    top: float
    bottom: float
    bbox: tuple[float, float, float, float]
    matrix: list[list[str]]
    cells: list[PdfCell] = field(default_factory=list)


@dataclass
class PdfImage:
    page: int
    top: float
    bottom: float
    bbox: tuple[float, float, float, float]
    file_name: str
    path: Path
    relative_path: str
    kind: str = "image"


@dataclass
class PdfBlock:
    kind: str
    page: int
    top: float
    line: PdfLine | None = None
    table: PdfTable | None = None
    image: PdfImage | None = None


@dataclass
class PdfDocument:
    source: Path
    sha256: str
    page_count: int
    blocks: list[PdfBlock]
    tables: list[PdfTable]
    images: list[PdfImage]
    ocr_pages: list[int]
    warnings: list[str]
    mean_ocr_confidence: float | None


_OCR_ENGINE = None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean_text(value: str | None) -> str:
    text = (value or "").replace("\x00", "").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


def _get_ocr_engine():
    global _OCR_ENGINE
    if _OCR_ENGINE is None:
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise PdfSourceError(
                "PDF缺少文字层且未安装 RapidOCR；请安装 requirements-pdf.txt"
            ) from exc
        _OCR_ENGINE = RapidOCR()
    return _OCR_ENGINE


def _rapidocr(image: np.ndarray) -> list[tuple[list[list[float]], str, float]]:
    result = _get_ocr_engine()(image)
    if result is None or result.boxes is None:
        return []
    boxes = result.boxes.tolist() if hasattr(result.boxes, "tolist") else result.boxes
    texts = list(result.txts or [])
    scores = list(result.scores or [])
    return [
        (box, clean_text(text), float(score))
        for box, text, score in zip(boxes, texts, scores)
        if clean_text(text)
    ]


def ocr_image_two_pass(image: np.ndarray) -> list[tuple[list[list[float]], str, float]]:
    """OCR colour image plus a red-stamp-suppressed black-text pass."""
    colour = _rapidocr(image)
    if image.ndim == 3:
        binary = np.where(np.max(image, axis=2) < 175, 0, 255).astype(np.uint8)
    else:
        binary = cv2.threshold(image, 175, 255, cv2.THRESH_BINARY)[1]
    black = _rapidocr(binary)
    best: dict[str, tuple[list[list[float]], str, float]] = {}
    for item in colour + black:
        key = re.sub(r"\s+", "", item[1])
        if key and (key not in best or item[2] > best[key][2]):
            best[key] = item
    return sorted(best.values(), key=lambda item: (min(p[1] for p in item[0]), min(p[0] for p in item[0])))


def _heading_level(text: str, size: float, body_size: float) -> int | None:
    compact = clean_text(text)
    if re.search(r"[.．·]{4,}", compact):
        return None
    match = re.match(r"^([0-9]+(?:[.．][0-9]+){0,2})(?:\s+|[、.．])(?!\d)", compact)
    if match:
        depth = match.group(1).replace("．", ".").count(".") + 1
        return min(depth, 3)
    if re.match(r"^[一二三四五六七八九十]+[、.．]\s*\S+", compact):
        return 1
    # Font size alone is deliberately not a heading signal: report covers and
    # table-of-contents entries frequently use the same large fonts.
    return None


def _words_to_lines(page_number: int, words: list[dict]) -> list[PdfLine]:
    groups: list[list[dict]] = []
    for word in sorted(words, key=lambda item: (round(float(item["top"]), 1), float(item["x0"]))):
        if not groups or abs(float(word["top"]) - float(groups[-1][0]["top"])) > 3.0:
            groups.append([word])
        else:
            groups[-1].append(word)
    sizes = [float(word.get("size") or 0) for word in words if float(word.get("size") or 0) > 0]
    body_size = Counter(round(size, 1) for size in sizes).most_common(1)[0][0] if sizes else 10.5
    result: list[PdfLine] = []
    for group in groups:
        group.sort(key=lambda item: float(item["x0"]))
        chunks: list[str] = []
        previous_x1: float | None = None
        for word in group:
            text = clean_text(str(word.get("text", "")))
            if not text:
                continue
            x0 = float(word["x0"])
            if previous_x1 is not None and x0 - previous_x1 > max(2.0, float(word.get("size") or 10) * 0.2):
                chunks.append(" ")
            chunks.append(text)
            previous_x1 = float(word["x1"])
        text = clean_text("".join(chunks))
        if not text:
            continue
        size = max(float(item.get("size") or 0) for item in group)
        font = str(max(group, key=lambda item: float(item.get("size") or 0)).get("fontname") or "")
        result.append(
            PdfLine(
                page_number,
                min(float(item["top"]) for item in group),
                max(float(item["bottom"]) for item in group),
                min(float(item["x0"]) for item in group),
                max(float(item["x1"]) for item in group),
                text,
                size,
                font,
                _heading_level(text, size, body_size),
            )
        )
    return result


def _inside(bbox: tuple[float, float, float, float], other: tuple[float, float, float, float]) -> bool:
    x0, top, x1, bottom = bbox
    ox0, otop, ox1, obottom = other
    cx, cy = (x0 + x1) / 2, (top + bottom) / 2
    return ox0 <= cx <= ox1 and otop <= cy <= obottom


def _extract_tables(page, page_number: int) -> list[PdfTable]:
    result: list[PdfTable] = []
    try:
        found = page.find_tables(
            table_settings={
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
                "snap_tolerance": 3,
                "join_tolerance": 3,
                "intersection_tolerance": 4,
            }
        )
    except Exception:
        found = []
    for raw in found:
        matrix = [
            [clean_text(cell) for cell in row]
            for row in (raw.extract() or [])
        ]
        if not matrix or max((len(row) for row in matrix), default=0) < 2:
            continue
        columns = max(len(row) for row in matrix)
        matrix = [row + [""] * (columns - len(row)) for row in matrix]
        cells = [PdfCell(r, c, value) for r, row in enumerate(matrix) for c, value in enumerate(row)]
        x0, top, x1, bottom = map(float, raw.bbox)
        result.append(PdfTable(page_number, top, bottom, (x0, top, x1, bottom), matrix, cells))
    return result


def _render_page(page, resolution: int = 180) -> np.ndarray:
    return np.asarray(page.to_image(resolution=resolution, antialias=True).original.convert("RGB"))


def _save_crop(
    page,
    bbox: tuple[float, float, float, float],
    target: Path,
    resolution: int = 180,
) -> None:
    image = page.crop(bbox).to_image(resolution=resolution, antialias=True).original.convert("RGB")
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG")


def _image_regions(page) -> list[tuple[float, float, float, float]]:
    width, height = float(page.width), float(page.height)
    regions: list[tuple[float, float, float, float]] = []
    for item in page.images:
        bbox = (
            max(0.0, float(item.get("x0", 0))),
            max(0.0, float(item.get("top", 0))),
            min(width, float(item.get("x1", width))),
            min(height, float(item.get("bottom", height))),
        )
        area = max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1])
        if area >= width * height * 0.008 and bbox[2] > bbox[0] and bbox[3] > bbox[1]:
            if not any(_inside(bbox, existing) and area < (existing[2]-existing[0])*(existing[3]-existing[1]) for existing in regions):
                regions.append(bbox)
    return regions


def _ocr_page(
    page,
    page_number: int,
    *,
    resolution: int = 180,
    two_pass: bool = True,
) -> tuple[list[PdfLine], list[float]]:
    image = _render_page(page, resolution)
    scale_x = float(page.width) / image.shape[1]
    scale_y = float(page.height) / image.shape[0]
    lines: list[PdfLine] = []
    scores: list[float] = []
    ocr_values = ocr_image_two_pass(image) if two_pass else _rapidocr(image)
    for box, text, score in ocr_values:
        x0 = min(point[0] for point in box) * scale_x
        x1 = max(point[0] for point in box) * scale_x
        top = min(point[1] for point in box) * scale_y
        bottom = max(point[1] for point in box) * scale_y
        lines.append(PdfLine(page_number, top, bottom, x0, x1, text, 0, "OCR", None, score, "local_ocr"))
        scores.append(score)
    return lines, scores


def _drop_repeated_headers(lines_by_page: list[list[PdfLine]], page_heights: list[float]) -> None:
    candidates: Counter[str] = Counter()
    for lines, height in zip(lines_by_page, page_heights):
        seen: set[str] = set()
        for line in lines:
            if line.top < height * 0.09 or line.bottom > height * 0.91:
                key = re.sub(r"\s+", "", line.text)
                if key:
                    seen.add(key)
        candidates.update(seen)
    threshold = max(2, int(len(lines_by_page) * 0.45 + 0.5))
    repeated = {key for key, count in candidates.items() if count >= threshold}
    for index, (lines, height) in enumerate(zip(lines_by_page, page_heights)):
        lines_by_page[index] = [
            line for line in lines
            if not (
                re.sub(r"\s+", "", line.text) in repeated
                or ((line.top < height * 0.07 or line.bottom > height * 0.93) and re.fullmatch(r"[-—－\s]*\d+[-—－\s]*", line.text))
            )
        ]


def _merge_continued_tables(
    tables_by_page: list[list[PdfTable]],
    page_heights: list[float],
) -> list[PdfTable]:
    ordered = sorted((table for page in tables_by_page for table in page), key=lambda item: (item.page, item.top))
    merged: list[PdfTable] = []
    removed: set[int] = set()
    for table in ordered:
        if not merged:
            merged.append(table)
            continue
        previous = merged[-1]
        previous_height = page_heights[previous.page - 1]
        current_height = page_heights[table.page - 1]
        same_width = len(previous.matrix[0]) == len(table.matrix[0]) if previous.matrix and table.matrix else False
        continuation = (
            table.page == previous.page + 1
            and same_width
            and previous.bottom > previous_height * 0.84
            and table.top < current_height * 0.16
        )
        if not continuation:
            merged.append(table)
            continue
        offset = len(previous.matrix)
        repeated_header = (
            previous.matrix
            and table.matrix
            and [re.sub(r"\s+", "", value) for value in previous.matrix[0]]
            == [re.sub(r"\s+", "", value) for value in table.matrix[0]]
        )
        skip = 1 if repeated_header else 0
        previous.matrix.extend(table.matrix[skip:])
        previous.cells.extend(
            PdfCell(
                cell.row + offset - skip,
                cell.column,
                cell.text,
                cell.bbox,
                cell.row_span,
                cell.column_span,
            )
            for cell in table.cells
            if cell.row >= skip
        )
        removed.add(id(table))
    if removed:
        for page_index, values in enumerate(tables_by_page):
            tables_by_page[page_index] = [table for table in values if id(table) not in removed]
    return merged


def extract_pdf(
    pdf_path: str | Path,
    assets_dir: str | Path,
    assets_relative_dir: str,
    *,
    extract_images: bool = True,
    force_ocr: bool = False,
    ocr_missing: bool = True,
    ocr_heading_terms: Iterable[str] | None = None,
    ocr_heading_page_limit: int = 15,
) -> PdfDocument:
    source = Path(pdf_path).expanduser().resolve()
    if not source.is_file() or source.suffix.lower() != ".pdf":
        raise PdfSourceError(f"输入不是可用 PDF: {source}")
    target_dir = Path(assets_dir)
    warnings: list[str] = []
    all_tables: list[PdfTable] = []
    all_images: list[PdfImage] = []
    lines_by_page: list[list[PdfLine]] = []
    tables_by_page: list[list[PdfTable]] = []
    images_by_page: list[list[PdfImage]] = []
    page_heights: list[float] = []
    ocr_pages: list[int] = []
    ocr_scores: list[float] = []
    try:
        document = pdfplumber.open(source)
    except Exception as exc:
        raise PdfSourceError(f"PDF 无法打开或已损坏: {source.name}: {exc}") from exc
    try:
        if getattr(document, "is_encrypted", False):
            warnings.append("PDF 标记为加密；已使用空密码成功读取")
        for page_number, page in enumerate(document.pages, start=1):
            page_heights.append(float(page.height))
            try:
                words = page.extract_words(extra_attrs=["fontname", "size"], keep_blank_chars=False)
            except Exception:
                words = page.extract_words(keep_blank_chars=False) or []
            native_lines = _words_to_lines(page_number, words or [])
            native_chars = sum(len(re.sub(r"\s+", "", item.text)) for item in native_lines)
            if force_ocr or (ocr_missing and native_chars < 25):
                lines, scores = _ocr_page(page, page_number)
                if lines:
                    native_lines = lines
                    ocr_pages.append(page_number)
                    ocr_scores.extend(scores)
                elif native_chars < 25:
                    warnings.append(f"第{page_number}页无文字层且 OCR 未识别到文字")
            elif ocr_heading_terms and page_number <= ocr_heading_page_limit:
                normalized_native = {re.sub(r"[\s：:]", "", line.text).strip(".．·") for line in native_lines}
                missing_terms = [
                    term for term in ocr_heading_terms
                    if not any(
                        value == re.sub(r"[\s：:]", "", term)
                        or (
                            value.endswith(re.sub(r"[\s：:]", "", term))
                            and len(value) <= len(re.sub(r"[\s：:]", "", term)) + 35
                        )
                        for value in normalized_native
                    )
                ]
                if missing_terms:
                    ocr_lines, scores = _ocr_page(
                        page,
                        page_number,
                        resolution=120,
                        two_pass=False,
                    )
                    recovered = []
                    for line in ocr_lines:
                        value = re.sub(r"[\s：:]", "", line.text).strip(".．·")
                        if any(
                            value == re.sub(r"[\s：:]", "", term)
                            or value.endswith(re.sub(r"[\s：:]", "", term))
                            for term in missing_terms
                        ):
                            if value not in normalized_native:
                                recovered.append(line)
                    if recovered:
                        native_lines.extend(recovered)
                        native_lines.sort(key=lambda item: (item.top, item.x0))
                        ocr_pages.append(page_number)
                        ocr_scores.extend(line.confidence for line in recovered)
            page_tables = _extract_tables(page, page_number)
            all_tables.extend(page_tables)
            tables_by_page.append(page_tables)
            page_images: list[PdfImage] = []
            if extract_images:
                for bbox in _image_regions(page):
                    file_name = f"IMG{len(all_images)+1:03d}.png"
                    path = target_dir / file_name
                    try:
                        _save_crop(page, bbox, path)
                    except Exception as exc:
                        warnings.append(f"第{page_number}页图片区域提取失败: {exc}")
                        continue
                    image = PdfImage(
                        page_number, bbox[1], bbox[3], bbox, file_name, path,
                        f"{assets_relative_dir}/{file_name}",
                    )
                    all_images.append(image)
                    page_images.append(image)
            lines_by_page.append(native_lines)
            images_by_page.append(page_images)
    finally:
        document.close()
    all_tables = _merge_continued_tables(tables_by_page, page_heights)
    _drop_repeated_headers(lines_by_page, page_heights)
    blocks: list[PdfBlock] = []
    for page_number, (lines, tables, images) in enumerate(zip(lines_by_page, tables_by_page, images_by_page), start=1):
        table_boxes = [table.bbox for table in tables]
        image_boxes = [item.bbox for item in images]
        for line in lines:
            bbox = (line.x0, line.top, line.x1, line.bottom)
            if not any(_inside(bbox, table_bbox) for table_bbox in table_boxes):
                blocks.append(PdfBlock("paragraph", page_number, line.top, line=line))
        blocks.extend(PdfBlock("table", page_number, table.top, table=table) for table in tables)
        for image in images:
            # A full-page scan already contributes OCR paragraphs, but its page image is
            # still retained as evidence and ordered after the page text.
            blocks.append(PdfBlock("image", page_number, image.top + 0.01, image=image))
    blocks.sort(key=lambda item: (item.page, item.top, {"paragraph": 0, "table": 1, "image": 2}[item.kind]))
    return PdfDocument(
        source,
        sha256_file(source),
        len(lines_by_page),
        blocks,
        all_tables,
        all_images,
        ocr_pages,
        warnings,
        (sum(ocr_scores) / len(ocr_scores)) if ocr_scores else None,
    )


def crop_table_cell(page, bbox: tuple[float, float, float, float], target: Path, resolution: int = 180) -> None:
    _save_crop(page, bbox, target, resolution)
