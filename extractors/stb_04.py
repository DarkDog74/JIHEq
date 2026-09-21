from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .common import stage_audit_artifacts
from .registry import register_extractor


class STB04ExtractionError(ValueError):
    """STB-04 三同步清单或报告内容无法可靠提取。"""


def extract_stb_04_data(
    manifest: Mapping[str, Any], manifest_dir: Path
) -> tuple[dict[str, Any], list[Path]]:
    """从三同步清单（三同步.json）收集报告形态，纯收集不做判断。

    与 STB-00 同一锚点：报告为 Word 时清单指向 Markdown（无图 → 必然无章，
    规则层直接判不通过）；为图片/PDF 时 image_files 指向解析器落在清单
    同目录的图片（规则层喂视觉模型识别印章）。
    """
    images: list[Path] = []
    for relative in manifest.get("image_files") or []:
        if not isinstance(relative, str) or not relative.strip():
            continue
        path = manifest_dir / relative
        if path.is_file() and path not in images:
            images.append(path)

    has_markdown = False
    text_file = manifest.get("text_file")
    if isinstance(text_file, str) and text_file.strip():
        if (manifest_dir / text_file).is_file():
            has_markdown = True

    if not images and not has_markdown:
        raise STB04ExtractionError(
            "三同步清单未指向任何可读的报告内容（无 Markdown 也无图片）"
        )
    fields: dict[str, Any] = {
        "has_images": bool(images),
        "图片数量": len(images),
        "报告形态": "图片" if images else "仅Markdown",
        "源文件": str(manifest.get("source_file") or ""),
    }
    return fields, images


@register_extractor("STB-04", document_name_contains=("三同步",), file_suffixes=(".json",))
def extract_stb_04(source: Path) -> dict[str, Any]:
    manifest_path = Path(source)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"输入三同步清单不存在：{manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise STB04ExtractionError(f"三同步清单 JSON 格式错误：{error}") from error
    if not isinstance(manifest, Mapping):
        raise STB04ExtractionError("三同步清单顶层必须是对象")

    fields, images = extract_stb_04_data(manifest, manifest_path.parent)
    stage_audit_artifacts(
        "STB-04",
        manifest_path,
        fields,
        images=[{"路径": image, "说明": "三同步报告图片"} for image in images],
    )
    return fields
