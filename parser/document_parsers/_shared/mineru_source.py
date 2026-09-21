# -*- coding: utf-8 -*-
"""Local MinerU CLI adapter producing the existing Word-like structure model."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

from lxml import html

from .pdf_light import PdfBlock, PdfCell, PdfDocument, PdfImage, PdfLine, PdfSourceError, PdfTable, clean_text, sha256_file
from .pdf_word import to_word_structure


def resolve_mineru() -> str:
    configured = os.getenv("MINERU_PATH", "").strip()
    candidate = configured or shutil.which("mineru") or ""
    if not candidate or not Path(candidate).is_file():
        raise PdfSourceError(
            "该类别的轻量PDF后端未通过Golden验收，必须使用本地MinerU；"
            "当前未找到 mineru 命令。请按 requirements-mineru.txt 安装并下载本地模型，"
            "或通过 MINERU_PATH 指定可执行文件。"
        )
    return candidate


def _span_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_span_text(item) for item in value)
    if isinstance(value, dict):
        if isinstance(value.get("content"), str):
            return value["content"]
        return "".join(_span_text(item) for item in value.values())
    return ""


def _html_table(value: str) -> tuple[list[list[str]], list[PdfCell]]:
    try:
        root = html.fromstring(value)
    except Exception:
        return [], []
    rows = root.xpath(".//tr") if root.tag.lower() != "tr" else [root]
    grid: list[list[str | None]] = []
    cells: list[PdfCell] = []
    occupied: dict[tuple[int, int], bool] = {}
    for row_index, tr in enumerate(rows):
        while len(grid) <= row_index:
            grid.append([])
        column = 0
        for node in tr.xpath("./th|./td"):
            while occupied.get((row_index, column)):
                column += 1
            row_span = max(1, int(node.get("rowspan", "1") or 1))
            column_span = max(1, int(node.get("colspan", "1") or 1))
            text = clean_text("".join(node.itertext()))
            needed = column + column_span
            for target_row in range(row_index, row_index + row_span):
                while len(grid) <= target_row:
                    grid.append([])
                if len(grid[target_row]) < needed:
                    grid[target_row].extend([None] * (needed - len(grid[target_row])))
                for target_column in range(column, column + column_span):
                    occupied[(target_row, target_column)] = True
            grid[row_index][column] = text
            cells.append(PdfCell(row_index, column, text, row_span=row_span, column_span=column_span))
            column += column_span
    width = max((len(row) for row in grid), default=0)
    return [[clean_text(value) for value in row] + [""] * (width - len(row)) for row in grid], cells


def _markdown_table(value: str) -> tuple[list[list[str]], list[PdfCell]]:
    rows = []
    for line in value.splitlines():
        stripped = line.strip().strip("|")
        if not stripped or re.fullmatch(r"[:\- |]+", stripped):
            continue
        rows.append([clean_text(cell) for cell in stripped.split("|")])
    width = max((len(row) for row in rows), default=0)
    matrix = [row + [""] * (width - len(row)) for row in rows]
    return matrix, [PdfCell(r, c, value) for r, row in enumerate(matrix) for c, value in enumerate(row)]


def _table_data(item: dict) -> tuple[list[list[str]], list[PdfCell]]:
    value = item.get("table_body") or item.get("content") or item.get("table_content") or ""
    if isinstance(value, dict):
        value = value.get("table_body") or value.get("html") or value.get("content") or ""
    value = _span_text(value)
    return _html_table(value) if "<table" in value.lower() else _markdown_table(value)


def _find_content_list(output_dir: Path) -> Path:
    legacy = sorted(output_dir.rglob("*_content_list.json"))
    if legacy:
        return legacy[0]
    raise PdfSourceError("MinerU执行完成但未生成 *_content_list.json")


def extract_mineru(
    pdf_path: str | Path,
    work_dir: str | Path,
    assets_dir: str | Path,
    assets_relative_dir: str,
) -> PdfDocument:
    source = Path(pdf_path).expanduser().resolve()
    output = Path(work_dir) / "mineru-output"
    output.mkdir(parents=True, exist_ok=True)
    command = [
        resolve_mineru(),
        "-p", str(source),
        "-o", str(output),
        "-b", "pipeline",
        "-m", "auto",
        "-l", "ch",
        "-t", "true",
        "-f", "false",
    ]
    environment = os.environ.copy()
    environment.setdefault("MINERU_TABLE_MERGE_ENABLE", "true")
    completed = subprocess.run(command, capture_output=True, text=True, timeout=3600, env=environment)
    if completed.returncode != 0:
        raise PdfSourceError(f"本地MinerU解析失败: {(completed.stderr or completed.stdout).strip()}")
    content_path = _find_content_list(output)
    values = json.loads(content_path.read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise PdfSourceError("MinerU content_list 顶层不是数组")
    blocks: list[PdfBlock] = []
    tables: list[PdfTable] = []
    images: list[PdfImage] = []
    final_assets = Path(assets_dir)
    final_assets.mkdir(parents=True, exist_ok=True)
    for item in values:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type", ""))
        page = int(item.get("page_idx", 0)) + 1
        bbox = item.get("bbox") if isinstance(item.get("bbox"), list) else [0, 0, 1000, 1000]
        top = float(bbox[1])
        if kind in {"text", "title", "list", "index"}:
            text = clean_text(item.get("text") or _span_text(item.get("content")) or "\n".join(item.get("list_items", [])))
            if not text:
                continue
            level = int(item.get("text_level") or 0) or None
            line = PdfLine(page, top, float(bbox[3]), float(bbox[0]), float(bbox[2]), text, heading_level=level, source="mineru")
            blocks.append(PdfBlock("paragraph", page, top, line=line))
        elif kind == "table":
            matrix, cells = _table_data(item)
            if not matrix:
                continue
            table = PdfTable(page, top, float(bbox[3]), tuple(map(float, bbox)), matrix, cells)
            tables.append(table)
            blocks.append(PdfBlock("table", page, top, table=table))
        elif kind in {"image", "chart"}:
            relative = item.get("img_path") or item.get("image_path") or ""
            image_source = (content_path.parent / relative).resolve() if relative else None
            if image_source is None or not image_source.is_file():
                continue
            extension = image_source.suffix.lower() or ".png"
            file_name = f"IMG{len(images)+1:03d}{extension}"
            target = final_assets / file_name
            shutil.copy2(image_source, target)
            image = PdfImage(page, top, float(bbox[3]), tuple(map(float, bbox)), file_name, target, f"{assets_relative_dir}/{file_name}", kind)
            images.append(image)
            blocks.append(PdfBlock("image", page, top, image=image))
    blocks.sort(key=lambda item: (item.page, item.top))
    page_count = max((block.page for block in blocks), default=0)
    return PdfDocument(
        source=source,
        sha256=sha256_file(source),
        page_count=page_count,
        blocks=blocks,
        tables=tables,
        images=images,
        ocr_pages=[],
        warnings=[],
        mean_ocr_confidence=None,
    )


def extract_mineru_as_word_structure(
    pdf_path: str | Path,
    work_dir: str | Path,
    assets_dir: str | Path,
    assets_relative_dir: str,
    source_module: ModuleType,
):
    document = extract_mineru(pdf_path, work_dir, assets_dir, assets_relative_dir)
    return to_word_structure(document, source_module), document
