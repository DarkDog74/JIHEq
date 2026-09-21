"""为后续多模态模型准备三同步报告的文本和图片输入。"""

from __future__ import annotations

import json
import mimetypes
import re
import shutil
from pathlib import Path
from typing import Any

from document_markdown import AssetEntry


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _normalized_image_suffix(value: str) -> str:
    suffix = value.lower()
    return ".jpg" if suffix == ".jpeg" else suffix


def _asset_extension(media_type: str) -> str:
    known = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/tiff": ".tiff",
        "image/bmp": ".bmp",
        "image/svg+xml": ".svg",
    }
    lowered = media_type.lower()
    if lowered in known:
        return known[lowered]
    guessed = mimetypes.guess_extension(lowered) if lowered.startswith("image/") else None
    if guessed:
        return _normalized_image_suffix(guessed)
    subtype = re.sub(r"[^a-z0-9]", "", lowered.partition("/")[2])
    return f".{subtype}" if subtype else ".img"


def _image_filename(stem: str, index: int, suffix: str) -> str:
    """单张使用“三同步.png/jpg”，多张从 -002 开始编号。"""
    normalized_suffix = _normalized_image_suffix(suffix)
    return f"{stem}{'' if index == 1 else f'-{index:03d}'}{normalized_suffix}"


def copy_source_image(source: Path, output_dir: Path, stem: str) -> list[dict[str, Any]]:
    """原样复制 PNG/JPG，不做压缩、OCR或二次编码。"""
    suffix = source.suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError(f"三同步图片格式不支持：{source.suffix}")
    target = output_dir / _image_filename(stem, 1, suffix)
    shutil.copyfile(source, target)
    return [
        {
            "marker_id": "IMG-001",
            "kind": "source_image",
            "media_type": mimetypes.guess_type(target.name)[0] or "image/jpeg",
            "file": target.relative_to(output_dir).as_posix(),
            "page": None,
            "origin_part": source.name,
        }
    ]


def render_pdf_pages(
    pdf_data: bytes,
    output_dir: Path,
    stem: str,
    dpi: int = 200,
) -> list[dict[str, Any]]:
    """使用 PyMuPDF 将 PDF 每页渲染成 PNG。"""
    try:
        import pymupdf
    except (ImportError, ModuleNotFoundError) as error:
        raise RuntimeError("PDF逐页转PNG需要PyMuPDF，请安装requirements.txt") from error

    document = pymupdf.open(stream=pdf_data, filetype="pdf")
    try:
        if document.needs_pass:
            raise ValueError("三同步PDF已加密，无法渲染")
        if len(document) == 0:
            raise ValueError("三同步PDF没有可渲染页面")
        images: list[dict[str, Any]] = []
        for page_index, page in enumerate(document):
            number = page_index + 1
            target = output_dir / _image_filename(stem, number, ".png")
            pixmap = page.get_pixmap(dpi=dpi, alpha=False)
            pixmap.save(target)
            images.append(
                {
                    "marker_id": f"IMG-{number:03d}",
                    "kind": "pdf_page",
                    "media_type": "image/png",
                    "file": target.relative_to(output_dir).as_posix(),
                    "page": number,
                    "origin_part": f"pdf/page-{number}",
                    "dpi": dpi,
                }
            )
        return images
    finally:
        document.close()


def export_word_images(
    document: Any,
    output_dir: Path,
    stem: str,
) -> tuple[dict[int, AssetEntry], list[dict[str, Any]]]:
    """导出 Word 内所有实际图片资产，不把 OLE/.bin 冒充图片。"""
    asset_map: dict[int, AssetEntry] = {}
    images: list[dict[str, Any]] = []
    for asset in document.assets:
        if not asset.media_type.lower().startswith("image/"):
            continue
        number = len(images) + 1
        suffix = _asset_extension(asset.media_type)
        target = output_dir / _image_filename(stem, number, suffix)
        target.write_bytes(asset.data)
        relative = target.relative_to(output_dir).as_posix()
        entry = AssetEntry(
            asset_id=asset.id,
            marker_id=f"IMG-{number:03d}",
            kind="image",
            media_type=asset.media_type,
            origin_part=asset.origin_part,
            file=relative,
        )
        asset_map[asset.id] = entry
        images.append(
            {
                "marker_id": entry.marker_id,
                "kind": "word_embedded_image",
                "media_type": entry.media_type,
                "file": relative,
                "page": None,
                "origin_part": entry.origin_part,
            }
        )
    return asset_map, images


def write_model_input_manifest(
    target: Path,
    *,
    source_file: str,
    source_format: str,
    text_file: str | None,
    images: list[dict[str, Any]],
) -> dict[str, Any]:
    """记录相对于清单所在目录的模型输入路径，方便整体搬迁。"""
    model_inputs: list[dict[str, Any]] = []
    if text_file:
        model_inputs.append({"type": "markdown", "path": text_file})
    model_inputs.extend(
        {
            "type": "image",
            "path": str(image["file"]),
            **({"page": image["page"]} if image.get("page") is not None else {}),
        }
        for image in images
    )
    payload = {
        "document_type": "三同步报告",
        "source_file": source_file,
        "source_format": source_format.lower(),
        "path_base": "当前JSON所在目录",
        "text_file": text_file,
        "image_files": [str(image["file"]) for image in images],
        "model_inputs": model_inputs,
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload
