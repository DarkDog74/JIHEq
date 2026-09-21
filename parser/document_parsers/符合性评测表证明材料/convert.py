# -*- coding: utf-8 -*-
"""Convert one compliance proof document to ordered nested JSON."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

PARSER_ROOT = Path(__file__).resolve().parents[2]
if str(PARSER_ROOT) not in sys.path:
    sys.path.insert(0, str(PARSER_ROOT))

from docx_zip_repair import (
    TRUNCATED_EOCD_REPAIR_WARNING,
    has_truncated_empty_zip_comment,
    repair_truncated_empty_zip_comment,
)

import source as source_module
from proof_structure import PARSER_VERSION, content_statistics, parse_nested_proof
from source import extract_ooxml, prepare_document

SHARED_ROOT = Path(__file__).resolve().parents[1]
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))
from _shared.audit import pdf_audit
from _shared.mineru_source import extract_mineru_as_word_structure
from _shared.pdf_backend import backend_for
from _shared.pdf_word import extract_as_word_structure


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
    output_path = Path(out_json).expanduser().resolve()
    source_path = Path(doc_path).expanduser().resolve()
    assets_name = f"{output_path.stem}.assets"
    assets_dir = output_path.parent / assets_name

    with tempfile.TemporaryDirectory(prefix="compliance-proof-parser-") as work_dir:
        parse_source = source_path
        repair_warning = ""
        if (
            source_path.suffix.lower() == ".docx"
            and has_truncated_empty_zip_comment(source_path)
        ):
            parse_source = repair_truncated_empty_zip_comment(
                source_path,
                Path(work_dir) / source_path.name,
            )
            repair_warning = TRUNCATED_EOCD_REPAIR_WARNING
        staged_assets = Path(work_dir) / "assets"
        if source_path.suffix.lower() == ".pdf":
            backend = backend_for("符合性评测表证明材料")
            if backend == "mineru":
                structure, pdf_document = extract_mineru_as_word_structure(
                    source_path, work_dir, staged_assets, assets_name, source_module
                )
            else:
                structure, pdf_document = extract_as_word_structure(
                    source_path, staged_assets, assets_name, source_module
                )
            result = parse_nested_proof(structure, source_path.name)
            _write_json_atomic(
                output_path.with_suffix(".audit.json"),
                pdf_audit(
                    pdf_document,
                    f"compliance-proof-pdf-{PARSER_VERSION}",
                    engine="MinerU pipeline(本地)" if backend == "mineru" else "pdfplumber+OpenCV+RapidOCR(按需)",
                ),
            )
        else:
            prepared = prepare_document(parse_source, work_dir)
            structure = extract_ooxml(
                prepared.structured_docx,
                staged_assets,
                assets_name,
                skip_broken_images=True,
            )
            result = parse_nested_proof(structure, prepared.source_path.name)
            if repair_warning:
                result.setdefault("解析警告", []).insert(0, repair_warning)
            if structure.warnings:
                result["解析警告"] = list(
                    dict.fromkeys(result.get("解析警告", []) + structure.warnings)
                )
        _replace_assets(staged_assets, assets_dir)
        _write_json_atomic(output_path, result)
        return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="符合性评测证明材料嵌套解析器")
    parser.add_argument("input", help="输入 .doc/.docx/.pdf 证明材料")
    parser.add_argument("output", help="输出嵌套 JSON")
    arguments = parser.parse_args(argv)
    try:
        result = convert(arguments.input, arguments.output)
    except Exception as exc:
        if Path(arguments.input).suffix.lower() == ".pdf":
            _write_json_atomic(
                Path(arguments.output).expanduser().resolve().with_suffix(".audit.json"),
                {"status": "error", "解析来源": "pdf", "源文件": str(Path(arguments.input).expanduser().resolve()), "错误": str(exc)},
            )
        print(f"解析失败: {exc}")
        return 1
    counts = content_statistics(result)
    print(f"解析完成: {arguments.output}")
    print(
        "内容统计: "
        f"章节 {counts['章节']}，段落 {counts['段落']}，"
        f"图片 {counts['图片']}，表格 {counts['表格']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
