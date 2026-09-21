# -*- coding: utf-8 -*-
"""Convert a filing-form Word document to business JSON without Tika/Java.

Usage::

    python convert.py input.doc output.json

The public ``convert(doc_path, out_json) -> dict`` entry point is retained.
An ``output.audit.json`` sidecar is written by default.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from .filing_schema import ParseAudit, parse_filing_tables
from .office_source import parse_ooxml_tables, prepare_document
from .pdf_checkbox import verify_and_apply_pdf


PARSER_NAME = "filing-form-ooxml"
PARSER_VERSION = "2.0.0"


def _default_audit_path(out_json: str | Path) -> Path:
    output = Path(out_json)
    return output.with_name(f"{output.stem}.audit.json")


def _write_json_atomic(path: str | Path, value: dict) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _event_dicts(audit: ParseAudit) -> list[dict]:
    return [asdict(item) for item in audit.events]


def _audit_base(doc_path: str | Path) -> dict:
    return {
        "status": "error",
        "parser": {"name": PARSER_NAME, "version": PARSER_VERSION},
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {"path": str(Path(doc_path).expanduser().resolve())},
        "conversion": {},
        "structure": {},
        "pdf_checkbox_verification": {},
        "checkbox_decisions": [],
        "filtered_records": [],
        "schema_placeholders": [],
        "issues": [],
    }


def convert(doc_path, out_json, audit_path=None, structured_docx_path=None) -> dict:
    """Parse one filing form and write business JSON plus an audit sidecar.

    Fatal conversion/structure failures write only the audit file and raise an
    exception.  The main JSON is replaced atomically only after every required
    stage has completed.
    """
    main_path = Path(out_json).expanduser().resolve()
    sidecar_path = (
        Path(audit_path).expanduser().resolve()
        if audit_path is not None
        else _default_audit_path(main_path).resolve()
    )
    report = _audit_base(doc_path)
    try:
        with tempfile.TemporaryDirectory(prefix="filing-parser-") as work_dir:
            prepared = prepare_document(doc_path, work_dir, structured_docx_path)
            report["source"].update(
                {
                    "sha256": prepared.source_sha256,
                    "format": prepared.source_path.suffix.lower().lstrip("."),
                }
            )
            report["conversion"] = {
                "engine": "LibreOffice",
                "executable": prepared.libreoffice_path,
                "docx_mode": "converted_from_original"
                if prepared.converted_from_doc
                else "native_ooxml",
                "pdf_mode": "rendered_from_original",
                "docx_ok": prepared.structured_docx.is_file(),
                "pdf_ok": prepared.pdf_path.is_file(),
            }

            tables = parse_ooxml_tables(prepared.structured_docx)
            report["structure"] = {
                "table_count": len(tables),
                "row_count": sum(len(table.rows) for table in tables),
                "cell_count": sum(len(row) for table in tables for row in table.rows),
                "merged_cell_count": sum(
                    cell.column_span > 1 or cell.row_span > 1
                    for table in tables
                    for row in table.rows
                    for cell in row
                ),
                "recognized": True,
            }

            # Verification runs before Schema parsing so a high-confidence PDF
            # conflict can update the canonical marker consumed by the parser.
            pdf_audit = verify_and_apply_pdf(tables, prepared.pdf_path)
            business_json, parse_audit = parse_filing_tables(tables)

            report["pdf_checkbox_verification"] = pdf_audit.to_dict()
            report["checkbox_decisions"] = parse_audit.checkbox_decisions
            report["filtered_records"] = parse_audit.filtered_records
            report["schema_placeholders"] = parse_audit.schema_placeholders
            report["issues"] = _event_dicts(parse_audit)
            if pdf_audit.conflicts:
                report["issues"].append(
                    {
                        "code": "PDF_OOXML_CONFLICT",
                        "message": "PDF 视觉状态与 OOXML 符号状态冲突，已采用高置信 PDF 结果",
                        "severity": "warning",
                        "field_path": "",
                        "details": {"count": len(pdf_audit.conflicts)},
                    }
                )
            if pdf_audit.unverified_ooxml:
                report["issues"].append(
                    {
                        "code": "PDF_MARKER_UNLOCATED",
                        "message": "部分 OOXML 复选框未能在 PDF 文字坐标中对齐，保留高置信 OOXML 证据并标记复核",
                        "severity": "info"
                        if all(
                            item["state"] != "unknown" and item["confidence"] >= 0.95
                            for item in pdf_audit.unverified_ooxml
                        )
                        else "warning",
                        "field_path": "",
                        "details": {"count": len(pdf_audit.unverified_ooxml)},
                    }
                )

            report["status"] = (
                "needs_review"
                if parse_audit.needs_review or pdf_audit.status == "needs_review"
                else "ok"
            )
            _write_json_atomic(main_path, business_json)
            _write_json_atomic(sidecar_path, report)
            return business_json
    except Exception as exc:
        report["status"] = "error"
        report["issues"].append(
            {
                "code": "FATAL_PARSE_ERROR",
                "message": str(exc),
                "severity": "error",
                "field_path": "",
                "details": {"exception_type": type(exc).__name__},
            }
        )
        # A short traceback is useful in the audit while avoiding terminal-only
        # failures.  It contains no document content.
        report["error_trace"] = traceback.format_exc(limit=8)
        _write_json_atomic(sidecar_path, report)
        raise


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="不依赖 Tika/Java 的备案信息表解析器"
    )
    parser.add_argument("input", help="输入 .doc/.docx")
    parser.add_argument("output", help="输出业务 JSON")
    parser.add_argument("--audit", help="自定义审计 JSON 路径")
    arguments = parser.parse_args(argv)
    try:
        result = convert(arguments.input, arguments.output, arguments.audit)
    except Exception as exc:
        print(f"解析失败: {exc}", file=sys.stderr)
        return 1
    audit = Path(arguments.audit) if arguments.audit else _default_audit_path(arguments.output)
    print(f"{len(result)} 个顶层键 -> {arguments.output}")
    print(f"审计记录 -> {audit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
