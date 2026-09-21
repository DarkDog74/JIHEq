from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Mapping

from .common import stage_audit_artifacts
from .registry import register_extractor


class STB00ExtractionError(ValueError):
    """STB-00 三同步报告无法可靠提取。"""


def _existing(manifest_dir: Path, relative: Any) -> Path | None:
    if not isinstance(relative, str) or not relative.strip():
        return None
    path = manifest_dir / relative
    return path if path.is_file() else None


def _is_sync_report_list_file(source: str) -> bool:
    """文件名按顺序命中“三同步…报告…清单”的 Excel 文件。"""
    path = Path(str(source or ""))
    if path.name.startswith("~$") or path.suffix.casefold() not in {".xls", ".xlsx"}:
        return False
    normalized = unicodedata.normalize("NFKC", path.stem)
    # 空格、连字符和括号等装饰不影响关键词顺序匹配。
    normalized = re.sub(r"[\W_]+", "", normalized, flags=re.UNICODE)
    return re.search(r"三同步.*报告.*清单", normalized) is not None


def _find_sync_report_list_files(manifest_dir: Path) -> list[str]:
    """从解析阶段保留的原始文件清单中识别三同步报告清单。"""
    analysis_path = Path(manifest_dir) / "文件分析.json"
    if not analysis_path.is_file():
        return []
    try:
        analysis = json.loads(analysis_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(analysis, Mapping):
        return []

    records = analysis.get("files")
    if not isinstance(records, list):
        return []
    matched: list[str] = []
    for record in records:
        if not isinstance(record, Mapping):
            continue
        source = record.get("source")
        if not isinstance(source, str) or not _is_sync_report_list_file(source):
            continue
        if source not in matched:
            matched.append(source)
    return matched


def extract_stb_00_data(
    manifest: Mapping[str, Any], manifest_dir: Path
) -> tuple[dict[str, Any], list[Path]]:
    """从三同步清单（三同步.json）收集报告正文与图片，纯收集不做判断。

    清单由解析器生成：报告为 Word 时 text_file 指向转换出的 Markdown；
    为图片或 PDF 时 image_files 指向解析器落在清单同目录的图片。两者可同时存在。
    """
    body = ""
    text_path = _existing(manifest_dir, manifest.get("text_file"))
    if text_path is not None:
        body = text_path.read_text(encoding="utf-8-sig").strip()
    images: list[Path] = []
    for relative in manifest.get("image_files") or []:
        path = _existing(manifest_dir, relative)
        if path is not None and path not in images:
            images.append(path)
    if not body and not images:
        raise STB00ExtractionError(
            "三同步清单未指向任何可读的报告内容（无 Markdown 也无图片）"
        )
    report_list_files = _find_sync_report_list_files(manifest_dir)
    fields: dict[str, Any] = {
        "三同步正文": body,
        "源文件": str(manifest.get("source_file") or ""),
        "图片数量": len(images),
        "三同步报告存在": True,
        "三同步报告清单存在": bool(report_list_files),
        "三同步报告清单文件": report_list_files,
    }
    return fields, images


@register_extractor("STB-00", document_name_contains=("三同步",), file_suffixes=(".json",))
def extract_stb_00(source: Path) -> dict[str, Any]:
    manifest_path = Path(source)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"输入三同步清单不存在：{manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise STB00ExtractionError(f"三同步清单 JSON 格式错误：{error}") from error
    if not isinstance(manifest, Mapping):
        raise STB00ExtractionError("三同步清单顶层必须是对象")

    fields, images = extract_stb_00_data(manifest, manifest_path.parent)
    stage_audit_artifacts(
        "STB-00",
        manifest_path,
        fields,
        images=[{"路径": image, "说明": "三同步报告图片"} for image in images],
    )
    return fields
