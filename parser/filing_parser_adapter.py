#!/usr/bin/env python3
"""加载并调用高精度备案信息表解析器。

备案信息表解析器直接读取 Word OOXML 表格结构，并用 PDF 视觉结果复核
复选框。这里仅负责把它接入现有的文件夹批处理工具。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from filing_form_parser import convert as convert_filing_form
from word_header import convert_doc_to_docx_bytes


def parse_registration_form(
    source: Path,
    output_json: Path,
    docx_data: bytes | None = None,
) -> dict:
    """解析一份备案信息表，只保留业务 JSON。

    底层解析器仍会生成审计信息用于保证解析完整性，但审计文件写入
    临时目录并在本次解析结束后自动清理。
    """
    with tempfile.TemporaryDirectory(prefix="filing-form-audit-") as directory:
        parser_source = source
        structured_docx = None
        if source.suffix.lower() in {".doc", ".docx", ".wps"} and docx_data is not None:
            structured_docx = Path(directory) / f"{source.stem}.docx"
            structured_docx.write_bytes(docx_data)
        elif source.suffix.lower() == ".wps":
            converted, warnings = convert_doc_to_docx_bytes(source)
            if converted is None:
                raise ValueError("WPS转DOCX失败：" + "；".join(warnings))
            structured_docx = Path(directory) / f"{source.stem}.docx"
            structured_docx.write_bytes(converted)
        if source.suffix.lower() in {".docx", ".wps"} and structured_docx is not None:
            parser_source = structured_docx
            structured_docx = None
        audit_json = Path(directory) / "备案信息表.audit.json"
        return convert_filing_form(
            parser_source,
            output_json,
            audit_json,
            structured_docx_path=structured_docx,
        )
