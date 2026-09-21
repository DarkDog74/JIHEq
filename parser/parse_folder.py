#!/usr/bin/env python3
"""扫描一个业务系统文件夹，分类并用 anydoc 转换已支持文档为 Markdown。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

# 解析子进程以 parser/ 作为脚本目录运行时，显式加入项目根目录，
# 使统一路径配置 core.path_config 可被所有解析器加载。
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from document_markdown import (
    export_assets,
    render_anydoc_with_image_markers,
    render_document,
    render_pdf_with_topology_markers,
)
from compliance_proof_adapter import ComplianceProofParseError, parse_compliance_proof
from filing_parser_adapter import parse_registration_form
from sync_report_adapter import (
    IMAGE_SUFFIXES as SYNC_IMAGE_SUFFIXES,
    copy_source_image,
    export_word_images,
    render_pdf_pages,
    write_model_input_manifest,
)
from scan_report_adapter import build_scan_report_summary, is_scan_archive_candidate
from emergency_material_standardizer import standardize_emergency_materials
from risk_report_standardizer import backup_risk_outputs, standardize_risk_reports
from docx_zip_repair import repair_truncated_docx_bytes
from word_header import convert_doc_to_docx_bytes, extract_word_header, prepend_header_comment


@dataclass(frozen=True)
class Category:
    code: str
    name: str

    @property
    def label(self) -> str:
        return f"{self.code} {self.name}"


CATEGORIES = (
    Category("2.1", "定级报告"),
    Category("2.2", "备案信息表"),
    Category("2.3", "资产清单"),
    Category("2.4", "三同步报告"),
    Category("2.5", "符合性评测表"),
    Category("2.6", "符合性评测表证明材料"),
    Category("2.7", "风险评估报告"),
    Category("2.8", "扫描报告"),
    Category("2.9", "符合性评测报告"),
    Category("2.10", "演练计划"),
)
CATEGORY_BY_CODE = {item.code: item for item in CATEGORIES}
IMPLEMENTED_CATEGORY_CODES = {
    "2.1", "2.2", "2.3", "2.4", "2.5", "2.6", "2.7", "2.8", "2.9", "2.10"
}
SUPPORTED_EXTENSIONS_BY_CATEGORY = {
    "2.1": {".doc", ".docx"},
    "2.2": {".doc", ".docx", ".wps"},
    "2.3": {".xls", ".xlsx"},
    "2.4": {".doc", ".docx", ".pdf", ".png", ".jpg", ".jpeg"},
    "2.5": {".xls", ".xlsx"},
    "2.6": {".doc", ".docx", ".pdf"},
    "2.7": {".doc", ".docx", ".pdf"},
    "2.8": {".zip"},
    "2.9": {".doc", ".docx"},
    "2.10": {".xls", ".xlsx"},
}
UNSUPPORTED_FORMAT_MESSAGES = {
    "2.1": "定级报告当前仅支持.doc和.docx格式。",
    "2.2": "备案信息表当前仅支持.doc、.docx和.wps格式。",
    "2.3": "资产清单当前仅支持.xls和.xlsx格式。",
    "2.4": "三同步报告当前仅支持.doc、.docx、.pdf、.png、.jpg和.jpeg格式。",
    "2.5": "符合性评测表当前仅支持.xls和.xlsx格式。",
    "2.6": "符合性评测表证明材料当前仅支持.doc、.docx和.pdf格式。",
    "2.7": "风险评估报告当前仅支持.doc、.docx和.pdf格式。",
    "2.8": "扫描报告当前仅支持包含HTML的.zip格式。",
    "2.9": "符合性评测报告当前仅支持.doc和.docx格式。",
    "2.10": "演练计划当前仅支持.xls和.xlsx格式。",
}
IGNORED_NAMES = {".DS_Store", "Thumbs.db"}
WORD_PDF_PRIORITY = {".docx": 0, ".doc": 1, ".pdf": 2}
SYNC_WORD_PDF_PRIORITY = {".pdf": 0, ".docx": 1, ".doc": 2}


def category_selector(value: str) -> str:
    """把“2.2”“备案信息表”等输入统一为类别代码。"""
    wanted = re.sub(r"\s+", "", value).lower()
    for category in CATEGORIES:
        candidates = {
            category.code.lower(),
            category.name.lower(),
            re.sub(r"\s+", "", category.label).lower(),
        }
        if wanted in candidates:
            if category.code not in IMPLEMENTED_CATEGORY_CODES:
                raise argparse.ArgumentTypeError(f"{category.name}当前只保留位置，尚未实现解析")
            return category.code
    supported = "、".join(
        category.name for category in CATEGORIES if category.code in IMPLEMENTED_CATEGORY_CODES
    )
    raise argparse.ArgumentTypeError(f"未知类别：{value}；当前可选：{supported}")


def normalized_name(path: Path) -> str:
    """用于分类的名称：忽略空格、连接符和常见括号。"""
    name = path.stem.lower()
    return re.sub(r"[\s\-_—－+（）()【】\[\]]+", "", name)


def classify(path: Path) -> Category | None:
    name = normalized_name(path)

    if path.suffix.casefold() == ".zip" and is_scan_archive_candidate(path):
        return CATEGORY_BY_CODE["2.8"]

    if "演练计划" in name:
        return CATEGORY_BY_CODE["2.10"]

    # “符合性”和“证明”可位于文件名任意位置，不要求两个词连续；
    # 也兼容仅以“证明材料”命名的符合性证明文件。
    # 必须先于“符合性评测表”判断，避免证明材料被归到 2.5。
    if ("符合性" in name and "证明" in name) or "证明材料" in name:
        return CATEGORY_BY_CODE["2.6"]
    if "风险评估" in name:
        return CATEGORY_BY_CODE["2.7"]
    if (
        "符合性" in name
        and "报告" in name
        and any(value in name for value in ("评测", "测评", "检查"))
    ):
        return CATEGORY_BY_CODE["2.9"]
    # 资产清单可能使用“资产表”命名，且个别文件名前缀含“定级报告”。
    # Excel 容器比该前缀更能说明它是资产表，应先于定级报告分类。
    if path.suffix.casefold() in {".xls", ".xlsx"} and any(
        value in name for value in ("资产清单", "资产表")
    ):
        return CATEGORY_BY_CODE["2.3"]
    if "定级报告" in name:
        return CATEGORY_BY_CODE["2.1"]
    if (
        "备案信息表" in name
        or "备案表" in name
        or ("备案" in name and "信息" in name)
        # 个别原件省略“备案”二字，例如“互联网域名信息安全管控系统信息表”。
        # 限定为Word/WPS且同时包含“系统信息表”，避免把普通说明表误归类。
        or (
            "系统信息表" in name
            and path.suffix.lower() in {".doc", ".docx", ".wps"}
        )
    ):
        return CATEGORY_BY_CODE["2.2"]
    if "资产清单" in name:
        return CATEGORY_BY_CODE["2.3"]
    if "三同步" in name:
        return CATEGORY_BY_CODE["2.4"]
    if any(word in name for word in ("符合性评测", "符合性测评", "符合性检查")):
        return CATEGORY_BY_CODE["2.5"]
    return None


def should_ignore(path: Path) -> bool:
    return (
        path.name in IGNORED_NAMES
        or path.name.startswith("~$")
        or any(part.startswith(".") for part in path.parts)
    )


def discover_files(input_dir: Path, output_dir: Path) -> list[Path]:
    files: list[Path] = []
    resolved_output = output_dir.resolve(strict=False)
    for path in input_dir.rglob("*"):
        relative = path.relative_to(input_dir)
        if any(
            part == "解析结果"
            or part.endswith("_anydoc解析结果")
            or "_旧结果备份_" in part
            for part in relative.parts[:-1]
        ):
            continue
        if not path.is_file() or should_ignore(relative):
            continue
        resolved = path.resolve()
        if resolved == resolved_output or resolved_output in resolved.parents:
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(input_dir).as_posix())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def duplicate_document_key(source: str, category_code: str) -> tuple[str, str, str]:
    """识别同一目录下仅扩展名不同的同名文档。

    类别由 classify() 先确定，因此不会把命中不同关键词类别的文件合并。
    目录也是键的一部分，避免误合并不同子目录中的同名材料。
    """
    path = Path(source)
    parent = unicodedata.normalize("NFKC", path.parent.as_posix()).casefold()
    stem = unicodedata.normalize("NFKC", path.stem).casefold()
    return category_code, parent, stem


def select_preferred_document_sources(records: list[dict[str, Any]]) -> None:
    """同名 Word/PDF 只保留一份待解析源：DOCX > DOC > PDF。

    只处理当前类别本已支持的 Word/PDF 格式。Excel 表格不参与此去重。
    """
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for record in records:
        if record.get("status") != "ready":
            continue
        extension = str(record.get("extension") or "").lower()
        if extension not in WORD_PDF_PRIORITY:
            continue
        category_code = str(record.get("category_code") or "")
        source = str(record.get("source") or "")
        key = duplicate_document_key(source, category_code)
        groups.setdefault(key, []).append(record)

    for candidates in groups.values():
        if len(candidates) < 2:
            continue
        extensions = {str(item.get("extension") or "").lower() for item in candidates}
        if len(extensions) < 2:
            continue
        candidates.sort(
            key=lambda item: (
                (
                    SYNC_WORD_PDF_PRIORITY
                    if str(item.get("category_code")) == "2.4"
                    else WORD_PDF_PRIORITY
                )[str(item["extension"]).lower()],
                str(item["source"]),
            )
        )
        selected = candidates[0]
        for duplicate in candidates[1:]:
            duplicate["status"] = "duplicate_skipped"
            duplicate["duplicate_of"] = selected["source"]
            duplicate["selection_reason"] = (
                "同名三同步报告只准备一份，优先级为 PDF > DOCX > DOC。"
                if str(duplicate.get("category_code")) == "2.4"
                else "同名不同格式只解析一份，优先级为 DOCX > DOC > PDF。"
            )
            duplicate["warnings"].append(
                f"与 {selected['source']} 同名，本次优先解析该文件。"
            )


def select_sync_source_family(records: list[dict[str, Any]]) -> None:
    """三同步只准备一类输入：原图 > PDF > Word。

    多张PNG/JPG会全部保留；只有在没有原图和PDF时才处理Word。
    """
    candidates = [
        record
        for record in records
        if record.get("category_code") == "2.4" and record.get("status") == "ready"
    ]
    if not candidates:
        return
    image_candidates = [
        record
        for record in candidates
        if str(record.get("extension") or "").lower() in SYNC_IMAGE_SUFFIXES
    ]
    pdf_candidates = [
        record
        for record in candidates
        if str(record.get("extension") or "").lower() == ".pdf"
    ]
    selected = image_candidates or pdf_candidates or candidates
    selected_ids = {id(record) for record in selected}
    preferred_source = str(selected[0]["source"])
    selected_type = "PNG/JPG原图" if image_candidates else ("PDF" if pdf_candidates else "Word")
    for record in candidates:
        if id(record) in selected_ids:
            continue
        record["status"] = "duplicate_skipped"
        record["duplicate_of"] = preferred_source
        record["selection_reason"] = f"三同步优先使用{selected_type}；仅在更高优先级格式不存在时处理本文件。"
        record["warnings"].append(record["selection_reason"])


def build_analysis(input_dir: Path, output_dir: Path, files: Iterable[Path]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    matched_counts = {category.code: 0 for category in CATEGORIES}
    convertible_counts = {category.code: 0 for category in CATEGORIES}

    for path in files:
        category = classify(path)
        extension = path.suffix.lower()
        error = None
        if category is None:
            status = "unmatched"
        else:
            matched_counts[category.code] += 1
            if category.code not in IMPLEMENTED_CATEGORY_CODES:
                status = "reserved"
            elif extension not in SUPPORTED_EXTENSIONS_BY_CATEGORY[category.code]:
                status = "unsupported_format"
                error = UNSUPPORTED_FORMAT_MESSAGES[category.code]
            else:
                status = "ready"

        records.append(
            {
                "source": path.relative_to(input_dir).as_posix(),
                "extension": extension,
                "size_bytes": path.stat().st_size,
                "sha256": sha256(path),
                "category_code": category.code if category else None,
                "category_name": category.name if category else None,
                "status": status,
                "output": None,
                "structured_output": None,
                "asset_directory": None,
                "asset_count": 0,
                "error": error,
                "failure_type": None,
                "failure_reason": None,
                "suggestion": None,
                "technical_error": None,
                "warnings": [],
                "header": None,
                "images": [],
                "model_inputs": [],
                "duplicate_of": None,
                "selection_reason": None,
            }
        )

    # 三同步优先直接使用原图，没有原图时使用PDF，只有Word时才转Markdown。
    select_sync_source_family(records)
    # 其他类别及Word形式的三同步，再对同目录、同名的 Word/PDF 选优。
    select_preferred_document_sources(records)
    for record in records:
        if record.get("status") == "ready" and record.get("category_code"):
            convertible_counts[str(record["category_code"])] += 1

    categories = []
    for category in CATEGORIES:
        categories.append(
            {
                "code": category.code,
                "name": category.name,
                "matched_files": matched_counts[category.code],
                "convertible_files": convertible_counts[category.code],
                "status": "present" if matched_counts[category.code] else "missing",
                "implementation": (
                    "proof_parser"
                    if category.code == "2.6"
                    else "scan_archive_parser"
                    if category.code == "2.8"
                    else "model_input_preparation"
                    if category.code == "2.4"
                    else "anydoc"
                    if category.code in IMPLEMENTED_CATEGORY_CODES
                    else "reserved"
                ),
            }
        )

    return {
        "tool": "anydoc文件夹解析工具",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "input_directory": str(input_dir),
        "output_directory": str(output_dir),
        "categories": categories,
        "files": records,
    }


def preserve_nonselected_analysis(
    output_dir: Path,
    analysis: dict[str, Any],
    selected_category_code: str,
) -> None:
    """单项重跑时保留其他类别原有的分析状态和输出引用。"""
    previous_path = output_dir / "文件分析.json"
    previous_by_source: dict[str, dict[str, Any]] = {}
    if previous_path.is_file():
        try:
            previous = json.loads(previous_path.read_text(encoding="utf-8"))
            previous_by_source = {
                str(item.get("source")): item
                for item in previous.get("files", [])
                if isinstance(item, dict) and item.get("source")
            }
        except (OSError, json.JSONDecodeError, TypeError):
            previous_by_source = {}

    merged = []
    for record in analysis["files"]:
        if record.get("category_code") == selected_category_code:
            merged.append(record)
            continue
        previous_record = previous_by_source.get(str(record.get("source")))
        if previous_record is not None:
            merged.append(previous_record)
            continue
        if record.get("status") == "ready":
            record["status"] = "skipped"
        merged.append(record)
    analysis["files"] = merged
    analysis["regeneration_mode"] = "single_category"
    analysis["selected_category_code"] = selected_category_code
    analysis["selected_category_name"] = CATEGORY_BY_CODE[selected_category_code].name


def write_analysis(output_dir: Path, analysis: dict[str, Any]) -> None:
    target = output_dir / "文件分析.json"
    target.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    status_names = {
        "ready": "待解析",
        "success": "成功",
        "failed": "失败",
        "unmatched": "未识别",
        "unsupported_format": "格式不支持",
        "dependency_missing": "缺少 anydoc",
        "duplicate_skipped": "同名优选未解析",
        "fallback_used": "首选源失败已回退",
        "skipped": "本次未重新生成",
        "reserved": "已识别，暂不解析",
    }

    def cell(value: Any) -> str:
        return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")

    lines = [
        "# 文件分析",
        "",
        f"- 输入文件夹：`{analysis['input_directory']}`",
        f"- 分析时间：{analysis['created_at']}",
        "",
        "## 类别汇总",
        "",
        "| 类别 | 识别数量 | 可解析数量 | 当前处理方式 | 状态 |",
        "|---|---:|---:|---|---|",
    ]
    for category in analysis["categories"]:
        status = "已找到" if category["status"] == "present" else "缺失"
        if category["implementation"] == "proof_parser":
            implementation = "证明材料专用解析器"
        elif category["implementation"] == "scan_archive_parser":
            implementation = "ZIP/HTML扫描报告解析器"
        elif category["implementation"] == "model_input_preparation":
            implementation = "模型输入准备"
        elif category["implementation"] == "anydoc":
            implementation = "anydoc 解析"
        else:
            implementation = "已识别，暂不解析"
        lines.append(
            f"| {category['code']} {category['name']} | {category['matched_files']} | "
            f"{category['convertible_files']} | {implementation} | {status} |"
        )

    lines.extend(
        [
            "",
            "## 文件明细",
            "",
            "| 原文件 | 规范类别 | 处理状态 | Markdown输出 | 结构化输出 | 失败原因 | 处理建议 | 说明 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )
    for record in analysis["files"]:
        category = (
            f"{record['category_code']} {record['category_name']}" if record["category_code"] else "—"
        )
        note_parts = []
        if record["error"]:
            note_parts.append(record["error"])
        note_parts.extend(record.get("warnings", []))
        lines.append(
            f"| {cell(record['source'])} | {cell(category)} | "
            f"{cell(status_names.get(record['status'], record['status']))} | "
            f"{cell(record['output'])} | {cell(record.get('structured_output'))} | "
            f"{cell(record.get('failure_reason'))} | {cell(record.get('suggestion'))} | "
            f"{cell('；'.join(note_parts))} |"
        )
    (output_dir / "文件分析.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def output_stem(category: Category, index: int) -> str:
    base = "三同步" if category.code == "2.4" else category.name
    return base if index == 1 else f"{base}（{index}）"


def clear_sync_category_outputs(output_dir: Path) -> None:
    """单项重跑三同步时，清理上次工具生成的同名文本、清单和图片。"""
    generated = re.compile(r"^三同步(?:（[2-9][0-9]*）)?(?:-[0-9]{3})?$")
    allowed_suffixes = {".md", ".json", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".tiff", ".bmp", ".svg"}
    for path in output_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in allowed_suffixes:
            continue
        if not generated.fullmatch(path.stem):
            continue
        if path.is_symlink():
            raise ValueError(f"拒绝清理符号链接形式的旧三同步结果：{path}")
        path.unlink()


def remove_stale_category_outputs(
    output_dir: Path,
    category: Category,
    expected_stems: set[str],
) -> None:
    """单项重跑时清理该类别不再需要的旧编号结果。

    只匹配工具自己生成的“类别名（N）”格式，不会扫描或删除其他文件。
    """
    numbered = re.compile(rf"^{re.escape(category.name)}(?:（[2-9][0-9]*）)?$")

    for path in output_dir.iterdir():
        if not path.is_file() or path.suffix.lower() not in {".md", ".json"}:
            continue
        if numbered.fullmatch(path.stem) and path.stem not in expected_stems:
            if path.is_symlink():
                raise ValueError(f"拒绝清理符号链接形式的旧结果：{path}")
            path.unlink()

    asset_root = output_dir / "附件"
    if not asset_root.is_dir() or asset_root.is_symlink():
        return
    for path in asset_root.iterdir():
        if not path.is_dir() or not numbered.fullmatch(path.name) or path.name in expected_stems:
            continue
        if path.is_symlink() or any(item.is_symlink() for item in path.rglob("*")):
            raise ValueError(f"拒绝清理包含符号链接的旧附件目录：{path}")
        shutil.rmtree(path)


def convert_files(
    input_dir: Path,
    output_dir: Path,
    analysis: dict[str, Any],
    analysis_only: bool,
    only_category_code: str | None = None,
) -> int:
    if analysis_only:
        return 0

    needs_anydoc = any(
        record.get("status") == "ready"
        and not (
            record.get("category_code") == "2.8"
            or (
                record.get("category_code") == "2.4"
                and str(record.get("extension") or "").lower()
                in (SYNC_IMAGE_SUFFIXES | {".pdf"})
            )
        )
        and (only_category_code is None or record.get("category_code") == only_category_code)
        for record in analysis["files"]
    )
    try:
        if needs_anydoc:
            import anydoc  # type: ignore  # noqa: F401
    except (ImportError, ModuleNotFoundError):
        message = "未安装 firecrawl-anydoc，请先运行：python3 -m pip install -r requirements.txt"
        for record in analysis["files"]:
            if record["status"] == "ready" and (
                only_category_code is None
                or record.get("category_code") == only_category_code
            ):
                record["status"] = "dependency_missing"
                record["error"] = message
        write_analysis(output_dir, analysis)
        print(message, file=sys.stderr)
        print(f"文件分析已保存：{output_dir / '文件分析.json'}", file=sys.stderr)
        return 2

    if only_category_code is not None:
        if only_category_code == "2.7":
            backup_risk_outputs(output_dir)
        selected_category = CATEGORY_BY_CODE[only_category_code]
        ready_count = sum(
            record.get("status") == "ready"
            and record.get("category_code") == only_category_code
            for record in analysis["files"]
        )
        expected_stems = (
            {"扫描报告"}
            if only_category_code == "2.8"
            else {
                output_stem(selected_category, index)
                for index in range(1, ready_count + 1)
            }
        )
        if only_category_code == "2.4":
            clear_sync_category_outputs(output_dir)
        else:
            remove_stale_category_outputs(output_dir, selected_category, expected_stems)

    scan_records = [
        record
        for record in analysis["files"]
        if record.get("category_code") == "2.8"
        and record.get("status") == "ready"
        and (only_category_code is None or only_category_code == "2.8")
    ]
    if scan_records:
        target = output_dir / "扫描报告.json"
        summary = build_scan_report_summary(
            (
                input_dir / str(record["source"]),
                str(record["source"]),
            )
            for record in scan_records
        )
        target.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result_by_source = {
            str(item["源文件"]): item
            for item in summary["压缩包检查结果"]
        }
        technical_statuses = {
            "ZIP损坏",
            "HTML无法读取",
            "HTML编码无法识别",
            "入口HTML过大",
            "解析异常",
        }
        for record in scan_records:
            parsed = result_by_source[str(record["source"])]
            record["output"] = target.relative_to(output_dir).as_posix()
            record["structured_output"] = target.relative_to(output_dir).as_posix()
            if parsed["状态"] in technical_statuses:
                record["status"] = "failed"
                record["failure_type"] = "scan_archive_parse_error"
                record["failure_reason"] = parsed["异常原因"]
                record["suggestion"] = "请检查ZIP是否可正常打开，且入口HTML可读。"
                record["error"] = record["failure_reason"]
                record["technical_error"] = record["failure_reason"]
            else:
                record["status"] = "success"
                if parsed["状态"] != "已识别":
                    record["warnings"].append(str(parsed["异常原因"] or parsed["状态"]))

    category_indices = {category.code: 0 for category in CATEGORIES}
    for record in analysis["files"]:
        if only_category_code is not None and record.get("category_code") != only_category_code:
            continue
        if record["status"] != "ready":
            continue

        category = CATEGORY_BY_CODE[record["category_code"]]
        category_indices[category.code] += 1
        stem = output_stem(category, category_indices[category.code])
        # 各类Markdown直接放在业务系统输出目录，不再创建“2.1 定级报告”等中间层。
        markdown_path = output_dir / f"{stem}.md"
        structured_path = (
            output_dir / f"{stem}.json" if category.code in {"2.2", "2.4"} else None
        )

        convert_record(input_dir, output_dir, record, category, markdown_path, structured_path)

        # 首选源失败时回退同名副本；三同步优先PDF，其他类别优先Word。
        if record["status"] == "failed":
            fallback_record = None
            siblings = sorted(
                (
                    item
                    for item in analysis["files"]
                    if item is not record
                    and item.get("status") == "duplicate_skipped"
                    and item.get("duplicate_of") == record["source"]
                ),
                key=lambda item: (
                    SYNC_WORD_PDF_PRIORITY
                    if category.code == "2.4"
                    else WORD_PDF_PRIORITY
                ).get(str(item["extension"]).lower(), 99),
            )
            for sibling in siblings:
                convert_record(
                    input_dir,
                    output_dir,
                    sibling,
                    CATEGORY_BY_CODE[sibling["category_code"]],
                    markdown_path,
                    structured_path,
                )
                if sibling["status"] == "success":
                    fallback_record = sibling
                    break
            if fallback_record is not None:
                fallback_record["duplicate_of"] = None
                fallback_record["selection_reason"] = (
                    f"首选源 {record['source']} 解析失败，自动回退采用本文件。"
                )
                fallback_record["warnings"].append(
                    f"{record['source']} 解析失败，本次以本文件生成结果。"
                )
                record["status"] = "fallback_used"
                record["fallback_to"] = fallback_record["source"]
                record["output"] = None
                record["warnings"].append(
                    f"首选源解析失败，已自动回退用 {fallback_record['source']} 生成结果。"
                )

        write_analysis(output_dir, analysis)

    risk_ordered = True
    if only_category_code in (None, "2.7"):
        risk_ordered = standardize_risk_reports(output_dir, analysis)
        write_analysis(output_dir, analysis)
    failed_any = any(
        record["status"] == "failed"
        for record in analysis["files"]
        if only_category_code is None or record.get("category_code") == only_category_code
    )
    return 1 if failed_any or not risk_ordered else 0


def convert_record(
    input_dir: Path,
    output_dir: Path,
    record: dict[str, Any],
    category: Category,
    markdown_path: Path,
    structured_path: Path | None,
) -> None:
    """解析单个待转换记录；成功标记 success，失败时记录原因（2.7 类别写失败存根）。"""
    stem = markdown_path.stem
    source = input_dir / record["source"]
    try:
            data = source.read_bytes()
            source_suffix = source.suffix.lower()
            converted_docx_data = None
            if source_suffix == ".docx" and category.code != "2.6":
                repair = repair_truncated_docx_bytes(data)
                if repair.repaired:
                    data = repair.data
                    converted_docx_data = data
                    record["warnings"].append(repair.warning)

            # 图片和PDF不依赖anydoc：直接准备可交给多模态模型的图片。
            if category.code == "2.4" and source_suffix in SYNC_IMAGE_SUFFIXES:
                images = copy_source_image(source, output_dir, stem)
                write_model_input_manifest(
                    structured_path,
                    source_file=record["source"],
                    source_format=source_suffix,
                    text_file=None,
                    images=images,
                )
                record["images"] = images
                record["model_inputs"] = [item["file"] for item in images]
                record["asset_count"] = len(images)
                record["structured_output"] = structured_path.relative_to(output_dir).as_posix()
                record["output"] = images[0]["file"]
                record["status"] = "success"
                return
            if category.code == "2.4" and source_suffix == ".pdf":
                images = render_pdf_pages(data, output_dir, stem)
                write_model_input_manifest(
                    structured_path,
                    source_file=record["source"],
                    source_format=source_suffix,
                    text_file=None,
                    images=images,
                )
                record["images"] = images
                record["model_inputs"] = [item["file"] for item in images]
                record["asset_count"] = len(images)
                record["structured_output"] = structured_path.relative_to(output_dir).as_posix()
                record["output"] = images[0]["file"]
                record["status"] = "success"
                return

            import anydoc  # type: ignore

            detected = anydoc.format_from_bytes(data) or anydoc.format_from_path(source)
            # 部分DOCX内含嵌入的Excel/OLE包，按字节探测会误判为xlsx。
            # Word扩展名是更可靠的容器约束，不应让嵌入对象改变主文档类型。
            if source.suffix.lower() == ".docx":
                detected = "docx"
            if source.suffix.lower() in {".doc", ".wps"}:
                converted, conversion_warnings = convert_doc_to_docx_bytes(source)
                record["warnings"].extend(conversion_warnings)
                if converted is not None:
                    data = converted
                    converted_docx_data = converted
                    detected = "docx"
            sync_images: list[dict[str, Any]] = []
            if category.code == "2.4" and detected == "docx":
                document = anydoc.to_document(data, detected)
                asset_map, sync_images = export_word_images(document, output_dir, stem)
                rendered = render_document(document, asset_map)
                markdown, marker_warnings = render_anydoc_with_image_markers(
                    anydoc,
                    data,
                    rendered,
                )
                record["warnings"].extend(marker_warnings)
                record["images"] = sync_images
                record["asset_count"] = len(sync_images)
            elif category.code in {"2.1", "2.7"} and detected == "docx":
                document = anydoc.to_document(data, detected)
                asset_map = export_assets(document, output_dir, stem)
                rendered = render_document(document, asset_map)
                record["images"] = rendered.occurrences
                record["asset_count"] = len(asset_map)
                if asset_map:
                    record["asset_directory"] = (Path("附件") / stem).as_posix()
                markdown, marker_warnings = render_anydoc_with_image_markers(
                    anydoc,
                    data,
                    rendered,
                )
                record["warnings"].extend(marker_warnings)
            elif category.code == "2.7" and detected == "pdf":
                native_markdown = anydoc.to_markdown_bytes(data, detected)
                rendered, marker_warnings = render_pdf_with_topology_markers(
                    data,
                    native_markdown,
                    output_dir,
                    stem,
                )
                markdown = rendered.markdown
                record["images"] = rendered.occurrences
                record["asset_count"] = len(rendered.occurrences)
                if rendered.occurrences:
                    record["asset_directory"] = (Path("附件") / stem).as_posix()
                record["warnings"].extend(marker_warnings)
            elif category.code == "2.6":
                proof = parse_compliance_proof(source, output_dir, stem)
                markdown = proof.markdown
                record["images"] = proof.images
                record["asset_count"] = proof.asset_count
                record["asset_directory"] = proof.asset_directory
                record["warnings"].extend(proof.warnings)
            else:
                # 备案信息表、资产清单、符合性评测表、风险评估报告和演练计划
                # 都保留 anydoc 原生表格 Markdown。
                markdown = anydoc.to_markdown_bytes(data, detected)
            if not markdown.strip():
                raise ValueError("anydoc 返回了空 Markdown")
            if category.code == "2.1":
                header = extract_word_header(source, docx_data=converted_docx_data)
                record["header"] = header.to_dict()
                record["warnings"].extend(header.warnings)
                markdown = prepend_header_comment(markdown, header)
            if category.code == "2.2":
                parse_registration_form(
                    source,
                    structured_path,
                    docx_data=converted_docx_data,
                )
            markdown_path.write_text(markdown, encoding="utf-8")
            if category.code == "2.4":
                markdown_relative = markdown_path.relative_to(output_dir).as_posix()
                write_model_input_manifest(
                    structured_path,
                    source_file=record["source"],
                    source_format=source_suffix,
                    text_file=markdown_relative,
                    images=sync_images,
                )
                record["model_inputs"] = [markdown_relative] + [
                    item["file"] for item in sync_images
                ]
            if structured_path is not None:
                record["structured_output"] = structured_path.relative_to(output_dir).as_posix()
            record["status"] = "success"
            record["output"] = markdown_path.relative_to(output_dir).as_posix()
    except Exception as error:  # 保留每个文件的具体失败类型和原因，继续处理后续文件。
        record["status"] = "failed"
        record["technical_error"] = f"{type(error).__name__}: {error}"
        if isinstance(error, ComplianceProofParseError):
            record["failure_type"] = error.failure_type
            record["failure_reason"] = error.reason
            record["suggestion"] = error.suggestion
            record["technical_error"] = error.technical_detail or record["technical_error"]
        else:
            record["failure_type"] = "unexpected_error"
            record["failure_reason"] = f"{type(error).__name__}: {error}"
            record["suggestion"] = "请检查原文件是否能正常打开，并查看技术错误后重试。"
        record["error"] = record["failure_reason"]
        if category.code == "2.7":
            safe_reason = re.sub(r"[\r\n]+", " ", record["failure_reason"] or "解析失败")
            markdown_path.write_text(
                f'<!-- PARSE_ERROR source="{record["source"]}" reason="{safe_reason}" -->\n'
                "# 风险评估报告解析失败\n\n"
                "该文件未生成可供稽核的正常 Markdown，请查看文件分析.json。\n",
                encoding="utf-8",
            )
            record["output"] = markdown_path.relative_to(output_dir).as_posix()


def prepare_output(output_dir: Path, overwrite: bool) -> None:
    if output_dir.exists():
        if not overwrite:
            raise FileExistsError(f"输出目录已存在：{output_dir}（如需重新生成，请加 --overwrite）")
        if output_dir.is_symlink():
            raise ValueError(f"拒绝覆盖符号链接：{output_dir}")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = output_dir.with_name(f"{output_dir.name}_旧结果备份_{timestamp}")
        serial = 2
        while backup.exists():
            backup = output_dir.with_name(f"{output_dir.name}_旧结果备份_{timestamp}_{serial}")
            serial += 1
        output_dir.rename(backup)
        print(f"原输出已备份：{backup}")
    output_dir.mkdir(parents=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="扫描一个业务系统文件夹，用anydoc解析已支持文档，并将Markdown直接输出到结果根目录。"
    )
    parser.add_argument("input", type=Path, help="待分析的业务系统文件夹")
    parser.add_argument("-o", "--output", type=Path, help="输出目录；默认放在输入文件夹旁边")
    parser.add_argument("--analysis-only", action="store_true", help="只分析和分类，不调用 anydoc")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已存在的输出目录")
    regeneration = parser.add_mutually_exclusive_group()
    regeneration.add_argument(
        "--only",
        type=category_selector,
        metavar="类别",
        help="只重新生成一种类别，可填2.1/2.2/2.3/2.4/2.5/2.6/2.7/2.8/2.9/2.10或定级报告/备案信息表/资产清单/三同步报告/符合性评测表/符合性评测表证明材料/风险评估报告/扫描报告/符合性评测报告/演练计划",
    )
    regeneration.add_argument(
        "--emergency-only",
        action="store_true",
        help="只生成应急预案/演练方案/应急演练报告的标准Markdown和分析清单",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_dir = args.input.expanduser().resolve()
    if not input_dir.is_dir():
        print(f"输入文件夹不存在：{input_dir}", file=sys.stderr)
        return 2

    output_dir = (
        args.output.expanduser().resolve()
        if args.output
        else input_dir.with_name(f"{input_dir.name}_anydoc解析结果")
    )
    if output_dir == input_dir:
        print("输出目录不能与输入目录相同。", file=sys.stderr)
        return 2
    if args.emergency_only and args.analysis_only:
        print("--emergency-only 不能与 --analysis-only 同时使用。", file=sys.stderr)
        return 2

    try:
        if args.emergency_only:
            if output_dir.is_symlink():
                raise ValueError(f"拒绝写入符号链接：{output_dir}")
            output_dir.mkdir(parents=True, exist_ok=True)
            emergency = standardize_emergency_materials(
                input_dir,
                output_dir,
                overwrite=args.overwrite,
            )
            print(f"应急材料分析：{emergency.manifest_path}")
            print(
                f"命中{emergency.matched_count}个，成功{emergency.success_count}个，"
                f"失败{emergency.failed_count}个。"
            )
            print(f"输出目录：{output_dir}")
            return 1 if emergency.failed_count else 0
        if args.only:
            if output_dir.is_symlink():
                raise ValueError(f"拒绝写入符号链接：{output_dir}")
            output_dir.mkdir(parents=True, exist_ok=True)
        else:
            prepare_output(output_dir, args.overwrite)
        files = discover_files(input_dir, output_dir)
        analysis = build_analysis(input_dir, output_dir, files)
        if args.only:
            preserve_nonselected_analysis(output_dir, analysis, args.only)
        write_analysis(output_dir, analysis)
        code = convert_files(
            input_dir,
            output_dir,
            analysis,
            args.analysis_only,
            only_category_code=args.only,
        )
        emergency = None
        if not args.analysis_only and args.only is None:
            emergency = standardize_emergency_materials(
                input_dir,
                output_dir,
                overwrite=False,
            )
            if emergency.failed_count:
                code = 1
    except Exception as error:
        print(f"处理失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 2

    counted_files = (
        [item for item in analysis["files"] if item.get("category_code") == args.only]
        if args.only
        else analysis["files"]
    )
    success = sum(item["status"] == "success" for item in counted_files)
    failed = sum(item["status"] == "failed" for item in counted_files)
    fallbacks = sum(item["status"] == "fallback_used" for item in counted_files)
    unsupported = sum(item["status"] == "unsupported_format" for item in counted_files)
    duplicates = sum(item["status"] == "duplicate_skipped" for item in counted_files)
    unmatched = sum(item["status"] == "unmatched" for item in counted_files)
    reserved = sum(item["status"] == "reserved" for item in counted_files)
    missing = [
        item["name"]
        for item in analysis["categories"]
        if item["status"] == "missing" and (args.only is None or item["code"] == args.only)
    ]
    print(f"文件分析：{output_dir / '文件分析.json'}")
    if args.only:
        print(f"本次只重新生成：{CATEGORY_BY_CODE[args.only].name}")
    if not args.analysis_only:
        print(f"成功解析：{success} 个文件")
        print(
            f"解析失败：{failed} 个文件；其他类别暂不解析：{reserved} 个文件；"
            f"同名优选未解析：{duplicates} 个文件；格式不支持：{unsupported} 个文件；"
            f"未识别：{unmatched} 个文件"
        )
        if emergency is not None:
            print(
                f"应急材料：命中{emergency.matched_count}个，"
                f"成功{emergency.success_count}个，失败{emergency.failed_count}个"
            )
    for record in counted_files:
        if record.get("status") != "failed":
            continue
        print(f"失败文件：{record.get('source')}")
        print(f"  失败原因：{record.get('failure_reason') or record.get('error')}")
        print(f"  处理建议：{record.get('suggestion') or '请查看文件分析.json中的技术错误。'}")
    if not args.analysis_only and fallbacks:
        print(f"自动回退挽救：{fallbacks} 个文件（首选源解析失败，已用同名副本成功解析）")
        for record in counted_files:
            if record.get("status") != "fallback_used":
                continue
            print(f"回退文件：{record.get('source')} → {record.get('fallback_to')}")
    if missing:
        print("缺失类别：" + "、".join(missing))
    print(f"输出目录：{output_dir}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
