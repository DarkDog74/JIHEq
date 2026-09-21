# -*- coding: utf-8 -*-
"""Convert one grading report to ordered, heading-nested JSON."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import source as source_module
from nested_structure import PARSER_VERSION, content_statistics, parse_nested_report
from source import extract_ooxml, prepare_document

SHARED_ROOT = Path(__file__).resolve().parents[1]
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))
from _shared.audit import pdf_audit
from _shared.mineru_source import extract_mineru_as_word_structure
from _shared.pdf_backend import backend_for
from _shared.pdf_word import extract_as_word_structure

PDF_HEADING_TERMS = {
    "基本情况", "系统概述", "业务/应用范围", "服务范围", "用户类型",
    "管理情况", "所属单位基本情况", "安全管理架构", "技术情况",
    "物理环境", "网络拓扑结构", "硬件设备部署情况", "网络边界划分",
    "云服务平台使用情况", "安全等级的确定", "赋值理由", "社会影响力赋值",
    "规模和服务范围赋值", "提供服务的重要性赋值",
    "采用定级方法及选择权重理由", "安全保护等级",
}


def _deduplicate_pdf_template_headings(structure) -> None:
    seen: set[str] = set()
    kept = []
    normalized_terms = {term.replace(" ", "") for term in PDF_HEADING_TERMS}
    root_index = next(
        (
            index
            for index, block in enumerate(structure.blocks)
            if block.kind == "paragraph"
            and block.text.replace(" ", "").strip("：:").endswith("基本情况")
            and len(block.text.replace(" ", "")) <= len("基本情况") + 35
        ),
        0,
    )
    for index, block in enumerate(structure.blocks):
        normalized = block.text.replace(" ", "").strip("：:") if block.kind == "paragraph" else ""
        if index < root_index and re.search(r"[.．·]{4,}", normalized):
            continue
        canonical = next(
            (
                term
                for term in sorted(normalized_terms, key=len, reverse=True)
                if normalized == term
                or (
                    term in {"基本情况", "安全等级的确定", "安全保护等级"}
                    and normalized.endswith(term)
                    and len(normalized) <= len(term) + 35
                )
            ),
            "",
        )
        if index < root_index and canonical:
            continue
        if canonical and canonical in seen:
            continue
        if canonical:
            seen.add(canonical)
        kept.append(block)
    structure.blocks = kept
    for index, block in enumerate(structure.blocks):
        block.block_index = index


def _canonical_section(title: str) -> str:
    value = re.sub(r"\s+", "", title).strip("：:")
    aliases = {
        "业务应用范围": "业务/应用范围",
        "所提供服务的重要性赋值": "提供服务的重要性赋值",
    }
    value = aliases.get(value, value)
    if value == "所属单位基本情况":
        return value
    if value.endswith("基本情况"):
        return "__基本情况__"
    if "安全等级的确定" in value or value.endswith("安全等级确定"):
        return "__等级确定__"
    if value.endswith("安全保护等级"):
        return "__保护等级__"
    return value if value in PDF_HEADING_TERMS else ""


def _normalize_pdf_section_tree(result: dict) -> None:
    """Merge duplicate recovered headings and enforce the fixed grading template."""
    found: dict[str, dict] = {}

    def collect(items: list[dict]) -> list[dict]:
        remainder: list[dict] = []
        for item in items:
            if item.get("类型") != "章节":
                remainder.append(item)
                continue
            canonical = _canonical_section(item.get("标题", ""))
            direct = collect(item.get("内容", []))
            if not canonical:
                clone = dict(item)
                clone["内容"] = direct
                remainder.append(clone)
                continue
            if canonical not in found:
                found[canonical] = {
                    "类型": "章节",
                    "标题级别": item.get("标题级别", 1),
                    "标题": item.get("标题", canonical),
                    "内容": direct,
                }
            else:
                found[canonical]["内容"].extend(direct)
        return remainder

    unknown_top = collect(result.get("章节", []))
    basic = found.get("__基本情况__")
    if basic is None:
        base = re.sub(r"(?:[-－]?定级报告.*)$", "", Path(result.get("文档名称", "")).stem)
        basic = {"类型": "章节", "标题级别": 1, "标题": f"{base}基本情况", "内容": []}
    base_title = basic["标题"][: -len("基本情况")] if basic["标题"].endswith("基本情况") else ""

    def section(key: str, level: int, title: str | None = None) -> dict:
        item = found.get(key, {"类型": "章节", "标题级别": level, "标题": title or key, "内容": []})
        item["标题级别"] = level
        if title is not None and key.startswith("__"):
            item["标题"] = title
        return item

    overview = section("系统概述", 2)
    overview["内容"].extend(
        section(key, 3) for key in ("业务/应用范围", "服务范围", "用户类型")
    )
    management = section("管理情况", 2)
    management["内容"].extend(
        section(key, 3) for key in ("所属单位基本情况", "安全管理架构")
    )
    technology = section("技术情况", 2)
    technology["内容"].extend(
        section(key, 3)
        for key in ("物理环境", "网络拓扑结构", "硬件设备部署情况", "网络边界划分", "云服务平台使用情况")
    )
    basic["标题级别"] = 1
    basic["内容"].extend((overview, management, technology))

    level_root = section("__等级确定__", 1, f"{base_title}安全等级的确定")
    reasons = section("赋值理由", 2)
    reasons["内容"].extend(
        section(key, 3)
        for key in ("社会影响力赋值", "规模和服务范围赋值", "提供服务的重要性赋值")
    )
    level_root["内容"].extend(
        (
            reasons,
            section("采用定级方法及选择权重理由", 2),
            section("__保护等级__", 2, f"{base_title}安全保护等级"),
        )
    )
    result["章节"] = [*unknown_top, basic, level_root]


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
    """Replace only the asset directory owned by the requested JSON output."""
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
    """Parse one .doc/.docx/.pdf grading report and write nested JSON."""
    main_path = Path(out_json).expanduser().resolve()
    source_path = Path(doc_path).expanduser().resolve()
    assets_name = f"{main_path.stem}.assets"
    assets_dir = main_path.parent / assets_name

    with tempfile.TemporaryDirectory(prefix="grading-report-parser-") as work_dir:
        staged_assets = Path(work_dir) / "assets"
        if source_path.suffix.lower() == ".pdf":
            backend = backend_for("定级报告")
            if backend == "mineru":
                structure, pdf_document = extract_mineru_as_word_structure(
                    source_path, work_dir, staged_assets, assets_name, source_module
                )
            else:
                structure, pdf_document = extract_as_word_structure(
                    source_path,
                    staged_assets,
                    assets_name,
                    source_module,
                    ocr_heading_terms=PDF_HEADING_TERMS,
                )
            _deduplicate_pdf_template_headings(structure)
            result = parse_nested_report(structure, source_path.name)
            _normalize_pdf_section_tree(result)
            _write_json_atomic(
                main_path.with_suffix(".audit.json"),
                pdf_audit(
                    pdf_document,
                    f"grading-pdf-{PARSER_VERSION}",
                    engine="MinerU pipeline(本地)" if backend == "mineru" else "pdfplumber+OpenCV+RapidOCR(按需)",
                ),
            )
        else:
            prepared = prepare_document(source_path, work_dir)
            structure = extract_ooxml(prepared.structured_docx, staged_assets, assets_name)
            result = parse_nested_report(structure, prepared.source_path.name)
        _replace_assets(staged_assets, assets_dir)
        _write_json_atomic(main_path, result)
        return result


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="定级报告按标题层级嵌套解析器")
    parser.add_argument("input", help="输入 .doc/.docx/.pdf 定级报告")
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
