#!/usr/bin/env python3
"""复用现有 Word 能力解析应急预案和应急演练材料。

本模块只负责格式转换，不根据文件名判定文档类型。
支持 .doc/.docx；不处理 WPS 和 PDF。
"""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    from .document_markdown import (
        export_assets,
        render_anydoc_with_image_markers,
        render_document,
    )
    from .docx_zip_repair import repair_truncated_docx_bytes
    from .word_header import convert_doc_to_docx_bytes
except ImportError:
    from document_markdown import (
        export_assets,
        render_anydoc_with_image_markers,
        render_document,
    )
    from docx_zip_repair import repair_truncated_docx_bytes
    from word_header import convert_doc_to_docx_bytes


SUPPORTED_SUFFIXES = frozenset({".doc", ".docx"})


@dataclass(frozen=True)
class EmergencyMarkdownResult:
    """单份应急文档的解析产物。"""

    document_kind: str
    source_format: str
    conversion_method: str
    markdown: str
    asset_count: int
    asset_directory: str | None
    images: list[dict[str, Any]]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EmergencyDocumentParseError(RuntimeError):
    """带有稳定错误分类的应急文档解析异常。"""

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


def _validate_stem(source: Path, stem: str | None) -> str:
    value = (stem or source.stem).strip()
    if not value or value in {".", ".."} or Path(value).name != value:
        raise EmergencyDocumentParseError(
            "invalid_output_name",
            f"非法的输出名称：{value!r}",
            "输出名称只能是单个文件名，不能包含目录或路径跳转。",
        )
    return value


def _load_docx_bytes(source: Path) -> tuple[bytes, str, list[str]]:
    suffix = source.suffix.casefold()
    if suffix == ".docx":
        try:
            repair = repair_truncated_docx_bytes(source.read_bytes())
            warnings = [repair.warning] if repair.repaired else []
            return repair.data, "anydoc_docx", warnings
        except OSError as error:
            raise EmergencyDocumentParseError(
                "source_read_failed",
                f"应急文档无法读取：{source.name}",
                "请检查原文件权限和完整性。",
                f"{type(error).__name__}: {error}",
            ) from error

    try:
        converted, warnings = convert_doc_to_docx_bytes(source)
    except OSError as error:
        raise EmergencyDocumentParseError(
            "source_read_failed",
            f"旧版 .doc 文档无法读取：{source.name}",
            "请检查原文件权限和完整性。",
            f"{type(error).__name__}: {error}",
        ) from error
    if converted is None:
        detail = "；".join(warnings) or "LibreOffice 未生成 DOCX"
        raise EmergencyDocumentParseError(
            "legacy_doc_conversion_failed",
            "旧版 .doc 文档无法转换为临时 DOCX。",
            "请确认已安装 LibreOffice，或将原文件另存为 .docx 后重试。",
            detail,
        )
    return converted, "libreoffice_then_anydoc", warnings


def _prepare_staging_root(destination: Path) -> tempfile.TemporaryDirectory[str]:
    if destination.is_symlink():
        raise EmergencyDocumentParseError(
            "output_write_failed",
            f"输出目录不能是符号链接：{destination}",
            "请使用普通目录作为输出位置。",
        )
    if destination.exists() and not destination.is_dir():
        raise EmergencyDocumentParseError(
            "output_write_failed",
            f"输出位置不是目录：{destination}",
            "请更换为有效的输出目录。",
        )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        return tempfile.TemporaryDirectory(
            prefix=".emergency-word-",
            dir=destination.parent,
        )
    except OSError as error:
        raise EmergencyDocumentParseError(
            "output_write_failed",
            f"无法创建应急文档临时输出目录：{destination.parent}",
            "请检查输出目录权限和剩余空间。",
            f"{type(error).__name__}: {error}",
        ) from error


def _commit_asset_directory(
    staged_assets: Path,
    final_assets: Path,
    staging_root: Path,
    *,
    overwrite: bool,
) -> None:
    if final_assets.is_symlink():
        raise EmergencyDocumentParseError(
            "output_write_failed",
            f"附件输出位置不能是符号链接：{final_assets}",
            "请使用普通目录作为附件输出位置。",
        )
    if final_assets.exists() and not final_assets.is_dir():
        raise EmergencyDocumentParseError(
            "output_write_failed",
            f"附件输出位置不是目录：{final_assets}",
            "请清理冲突文件后重试。",
        )
    if final_assets.exists() and not overwrite:
        raise EmergencyDocumentParseError(
            "output_exists",
            f"附件输出目录已存在：{final_assets}",
            "如确认需要整体替换旧附件，请显式传入 overwrite=True。",
        )

    backup = staging_root / "previous-assets"
    had_previous = final_assets.exists()
    try:
        if had_previous:
            final_assets.rename(backup)
        if staged_assets.is_dir():
            final_assets.parent.mkdir(parents=True, exist_ok=True)
            staged_assets.rename(final_assets)
    except OSError as error:
        if had_previous and backup.exists() and not final_assets.exists():
            backup.rename(final_assets)
        raise EmergencyDocumentParseError(
            "output_write_failed",
            f"应急文档附件写入失败：{final_assets}",
            "请检查输出目录权限和剩余空间。",
            f"{type(error).__name__}: {error}",
        ) from error


