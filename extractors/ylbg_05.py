from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote

from .common import parse_image_refs, stage_audit_artifacts
from .emergency_audit_common import (
    EmergencyAuditExtractionError,
    find_named_section,
    read_current_emergency_markdown,
)
from .errors import TargetSectionNotFoundError
from .registry import register_extractor


_MODEL_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
_MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*(?:<(?P<angle>[^>]+)>|(?P<plain>[^\s)]+))(?:\s+\"[^\"]*\")?\s*\)")


def _image_key(value: object) -> str:
    text = unquote(str(value or "").strip())
    return text[2:] if text.startswith("./") else text


def _select_process_images(
    markdown: str, record: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """按正文位置筛选，从演练过程标题之后一直取到文末。"""
    section = find_named_section(markdown, ("演练过程",))
    if section is None:
        raise TargetSectionNotFoundError("应急演练报告未找到正文“演练过程”章节，不回退全文图片")
    raw_images = record.get("图片") or []
    if not isinstance(raw_images, list) or any(not isinstance(x, Mapping) for x in raw_images):
        raise EmergencyAuditExtractionError("应急演练报告图片清单格式异常")

    lines = markdown.splitlines()
    selected: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    located: set[int] = set()
    before: set[int] = set()
    for line_number, line in enumerate(lines, start=1):
        # 若标题与图片压在同一行，以标题文字的结束位置为界。
        fragments = [(line, line_number > section.title_line)]
        if line_number == section.title_line:
            split = line.find("演练过程") + len("演练过程")
            fragments = [(line[:split], False), (line[split:], True)]
        for fragment, in_scope in fragments:
            refs = parse_image_refs(fragment)
            refs.extend(
                {"id": "", "file": match.group("angle") or match.group("plain")}
                for match in _MARKDOWN_IMAGE_RE.finditer(fragment)
            )
            for ref in refs:
                marker = str(ref.get("id") or "")
                path = _image_key(ref.get("file"))
                matches = [
                    (i, item) for i, item in enumerate(raw_images)
                    if (marker and marker == item.get("marker_id"))
                    or (path and path == _image_key(item.get("file")))
                ]
                if len(matches) > 1:
                    raise EmergencyAuditExtractionError(f"图片引用对应多条清单记录：{marker or path}")
                if matches:
                    index, item = matches[0]
                    located.add(index)
                    if not in_scope:
                        before.add(index)
                    if marker and path and _image_key(item.get("file")) != path:
                        raise EmergencyAuditExtractionError(f"图片ID与文件路径不一致：{marker}")
                else:
                    # 未登记的章内引用不能静默漏掉，保留为无法核验。
                    item = {"kind": "image", "marker_id": marker, "file": path,
                            "media_type": ref.get("type"), "status": "unavailable"}
                key = _image_key(item.get("file")) or str(item.get("marker_id") or "")
                if in_scope and key not in seen:
                    selected.append(item)
                    seen.add(key)

    unlocated = [
        str(item.get("marker_id") or item.get("file") or "未知图片")
        for i, item in enumerate(raw_images)
        if i not in located and (
            str(item.get("media_type") or "").startswith("image/")
            or (item.get("kind") == "image" and not item.get("media_type"))
        )
    ]
    if unlocated:
        raise EmergencyAuditExtractionError("图片在Markdown中没有位置，无法确定是否属于演练过程之后：" + "、".join(unlocated))
    selected_keys = {_image_key(x.get("file")) or str(x.get("marker_id") or "") for x in selected}
    excluded = [
        {"图片ID": raw_images[i].get("marker_id"), "文件": raw_images[i].get("file"), "原因": "位于演练过程标题之前"}
        for i in sorted(before)
        if (_image_key(raw_images[i].get("file")) or str(raw_images[i].get("marker_id") or "")) not in selected_keys
    ]
    return {**record, "图片": selected}, {
        "图片筛选范围": {"起始章节": section.actual_title, "标题行号": section.title_line,
                         "结束行号": len(lines), "终点": "文末"},
        "范围外图片数量": len(excluded), "范围外图片清单": excluded,
    }


def _safe_local_image(manifest_dir: Path, file_value: str) -> tuple[Path | None, str | None]:
    """Resolve one generated image without allowing paths outside the parsed unit."""

    if not file_value.strip():
        return None, "解析清单未记录图片文件路径"
    relative = Path(file_value)
    if relative.is_absolute():
        return None, "解析清单中的图片路径不是相对路径"
    root = manifest_dir.resolve()
    candidate = (manifest_dir / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None, "解析清单中的图片路径超出当前系统目录"
    if candidate.suffix.casefold() not in _MODEL_IMAGE_SUFFIXES:
        return None, f"图片格式 {candidate.suffix or '未知'} 暂不支持视觉核验"
    if not candidate.is_file():
        return None, "解析出的图片文件不存在"
    return candidate, None


def extract_ylbg_05_data(
    record: Mapping[str, Any], manifest_dir: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """检查已按章节筛选的图片，保留无法核验项，不扩大取图范围。"""

    raw_records = record.get("图片")
    raw_records = raw_records if isinstance(raw_records, list) else []
    checklist: list[dict[str, Any]] = []
    staged: list[dict[str, Any]] = []
    non_images: list[dict[str, Any]] = []

    for raw_index, raw in enumerate(raw_records, start=1):
        if not isinstance(raw, Mapping):
            non_images.append(
                {
                    "清单序号": raw_index,
                    "原因": "图片记录格式异常",
                }
            )
            continue

        kind = str(raw.get("kind") or "").strip().casefold()
        media_type = str(raw.get("media_type") or "").strip().casefold()
        marker_id = str(raw.get("marker_id") or "").strip()
        file_value = str(raw.get("file") or "").strip()
        parser_status = str(raw.get("status") or "").strip()
        # 外链或解析失败的图片可能没有 media_type，但上游仍会用 kind=image
        # 标识其客观上是一张图片。必须把它保留为无法核验，不能当成非图片跳过。
        # 明确的非图片媒体类型（例如 Word OLE）优先于上游可能误填的
        # kind=image；只有 image/*，或 kind=image 且媒体类型缺失时才算图片。
        is_real_image = media_type.startswith("image/") or (
            kind == "image" and not media_type
        )
        if not is_real_image:
            non_images.append(
                {
                    "清单序号": raw_index,
                    "图片ID": marker_id,
                    "文件": file_value,
                    "对象类型": kind or None,
                    "媒体类型": media_type or None,
                    "原因": "嵌入对象不是真实图片",
                }
            )
            continue

        image_number = len(checklist) + 1
        name = str(raw.get("name") or raw.get("alt") or "").strip()
        reason: str | None = None
        source: Path | None = None
        if parser_status != "exported":
            reason = f"图片解析状态为 {parser_status or '未知'}"
        else:
            source, reason = _safe_local_image(manifest_dir, file_value)

        item = {
            "图片序号": image_number,
            "清单序号": raw_index,
            "图片ID": marker_id or f"IMAGE-{image_number:03d}",
            "文件": file_value,
            "图片名称": name,
            "媒体类型": media_type,
            "解析状态": parser_status or None,
            "可核验": reason is None,
            "无法核验原因": reason,
        }
        checklist.append(item)
        if source is not None:
            staged.append(
                {
                    "路径": source,
                    "图片序号": image_number,
                    "图片ID": item["图片ID"],
                    "文件": file_value,
                    "图片名称": name,
                    "说明": f"应急演练报告第{image_number}张图片",
                }
            )

    fields: dict[str, Any] = {
        "图片数量": len(checklist),
        "可核验图片数量": sum(bool(item["可核验"]) for item in checklist),
        "图片清单": checklist,
        "非图片嵌入对象数量": len(non_images),
        "非图片嵌入对象": non_images,
    }
    return fields, staged


@register_extractor(
    "YLBG-05", document_name_contains=("应急材料分析",), file_suffixes=(".json",)
)
def extract_ylbg_05(source: Path) -> dict[str, Any]:
    manifest_path = Path(source)
    report_path, markdown, record = read_current_emergency_markdown(
        manifest_path, "应急演练报告"
    )
    selected_record, scope = _select_process_images(markdown, record)
    fields, images = extract_ylbg_05_data(selected_record, manifest_path.parent)
    fields.update(scope)
    fields["应急演练报告Markdown"] = report_path.name
    stage_audit_artifacts(
        "YLBG-05",
        (manifest_path, report_path),
        fields,
        images=images,
    )
    return fields
