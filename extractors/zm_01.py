from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import parse_image_refs, resolve_image_path, stage_audit_artifacts
from .registry import register_extractor


class ZM01ExtractionError(ValueError):
    """ZM-01 证明材料的检查项结构无法可靠提取。"""


_ITEM_CODE_RE = re.compile(r"(?<![\d-])(?P<code>\d{2}-\d{2}-\d{2}-\d{2}-\d{2})(?![\d-])")
_INLINE_IMAGE_MD_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _clean_description(block: str) -> str:
    text = _INLINE_IMAGE_MD_RE.sub("", block)
    text = _ITEM_CODE_RE.sub("", text, count=1)
    text = re.sub(r"\|", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" -|")


def extract_zm_01_text(markdown: str) -> dict[str, Any]:
    matches = list(_ITEM_CODE_RE.finditer(markdown))
    if not matches:
        raise ZM01ExtractionError("证明材料中未找到检查项编号（形如 23-01-01-01-01）")

    items: list[dict[str, Any]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        block = markdown[match.start() : end]
        screenshots = [
            {"id": ref["id"], "文件": ref["file"], "alt": ref["alt"]}
            for ref in parse_image_refs(block)
        ]
        items.append(
            {
                "检查项编号": match.group("code"),
                "说明文字": _clean_description(block),
                "截图": screenshots,
            }
        )

    with_screenshots = sum(1 for item in items if item["截图"])
    return {
        "检查项": items,
        "检查项数量": len(items),
        "含截图检查项数量": with_screenshots,
        "无截图检查项编号": [
            item["检查项编号"] for item in items if not item["截图"]
        ],
    }


@register_extractor("ZM-01", document_name_contains=("符合性评测表证明材料",))
def extract_zm_01(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入证明材料 Markdown 不存在：{path}")
    fields = extract_zm_01_text(path.read_text(encoding="utf-8-sig"))

    staged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in fields["检查项"]:
        for screenshot in item["截图"]:
            if screenshot["文件"] in seen:
                continue
            seen.add(screenshot["文件"])
            staged.append(
                {
                    "路径": resolve_image_path(
                        {"file": screenshot["文件"]}, path.parent
                    ),
                    "id": screenshot["id"],
                    "说明": f"检查项 {item['检查项编号']} 截图",
                }
            )
    stage_audit_artifacts("ZM-01", path, fields, images=staged)
    return fields
