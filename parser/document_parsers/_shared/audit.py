# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(path: str | Path, value: dict) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, target)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def pdf_audit(
    document,
    parser_version: str,
    warnings: list[str] | None = None,
    engine: str = "pdfplumber+OpenCV+RapidOCR(按需)",
) -> dict:
    all_warnings = list(document.warnings) + list(warnings or [])
    return {
        "status": "needs_review" if all_warnings else "ok",
        "解析器版本": parser_version,
        "解析来源": "pdf",
        "解析引擎": engine,
        "源文件": str(document.source),
        "源文件SHA256": document.sha256,
        "页数": document.page_count,
        "OCR页码": document.ocr_pages,
        "OCR平均置信度": document.mean_ocr_confidence,
        "表格数": len(document.tables),
        "图片区域数": len(document.images),
        "警告": all_warnings,
    }