def _parse_word_document(
    source: str | Path,
    output_dir: str | Path,
    *,
    document_kind: str,
    stem: str | None = None,
    overwrite: bool = False,
) -> EmergencyMarkdownResult:
    source_path = Path(source)
    destination = Path(output_dir)

    try:
        is_file = source_path.is_file()
        source_size = source_path.stat().st_size if is_file else None
    except OSError as error:
        raise EmergencyDocumentParseError(
            "source_read_failed",
            f"应急文档无法读取：{source_path}",
            "请检查原始文件路径和权限。",
            f"{type(error).__name__}: {error}",
        ) from error
    if not is_file:
        raise EmergencyDocumentParseError(
            "source_not_found",
            f"应急文档不存在：{source_path}",
            "请检查原始文件路径。",
        )
    suffix = source_path.suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise EmergencyDocumentParseError(
            "unsupported_format",
            f"应急文档当前仅支持 .doc 和 .docx：{source_path.name}",
            "请提供 .doc 或 .docx 文件；WPS 和 PDF 不在本次处理范围。",
        )
    if source_size == 0:
        raise EmergencyDocumentParseError(
            "empty_source",
            f"应急文档为空文件：{source_path.name}",
            "请重新获取完整的原始文件。",
        )

    output_stem = _validate_stem(source_path, stem)
    final_assets = destination / "附件" / output_stem
    if final_assets.exists() and not overwrite:
        raise EmergencyDocumentParseError(
            "output_exists",
            f"附件输出目录已存在：{final_assets}",
            "如确认需要整体替换旧附件，请显式传入 overwrite=True。",
        )
    docx_data, conversion_method, warnings = _load_docx_bytes(source_path)
    try:
        import anydoc  # type: ignore
    except (ImportError, ModuleNotFoundError) as error:
        raise EmergencyDocumentParseError(
            "dependency_missing",
            "缺少应急 Word 解析依赖 anydoc。",
            "请使用项目 .venv 运行，或安装 parser/requirements.txt。",
            f"{type(error).__name__}: {error}",
        ) from error

    temporary_directory = _prepare_staging_root(destination)
    with temporary_directory as temporary_name:
        staging_root = Path(temporary_name)
        try:
            # 强制按 DOCX 主容器解析，避免内嵌 Excel/OLE 导致字节探测误判。
            document = anydoc.to_document(docx_data, "docx")
            asset_map = export_assets(document, staging_root, output_stem)
            rendered = render_document(document, asset_map)
            markdown, marker_warnings = render_anydoc_with_image_markers(
                anydoc,
                docx_data,
                rendered,
            )
            warnings = [*warnings, *marker_warnings]
        except Exception as error:
            raise EmergencyDocumentParseError(
                "word_parse_failed",
                f"应急文档解析失败：{source_path.name}",
                "请检查文件是否能正常打开，并查看技术错误后重试。",
                f"{type(error).__name__}: {error}",
            ) from error

        if not markdown.strip():
            raise EmergencyDocumentParseError(
                "empty_markdown",
                f"应急文档未解析出有效正文：{source_path.name}",
                "请检查文档是否仅包含扫描图片或内容已损坏。",
            )

        _commit_asset_directory(
            staging_root / "附件" / output_stem,
            final_assets,
            staging_root,
            overwrite=overwrite,
        )

    asset_directory = (
        (Path("附件") / output_stem).as_posix() if asset_map else None
    )
    return EmergencyMarkdownResult(
        document_kind=document_kind,
        source_format=suffix.removeprefix("."),
        conversion_method=conversion_method,
        markdown=markdown,
        asset_count=len(asset_map),
        asset_directory=asset_directory,
        images=[dict(item) for item in rendered.occurrences],
        warnings=warnings,
    )


def write_emergency_markdown(
    result: EmergencyMarkdownResult,
    output: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    """将已成功解析的 Markdown 落盘，不负责解析或文档分类。"""

    target = Path(output)
    if target.exists() and not overwrite:
        raise EmergencyDocumentParseError(
            "output_exists",
            f"输出文件已存在：{target}",
            "如确认需要覆盖，请显式传入 overwrite=True。",
        )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result.markdown, encoding="utf-8")
    except OSError as error:
        raise EmergencyDocumentParseError(
            "output_write_failed",
            f"应急文档 Markdown 写入失败：{target}",
            "请检查输出目录权限和剩余空间。",
            f"{type(error).__name__}: {error}",
        ) from error
    return target


def parse_emergency_plan(
    source: str | Path,
    output_dir: str | Path,
    stem: str | None = None,
    *,
    overwrite: bool = False,
) -> EmergencyMarkdownResult:
    """解析一份应急预案，保留标题、表格和内嵌图片。"""

    return _parse_word_document(
        source,
        output_dir,
        document_kind="应急预案",
        stem=stem,
        overwrite=overwrite,
    )


def parse_emergency_drill(
    source: str | Path,
    output_dir: str | Path,
    stem: str | None = None,
    *,
    overwrite: bool = False,
) -> EmergencyMarkdownResult:
    """解析一份应急演练材料，包括演练方案或演练总结报告。"""

    return _parse_word_document(
        source,
        output_dir,
        document_kind="应急演练",
        stem=stem,
        overwrite=overwrite,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="复用现有 Word 能力解析应急预案或应急演练材料。"
    )
    parser.add_argument("source", type=Path, help="原始 .doc/.docx 文件")
    parser.add_argument("output", type=Path, help="输出 Markdown 文件")
    parser.add_argument(
        "--kind",
        choices=("plan", "drill"),
        required=True,
        help="plan=应急预案，drill=应急演练材料",
    )
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有 Markdown")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    output: Path = args.output
    if output.exists() and not args.overwrite:
        raise EmergencyDocumentParseError(
            "output_exists",
            f"输出文件已存在：{output}",
            "如需覆盖请加 --overwrite。",
        )
    parser_function = parse_emergency_plan if args.kind == "plan" else parse_emergency_drill
    result = parser_function(
        args.source,
        output.parent,
        stem=output.stem,
        overwrite=args.overwrite,
    )
    write_emergency_markdown(result, output, overwrite=args.overwrite)
    summary = result.to_dict()
    summary.pop("markdown", None)
    summary["output"] = output.as_posix()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
