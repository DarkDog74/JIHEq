from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    parse_image_refs,
    parse_markdown_heading,
    resolve_image_path,
    stage_audit_artifacts,
)
from .dj_09 import extract_asset_table_text
from .registry import register_extractor


class FX06ExtractionError(ValueError):
    """FX-06 风险评估报告图片或资产清单无法可靠提取。"""


def extract_fx_06_data(risk_markdown: str, inventory_markdown: str) -> dict[str, Any]:
    lines = risk_markdown.splitlines()
    current_section = ""
    images: list[dict[str, Any]] = []
    for line_index, line in enumerate(lines):
        heading = parse_markdown_heading(line)
        if heading is not None:
            current_section = heading[1]
        for ref in parse_image_refs(line):
            images.append(
                {
                    "id": ref["id"],
                    "文件": ref["file"],
                    "alt": ref["alt"],
                    "所在章节": current_section,
                    "Markdown行号": line_index + 1,
                    "_line_index": line_index,
                }
            )
    if not images:
        raise FX06ExtractionError("风险评估报告中未找到任何图片引用")

    topology_ranges: list[tuple[int, int, str]] = []
    for start_index, line in enumerate(lines):
        if "拓扑图" not in line:
            continue
        end_index = next(
            (
                following_index
                for following_index in range(start_index + 1, len(lines))
                if parse_markdown_heading(lines[following_index]) is not None
            ),
            len(lines),
        )
        topology_ranges.append((start_index, end_index, line.strip()))

    topology_images: list[dict[str, Any]] = []
    seen_topology_images: set[tuple[str, str, int]] = set()
    for start_index, end_index, anchor_text in topology_ranges:
        for image in images:
            image_index = int(image["_line_index"])
            if not start_index <= image_index < end_index:
                continue
            identity = (
                str(image.get("id") or ""),
                str(image.get("文件") or ""),
                int(image.get("Markdown行号") or 0),
            )
            if identity in seen_topology_images:
                continue
            seen_topology_images.add(identity)
            topology_images.append(
                {
                    key: value
                    for key, value in image.items()
                    if key != "_line_index"
                }
                | {
                    "定位文字": anchor_text,
                    "定位行号": start_index + 1,
                }
            )

    report_images = [
        {key: value for key, value in image.items() if key != "_line_index"}
        for image in images
    ]

    rows = extract_asset_table_text(inventory_markdown, "资产清单")
    ips: list[str] = []
    for row in rows:
        ip = row["资产IP"].strip()
        if ip and ip not in ips:
            ips.append(ip)

    return {
        "报告图片": report_images,
        "拓扑图": topology_images,
        "资产IP清单": ips,
        "资产数量": len(rows),
        "资产表": rows,
    }


@register_extractor(
    "FX-06",
    document_name_contains=("风险评估报告",),
    additional_document_name_groups=(("资产清单",),),
)
def extract_fx_06(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(source, tuple) or len(source) != 2:
        raise FX06ExtractionError("FX-06 需要风险评估报告 Markdown 和资产清单 Markdown")
    risk_path, inventory_path = source
    if not risk_path.is_file():
        raise FileNotFoundError(f"输入风险评估报告不存在：{risk_path}")
    if not inventory_path.is_file():
        raise FileNotFoundError(f"输入资产清单不存在：{inventory_path}")

    fields = extract_fx_06_data(
        risk_path.read_text(encoding="utf-8-sig"),
        inventory_path.read_text(encoding="utf-8-sig"),
    )
    staged = [
        {
            "路径": resolve_image_path({"file": image["文件"]}, risk_path.parent),
            "id": image["id"],
            "说明": image.get("定位文字") or image["所在章节"],
        }
        for image in fields["拓扑图"]
    ]
    stage_audit_artifacts("FX-06", source, fields, images=staged)
    return fields
