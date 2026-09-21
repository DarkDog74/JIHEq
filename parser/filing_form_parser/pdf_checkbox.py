# -*- coding: utf-8 -*-
"""Local PDF checkbox verification.

The PDF is rendered from the original input document, never from the
intermediate DOCX.  Text/font coordinates are preferred because they preserve
the exact symbol-font semantics.  A small OpenCV classifier is available for
glyphs whose PDF character mapping is private or unknown.  Network services
are deliberately not used.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
import pdfplumber

from .office_source import Cell, SymbolEvidence, Table, classify_symbol


State = Literal["checked", "unchecked", "unknown"]


@dataclass
class PdfMarker:
    state: State
    page: int
    bbox: tuple[float, float, float, float]
    raw: str
    font: str
    evidence_source: str = "pdf_text"
    confidence: float = 1.0


@dataclass
class PdfVerification:
    status: str = "ok"
    pages: int = 0
    ooxml_marker_count: int = 0
    pdf_marker_count: int = 0
    matched_count: int = 0
    conflicts: list[dict] = field(default_factory=list)
    unverified_ooxml: list[dict] = field(default_factory=list)
    unmatched_pdf: list[dict] = field(default_factory=list)
    decisions: list[dict] = field(default_factory=list)
    ocr_fallback: str = "not_needed"

    def to_dict(self) -> dict:
        return asdict(self)


def classify_checkbox_crop(image: np.ndarray) -> tuple[State, float]:
    """Classify a tightly cropped square checkbox using only local pixels.

    The border is removed before measuring interior ink.  Deliberately
    ambiguous crops are returned as ``unknown`` instead of being guessed.
    """
    if image is None or image.size == 0:
        return "unknown", 0.0
    if image.ndim == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.astype(np.uint8, copy=False)
    height, width = gray.shape[:2]
    if height < 6 or width < 6:
        return "unknown", 0.0
    gray = cv2.resize(gray, (48, 48), interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # Ignore the square's outer frame and look for strokes in its interior.
    interior = binary[10:38, 10:38]
    ink_ratio = float(np.count_nonzero(interior)) / float(interior.size)
    if ink_ratio >= 0.075:
        return "checked", min(0.99, 0.72 + ink_ratio)
    if ink_ratio <= 0.025:
        return "unchecked", min(0.98, 0.90 - ink_ratio)
    return "unknown", 0.45


def _crop_unknown_marker(page, char: dict) -> tuple[State, float]:
    try:
        rendered = np.asarray(page.to_image(resolution=240, antialias=True).original)
    except Exception:
        return "unknown", 0.0
    scale = 240.0 / 72.0
    x0 = max(0, int((char["x0"] - 1.5) * scale))
    y0 = max(0, int((char["top"] - 1.5) * scale))
    x1 = min(rendered.shape[1], int((char["x1"] + 1.5) * scale))
    y1 = min(rendered.shape[0], int((char["bottom"] + 1.5) * scale))
    return classify_checkbox_crop(rendered[y0:y1, x0:x1])


def extract_pdf_markers(pdf_path: str | Path) -> tuple[list[PdfMarker], int]:
    # PyMuPDF is the primary coordinate engine.  pdfplumber remains a local
    # compatibility fallback for runtimes where PyMuPDF is not installed or a
    # legacy symbol font has no usable PyMuPDF character map.
    pymupdf = _extract_pymupdf_markers(pdf_path)
    fallback = _extract_pdfplumber_markers(pdf_path)
    if pymupdf is None or not pymupdf[0]:
        return fallback
    # Legacy Word symbol fonts can expose different character maps through
    # different PDF engines.  Accept the PyMuPDF stream when the independent
    # local extraction agrees; otherwise use the already validated fallback
    # and let OOXML/PDF audit surface any remaining gap.
    if fallback[0]:
        pymupdf_states = [item.state for item in pymupdf[0]]
        fallback_states = [item.state for item in fallback[0]]
        if pymupdf_states != fallback_states:
            return fallback
    return pymupdf


def _order_baseline_runs(page_markers: list[PdfMarker]) -> list[PdfMarker]:
    ordered: list[PdfMarker] = []
    cursor = 0
    while cursor < len(page_markers):
        end = cursor + 1
        baseline = (page_markers[cursor].bbox[1] + page_markers[cursor].bbox[3]) / 2
        while end < len(page_markers):
            candidate = page_markers[end]
            candidate_baseline = (candidate.bbox[1] + candidate.bbox[3]) / 2
            if abs(candidate_baseline - baseline) > 3.0:
                break
            end += 1
        ordered.extend(sorted(page_markers[cursor:end], key=lambda item: item.bbox[0]))
        cursor = end
    return ordered


def _extract_pymupdf_markers(
    pdf_path: str | Path,
) -> tuple[list[PdfMarker], int] | None:
    try:
        import pymupdf  # type: ignore
    except Exception:
        return None
    markers: list[PdfMarker] = []
    try:
        with pymupdf.open(str(pdf_path)) as document:
            pages = len(document)
            for page_number, page in enumerate(document, 1):
                page_markers: list[PdfMarker] = []
                raw = page.get_text("rawdict")
                for block in raw.get("blocks", []):
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            font = span.get("font", "")
                            for char in span.get("chars", []):
                                text = char.get("c", "")
                                evidence = classify_symbol(text, font)
                                if evidence is None:
                                    continue
                                bbox = tuple(float(value) for value in char.get("bbox", (0, 0, 0, 0)))
                                state = evidence.state
                                confidence = evidence.confidence
                                source = "pdf_text"
                                if state == "unknown":
                                    state, confidence = _crop_unknown_pymupdf(page, bbox)
                                    source = "local_image"
                                page_markers.append(
                                    PdfMarker(
                                        state=state,
                                        page=page_number,
                                        bbox=bbox,
                                        raw=text,
                                        font=font,
                                        evidence_source=source,
                                        confidence=confidence,
                                    )
                                )
                markers.extend(_order_baseline_runs(page_markers))
        return markers, pages
    except Exception:
        return None


def _crop_unknown_pymupdf(page, bbox) -> tuple[State, float]:
    try:
        import pymupdf  # type: ignore

        rectangle = pymupdf.Rect(bbox)
        rectangle.x0 -= 1.5
        rectangle.y0 -= 1.5
        rectangle.x1 += 1.5
        rectangle.y1 += 1.5
        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(240 / 72, 240 / 72),
            clip=rectangle,
            alpha=False,
        )
        array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
            pixmap.height, pixmap.width, pixmap.n
        )
        return classify_checkbox_crop(array)
    except Exception:
        return "unknown", 0.0


def _extract_pdfplumber_markers(pdf_path: str | Path) -> tuple[list[PdfMarker], int]:
    markers: list[PdfMarker] = []
    pages = 0
    with pdfplumber.open(str(pdf_path)) as pdf:
        pages = len(pdf.pages)
        for page_number, page in enumerate(pdf.pages, 1):
            page_markers: list[PdfMarker] = []
            for char in page.chars:
                raw = char.get("text", "")
                font = char.get("fontname", "")
                evidence = classify_symbol(raw, font)
                if evidence is None:
                    continue
                state = evidence.state
                confidence = evidence.confidence
                source = "pdf_text"
                if state == "unknown":
                    state, confidence = _crop_unknown_marker(page, char)
                    source = "local_image"
                page_markers.append(
                    PdfMarker(
                        state=state,
                        page=page_number,
                        bbox=(
                            float(char.get("x0", 0.0)),
                            float(char.get("top", 0.0)),
                            float(char.get("x1", 0.0)),
                            float(char.get("bottom", 0.0)),
                        ),
                        raw=raw,
                        font=font,
                        evidence_source=source,
                        confidence=confidence,
                    )
                )
            # PDF content streams may emit different font runs in font order
            # instead of visual order (e.g. unchecked OpenSymbol glyphs before
            # a checked glyph positioned to their left).  Reorder only
            # consecutive markers that share a visual baseline; retaining the
            # surrounding content-stream order avoids interleaving multi-line
            # checkbox cells from different table columns.
            markers.extend(_order_baseline_runs(page_markers))
    return markers, pages


def _flatten_ooxml(tables: list[Table]) -> list[tuple[Cell, int, SymbolEvidence]]:
    flattened: list[tuple[Cell, int, SymbolEvidence]] = []
    for table in tables:
        for row in table.rows:
            for cell in row:
                for marker_index, evidence in enumerate(cell.symbol_evidence):
                    flattened.append((cell, marker_index, evidence))
    return flattened


def _align_states(left: list[State], right: list[State]) -> list[tuple[int | None, int | None]]:
    """Levenshtein alignment, preferring a same-position substitution.

    A substitution costs less than delete+insert so a real PDF/OOXML conflict
    is surfaced as a conflict rather than hidden as two unrelated gaps.
    """
    n, m = len(left), len(right)
    costs = [[0] * (m + 1) for _ in range(n + 1)]
    steps: list[list[str]] = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        costs[i][0], steps[i][0] = i, "up"
    for j in range(1, m + 1):
        costs[0][j], steps[0][j] = j, "left"
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            diagonal = costs[i - 1][j - 1] + (0 if left[i - 1] == right[j - 1] else 1)
            up = costs[i - 1][j] + 1
            across = costs[i][j - 1] + 1
            best = min(diagonal, up, across)
            costs[i][j] = best
            steps[i][j] = "diag" if diagonal == best else ("up" if up == best else "left")
    pairs: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i or j:
        step = steps[i][j]
        if step == "diag":
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif step == "up":
            pairs.append((i - 1, None))
            i -= 1
        else:
            pairs.append((None, j - 1))
            j -= 1
    return list(reversed(pairs))


def _replace_cell_marker(cell: Cell, marker_index: int, state: State) -> None:
    replacement = "☑" if state == "checked" else ("□" if state == "unchecked" else "�")
    seen = -1

    def replace(match: re.Match[str]) -> str:
        nonlocal seen
        seen += 1
        return replacement if seen == marker_index else match.group(0)

    cell.normalized_text = re.sub(r"[☑□�]", replace, cell.normalized_text)


def verify_and_apply_pdf(tables: list[Table], pdf_path: str | Path) -> PdfVerification:
    result = PdfVerification()
    ooxml = _flatten_ooxml(tables)
    markers, result.pages = extract_pdf_markers(pdf_path)
    result.ooxml_marker_count = len(ooxml)
    result.pdf_marker_count = len(markers)
    if not markers and ooxml:
        markers, result.ocr_fallback = _extract_ocr_markers(pdf_path)
        result.pdf_marker_count = len(markers)
    if not markers and ooxml:
        result.status = "needs_review"
        for cell, marker_index, evidence in ooxml:
            result.unverified_ooxml.append(
                _unverified_dict(cell, marker_index, evidence, "PDF 无可用复选框坐标")
            )
        return result

    pairs = _align_states(
        [item[2].state for item in ooxml],
        [marker.state for marker in markers],
    )
    for ooxml_index, pdf_index in pairs:
        if ooxml_index is None:
            result.unmatched_pdf.append(asdict(markers[pdf_index]))
            continue
        cell, marker_index, xml_evidence = ooxml[ooxml_index]
        if pdf_index is None:
            result.unverified_ooxml.append(
                _unverified_dict(cell, marker_index, xml_evidence, "PDF 视觉文本中未找到对应符号")
            )
            continue
        pdf_evidence = markers[pdf_index]
        result.matched_count += 1
        decision = {
            "table_index": cell.table_index,
            "row_index": cell.row_index,
            "cell_column": cell.column_start,
            "marker_index": marker_index,
            "ooxml_state": xml_evidence.state,
            "pdf_state": pdf_evidence.state,
            "final_state": xml_evidence.state,
            "page": pdf_evidence.page,
            "bbox": list(pdf_evidence.bbox),
            "evidence_source": pdf_evidence.evidence_source,
            "confidence": pdf_evidence.confidence,
        }
        if pdf_evidence.state != "unknown" and pdf_evidence.confidence >= 0.70:
            decision["final_state"] = pdf_evidence.state
            # A visually located PDF marker is the final evidence even when it
            # agrees with OOXML; attach its page/coordinates for Schema audit.
            xml_evidence.source = pdf_evidence.evidence_source
            xml_evidence.confidence = pdf_evidence.confidence
            xml_evidence.page = pdf_evidence.page
            xml_evidence.bbox = pdf_evidence.bbox
            if pdf_evidence.state != xml_evidence.state:
                conflict = decision | {"cell_text": cell.normalized_text}
                result.conflicts.append(conflict)
                _replace_cell_marker(cell, marker_index, pdf_evidence.state)
                xml_evidence.state = pdf_evidence.state
        result.decisions.append(decision)

    if result.conflicts or result.unmatched_pdf:
        result.status = "needs_review"
    # Missing PDF text is not allowed to erase strong OOXML evidence.  This is
    # observable in a few source rows where LibreOffice visibly drops a symbol
    # during rendering.  Strong, known OOXML symbols remain usable; only weak
    # or unknown fallbacks force manual review.
    if any(
        item["state"] == "unknown" or item["confidence"] < 0.95
        for item in result.unverified_ooxml
    ):
        result.status = "needs_review"
    return result


def _unverified_dict(
    cell: Cell,
    marker_index: int,
    evidence: SymbolEvidence,
    reason: str,
) -> dict:
    return {
        "table_index": cell.table_index,
        "row_index": cell.row_index,
        "cell_column": cell.column_start,
        "marker_index": marker_index,
        "state": evidence.state,
        "source": evidence.source,
        "confidence": evidence.confidence,
        "cell_text": cell.normalized_text,
        "reason": reason,
    }


def _rapidocr_engine():
    try:
        from rapidocr import RapidOCR  # type: ignore
    except Exception:
        try:
            from rapidocr_onnxruntime import RapidOCR  # type: ignore
        except Exception:
            return None
    try:
        return RapidOCR()
    except Exception:
        return None


def _ocr_lines(engine, image: np.ndarray) -> list[tuple[np.ndarray, str, float]]:
    """Normalise RapidOCR 1.x/2.x/3.x result shapes."""
    try:
        raw = engine(image)
    except Exception:
        return []
    if hasattr(raw, "boxes") and hasattr(raw, "txts"):
        boxes = list(raw.boxes or [])
        texts = list(raw.txts or [])
        scores = list(raw.scores or [0.5] * len(texts))
        return [(np.asarray(box), str(text), float(score)) for box, text, score in zip(boxes, texts, scores)]
    rows = raw[0] if isinstance(raw, tuple) else raw
    if not isinstance(rows, list):
        return []
    result: list[tuple[np.ndarray, str, float]] = []
    for row in rows:
        if isinstance(row, (list, tuple)) and len(row) >= 3:
            try:
                result.append((np.asarray(row[0]), str(row[1]), float(row[2])))
            except (TypeError, ValueError):
                continue
    return result


OCR_OPTION_PATTERN = re.compile(
    r"(?:全国|全省|跨省|跨地|单位内部|社会公众|两者均包括|"
    r"未评审|已评审|第[1-5]级|远程|本地|自行开发|第三方开发|"
    r"已完成改造|部分改造|不适用|未处理|处理或存储|无|有|是|否)"
)


def _extract_ocr_markers(pdf_path: str | Path) -> tuple[list[PdfMarker], str]:
    """Use local OCR labels and a left-hand image crop when PDF has no text.

    This fallback intentionally returns only visually classifiable boxes.  It
    never calls a network model and never infers a selection from the label.
    """
    engine = _rapidocr_engine()
    if engine is None:
        return [], "unavailable"
    markers: list[PdfMarker] = []
    try:
        with pdfplumber.open(str(pdf_path)) as pdf:
            for page_number, page in enumerate(pdf.pages, 1):
                rendered = np.asarray(page.to_image(resolution=200, antialias=True).original)
                for box, text, score in _ocr_lines(engine, rendered):
                    if score < 0.45 or not OCR_OPTION_PATTERN.search(text):
                        continue
                    x0, y0 = np.min(box[:, 0]), np.min(box[:, 1])
                    x1, y1 = np.max(box[:, 0]), np.max(box[:, 1])
                    height = max(8.0, y1 - y0)
                    # The form template places each square immediately left of
                    # its option.  Include a small margin for Wingdings marks.
                    crop_x0 = max(0, int(x0 - height * 1.35))
                    crop_x1 = max(crop_x0 + 1, int(x0 - height * 0.05))
                    crop_y0 = max(0, int(y0 - height * 0.15))
                    crop_y1 = min(rendered.shape[0], int(y1 + height * 0.15))
                    state, confidence = classify_checkbox_crop(
                        rendered[crop_y0:crop_y1, crop_x0:crop_x1]
                    )
                    if state == "unknown":
                        continue
                    scale = 72.0 / 200.0
                    markers.append(
                        PdfMarker(
                            state=state,
                            page=page_number,
                            bbox=(
                                crop_x0 * scale,
                                crop_y0 * scale,
                                crop_x1 * scale,
                                crop_y1 * scale,
                            ),
                            raw=text,
                            font="OCR",
                            evidence_source="local_image",
                            confidence=min(confidence, float(score)),
                        )
                    )
    except Exception:
        return [], "failed"
    markers.sort(key=lambda item: (item.page, item.bbox[1], item.bbox[0]))
    return markers, "used" if markers else "completed_no_markers"
