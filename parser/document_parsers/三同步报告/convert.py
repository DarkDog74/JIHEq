# -*- coding: utf-8 -*-
"""Convert one Word 三同步 report into business JSON."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from sansync_schema import PARSER_VERSION, content_statistics, parse_report
from word_source import extract_ooxml, prepare_document

SHARED_ROOT = Path(__file__).resolve().parents[1]
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))
from _shared.audit import pdf_audit


def _write_json_atomic(path: str | Path, value: dict) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary_name, target)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _replace_assets(staged_assets: Path, final_assets: Path) -> None:
    if not final_assets.name.endswith(".assets") or final_assets.name == ".assets":
        raise ValueError(f"非法资源目录: {final_assets}")
    if final_assets.is_symlink():
        raise ValueError(f"资源输出位置不能是符号链接: {final_assets}")
    if final_assets.exists():
        if not final_assets.is_dir():
            raise ValueError(f"资源输出位置不是普通目录: {final_assets}")
        shutil.rmtree(final_assets)
    if staged_assets.is_dir() and any(staged_assets.iterdir()):
        shutil.copytree(staged_assets, final_assets)


def convert(doc_path, out_json) -> dict:
    """Parse one .doc/.docx/.pdf 三同步 report and write JSON plus audit."""
    output_path = Path(out_json).expanduser().resolve()
    source_path = Path(doc_path).expanduser().resolve()
    assets_name = f"{output_path.stem}.assets"
    assets_dir = output_path.parent / assets_name

    with tempfile.TemporaryDirectory(prefix="sansync-report-parser-") as work_dir:
        staged_assets = Path(work_dir) / "assets"
        if source_path.suffix.lower() == ".pdf":
            from pdf_source import extract_pdf_report

            structure, pdf_document = extract_pdf_report(source_path, staged_assets, assets_name)
            result, warnings = parse_report(structure, source_path.name)
            audit = {
                **pdf_audit(pdf_document, f"sansync-pdf-{PARSER_VERSION}", warnings),
                "正文段落总数（含空段落）": structure.paragraph_count,
                **content_statistics(result),
            }
        else:
            prepared = prepare_document(source_path, work_dir)
            structure = extract_ooxml(prepared.structured_docx, staged_assets, assets_name)
            result, warnings = parse_report(structure, prepared.source_path.name)
            audit = {
                "status": "needs_review" if warnings else "ok",
                "解析器版本": PARSER_VERSION,
                "源文件": str(prepared.source_path),
                "源文件SHA256": prepared.source_sha256,
                "原始格式": prepared.source_path.suffix.lower().lstrip("."),
                "是否经LibreOffice转换": prepared.converted_from_doc,
                "正文段落总数（含空段落）": structure.paragraph_count,
                **content_statistics(result),
                "警告": warnings,
            }
        _replace_assets(staged_assets, assets_dir)
        _write_json_atomic(output_path, result)
        _write_json_atomic(output_path.with_suffix(".audit.json"), audit)
        return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Word 三同步报告业务字段解析器")
    parser.add_argument("input", help="输入 .doc/.docx/.pdf 三同步报告")
    parser.add_argument("output", help="输出 JSON")
    arguments = parser.parse_args(argv)
    try:
        result = convert(arguments.input, arguments.output)
    except Exception as exc:
        print(f"解析失败: {exc}")
        return 1
    counts = content_statistics(result)
    print(f"解析完成: {arguments.output}")
    print(
        f"系统: {result['网络/系统单元名称']}；"
        f"段落 {counts['段落数']}，图片 {counts['图片数']}，表格 {counts['表格数']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
