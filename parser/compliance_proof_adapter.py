#!/usr/bin/env python3
"""把现有的符合性证明材料结构解析器接入文件夹解析工具。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

try:
    from .docx_zip_repair import has_truncated_empty_zip_comment
except ImportError:
    from docx_zip_repair import has_truncated_empty_zip_comment


PROOF_PARSER_DIR = (
    Path(__file__).resolve().parent
    / "document_parsers"
    / "符合性评测表证明材料"
)


@dataclass(frozen=True)
class ProofMarkdownResult:
    markdown: str
    asset_count: int
    asset_directory: str | None
    images: list[dict[str, Any]]
    warnings: list[str]


class ComplianceProofParseError(RuntimeError):
    """带有明确原因和处理建议的证明材料解析错误。"""

    def __init__(
        self,
        failure_type: str,
        reason: str,
        suggestion: str,
        technical_detail: str = "",
    ) -> None:
        super().__init__(reason)
        self.failure_type = failure_type
        self.reason = reason
        self.suggestion = suggestion
        self.technical_detail = technical_detail


def _parser_failure(detail: str) -> ComplianceProofParseError:
    if "File is not a zip file" in detail or "End-of-central-directory" in detail:
        return ComplianceProofParseError(
            "docx_corrupt",
            "文件扩展名是.docx，但内部压缩包不完整或已经损坏，无法读取Word正文。",
            "请用Word/WPS打开后另存为新的DOCX；如果无法打开，请重新获取原始文件。",
            detail,
        )
    if "图片关系" in detail and ("不存在的文件" in detail or "NULL" in detail):
        return ComplianceProofParseError(
            "broken_image_reference",
            "Word内部存在失效的图片引用，引用指向了不存在的图片文件。",
            "请用Word/WPS打开后另存为新的DOCX，或删除失效图片后重新插入。",
            detail,
        )
    return ComplianceProofParseError(
        "proof_parser_error",
        "符合性评测表证明材料解析器未能完成解析。",
        "请查看技术错误；如文档能正常打开，可先用Word/WPS另存为新的DOCX后重试。",
        detail,
    )


def _cell(value: Any) -> str:
    return str(value if value is not None else "").replace("|", "\\|").replace("\n", "<br>")


def _render_table(item: dict[str, Any], lines: list[str]) -> None:
    records = item.get("人员信息") or item.get("数据记录") or []
    if not isinstance(records, list) or not records:
        lines.extend(["<!-- 空表格 -->", ""])
        return

    headers: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        for key in record:
            if key not in headers:
                headers.append(str(key))
    if not headers:
        lines.extend(["<!-- 空表格 -->", ""])
        return

    lines.append("| " + " | ".join(_cell(header) for header in headers) + " |")
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    for record in records:
        row = record if isinstance(record, dict) else {}
        lines.append("| " + " | ".join(_cell(row.get(header)) for header in headers) + " |")
    lines.append("")


def _render_items(
    items: list[dict[str, Any]],
    lines: list[str],
    image_link: Callable[[dict[str, Any]], str],
    images: list[dict[str, Any]],
) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = item.get("类型")
        if kind == "段落":
            text = str(item.get("文本") or "")
            lines.extend([text, ""])
        elif kind == "图片":
            link = image_link(item)
            image_id = str(item.get("图片编号") or "IMAGE")
            alt = str(item.get("替代文字") or image_id)
            lines.extend(
                [
                    f"[[IMAGE id={image_id} file={link}]]",
                    f"![{alt}]({link})",
                    "[[/IMAGE]]",
                    "",
                ]
            )
            images.append(
                {
                    "image_id": image_id,
                    "path": link,
                    "alt": str(item.get("替代文字") or ""),
                }
            )
        elif kind == "表格":
            _render_table(item, lines)
            table_images = item.get("图片") or []
            if isinstance(table_images, list):
                _render_items(table_images, lines, image_link, images)
        elif kind == "章节":
            level = max(2, min(6, int(item.get("标题级别") or 1) + 1))
            lines.extend([f"{'#' * level} {item.get('标题') or ''}", ""])
            content = item.get("内容") or []
            if isinstance(content, list):
                _render_items(content, lines, image_link, images)


def _render_markdown(
    report: dict[str, Any],
    asset_prefix: str,
) -> tuple[str, list[dict[str, Any]]]:
    images: list[dict[str, Any]] = []

    def image_link(item: dict[str, Any]) -> str:
        filename = Path(str(item.get("地址") or item.get("图片编号") or "image.bin")).name
        return (Path(asset_prefix) / filename).as_posix()

    document_name = str(report.get("文档名称") or "符合性评测表证明材料")
    lines = [f"# {document_name}", ""]
    preface = report.get("前置内容") or []
    if isinstance(preface, list):
        _render_items(preface, lines, image_link, images)
    sections = report.get("章节") or []
    if isinstance(sections, list):
        _render_items(sections, lines, image_link, images)
    return "\n".join(lines).rstrip() + "\n", images


def _replace_assets(staged_assets: Path, final_assets: Path) -> int:
    if final_assets.is_symlink():
        raise ValueError(f"附件输出位置不能是符号链接：{final_assets}")
    if final_assets.exists():
        if not final_assets.is_dir():
            raise ValueError(f"附件输出位置不是目录：{final_assets}")
        shutil.rmtree(final_assets)
    if not staged_assets.is_dir():
        return 0
    files = [path for path in staged_assets.iterdir() if path.is_file()]
    if files:
        final_assets.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(staged_assets, final_assets)
    return len(files)


def parse_compliance_proof(source: Path, output_dir: Path, stem: str) -> ProofMarkdownResult:
    """调用用户提供的证明材料解析器并转换为带图片链接的 Markdown。"""
    converter = PROOF_PARSER_DIR / "convert.py"
    if not converter.is_file():
        raise FileNotFoundError(f"缺少符合性证明材料解析器：{converter}")
    if (
        source.suffix.lower() == ".docx"
        and not zipfile.is_zipfile(source)
        and not has_truncated_empty_zip_comment(source)
    ):
        raise ComplianceProofParseError(
            "docx_corrupt",
            "文件扩展名是.docx，但内部压缩包不完整或已经损坏，无法读取Word正文。",
            "请用Word/WPS打开后另存为新的DOCX；如果无法打开，请重新获取原始文件。",
            "DOCX ZIP完整性预检失败：缺少有效的中央目录。",
        )

    relative_assets = (Path("附件") / stem).as_posix()
    final_assets = output_dir / "附件" / stem
    with tempfile.TemporaryDirectory(prefix="compliance-proof-markdown-") as directory:
        temporary_root = Path(directory)
        temporary_json = temporary_root / "proof.json"
        completed = subprocess.run(
            [sys.executable, str(converter), str(source), str(temporary_json)],
            cwd=PROOF_PARSER_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise _parser_failure(detail or f"证明材料解析器退出码：{completed.returncode}")
        report = json.loads(temporary_json.read_text(encoding="utf-8"))
        asset_count = _replace_assets(temporary_root / "proof.assets", final_assets)

    markdown, images = _render_markdown(report, relative_assets)
    raw_warnings = report.get("解析警告", [])
    warnings = [str(item) for item in raw_warnings] if isinstance(raw_warnings, list) else []
    return ProofMarkdownResult(
        markdown=markdown,
        asset_count=asset_count,
        asset_directory=relative_assets if asset_count else None,
        images=images,
        warnings=warnings,
    )
