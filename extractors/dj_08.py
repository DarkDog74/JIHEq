from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import (
    normalize_heading_title,
    parse_image_refs,
    parse_markdown_heading,
    resolve_image_path,
    slice_section_by_heading,
    stage_audit_artifacts,
)
from .dj_09 import extract_asset_table_text
from .errors import ExtractorNotApplicableError
from .registry import register_extractor


class DJ08ExtractionError(ExtractorNotApplicableError):
    """DJ-08 拓扑图或定级报告内嵌资产表无法可靠提取（归"提取异常"而非"程序错误"）。"""


_BULLET_LABEL_RE = re.compile(r"^[-*+]\s*\d+\s*[)）]")


def _topology_ref(markdown: str) -> tuple[dict[str, Any], int] | None:
    """定位“技术情况 → 网络拓扑结构”小节内的第一个 IMAGE 引用。"""
    lines = markdown.splitlines()
    refs = {ref["行号"]: ref for ref in parse_image_refs(markdown)}

    search_ranges: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        heading = parse_markdown_heading(line)
        if heading is not None and normalize_heading_title(heading[1]) == "技术情况":
            search_ranges.append(
                (index + 1, index + 1 + len(slice_section_by_heading(lines, index, heading[0])))
            )
            break
    # 兜底：标题缺失时全文找“网络拓扑结构”。
    search_ranges.append((0, len(lines)))

    for start, end in search_ranges:
        anchor = next(
            (
                position
                for position in range(start, end)
                if "网络拓扑结构" in lines[position]
            ),
            None,
        )
        if anchor is None:
            continue
        for position in range(anchor, min(anchor + 20, end)):
            ref = refs.get(position + 1)
            if ref is not None:
                return ref, anchor
            line = lines[position].strip()
            if position > anchor and (_BULLET_LABEL_RE.match(line) or line.startswith("#")):
                break
    return None


def extract_dj_08_text(markdown: str) -> dict[str, Any]:
    topology = _topology_ref(markdown)
    if topology is None:
        raise DJ08ExtractionError("定级报告未找到“网络拓扑结构”小节或其中的拓扑图图片")
    ref, _anchor = topology

    rows = extract_asset_table_text(markdown, "定级报告", prefer_last=True)
    ips: list[str] = []
    for row in rows:
        ip = row["资产IP"].strip()
        if ip and ip not in ips:
            ips.append(ip)

    return {
        "拓扑图": {
            "id": ref["id"],
            "文件": ref["file"],
            "alt": ref["alt"],
            "所在小节": "技术情况 / 网络拓扑结构",
        },
        "资产IP清单": ips,
        "资产数量": len(rows),
        "资产表说明": "完整资产表见定级报告原文（DJ-09 已单独存档），此处仅保留比对用 IP 清单",
    }


@register_extractor("DJ-08", document_name_contains=("定级报告",))
def extract_dj_08(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入定级报告不存在：{path}")
    fields = extract_dj_08_text(path.read_text(encoding="utf-8-sig"))

    image_path = resolve_image_path({"file": fields["拓扑图"]["文件"]}, path.parent)
    stage_audit_artifacts(
        "DJ-08",
        path,
        fields,
        images=[{"路径": image_path, "id": fields["拓扑图"]["id"], "说明": "定级报告网络拓扑图"}],
    )
    return fields
