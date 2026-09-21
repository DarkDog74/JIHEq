from __future__ import annotations

import re
from pathlib import Path

from .registry import register_extractor


_NUMBERED_TITLE_RE = re.compile(r"^(?P<number>\d+)[)）](?P<label>.+)$")
_IMAGE_MARKER_RE = re.compile(r"\[\[\s*IMAGE\b[^\]]*\]\]", re.IGNORECASE)


def _parse_numbered_title(line: str) -> tuple[int | None, str] | None:
    """识别正文标题结构，不把目录链接当成正文标题。"""
    candidate = line.strip()
    if not candidate:
        return None

    is_markdown_heading = bool(re.match(r"^#{1,6}\s+", candidate))
    is_bold_list_title = bool(
        re.match(r"^[-*+]\s+", candidate) and ("**" in candidate or "__" in candidate)
    )
    is_bare_bold_title = bool(
        re.fullmatch(r"(?:\*\*.+\*\*|__.+__)", candidate)
    )
    has_title_format = is_markdown_heading or is_bold_list_title or is_bare_bold_title
    if not has_title_format:
        return None

    candidate = re.sub(r"^#{1,6}\s+", "", candidate)
    candidate = re.sub(r"^[-*+]\s+", "", candidate)
    candidate = candidate.replace("\\", "")
    candidate = candidate.replace("**", "").replace("__", "").replace("`", "")
    candidate = re.sub(r"\s+", "", candidate).strip()
    match = _NUMBERED_TITLE_RE.fullmatch(candidate)
    if match is not None:
        return int(match.group("number")), match.group("label")
    return None, candidate


def extract_dj_07_text(markdown: str) -> dict[str, object]:
    lines = markdown.splitlines()
    titles: list[tuple[int, int | None, str]] = []
    for index, line in enumerate(lines):
        parsed = _parse_numbered_title(line)
        if parsed is not None:
            number, label = parsed
            titles.append((index, number, label))

    topology_titles = [title for title in titles if "拓扑" in title[2]]
    boundary_titles = [title for title in titles if "网络边界划分" in title[2]]

    image_count = 0
    title_indexes = [title[0] for title in titles]
    for topology_index, _, _ in topology_titles:
        following_indexes = [index for index in title_indexes if index > topology_index]
        section_end = min(following_indexes) if following_indexes else len(lines)
        section_text = "\n".join(lines[topology_index + 1 : section_end])
        image_count += len(_IMAGE_MARKER_RE.findall(section_text))

    return {
        "网络拓扑结构章节存在": bool(topology_titles),
        "网络拓扑图数量": image_count,
        "网络边界划分章节存在": bool(boundary_titles),
    }


@register_extractor("DJ-07", document_name_contains=("定级报告",))
def extract_dj_07(path: Path) -> dict[str, object]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入 Markdown 不存在：{path}")
    return extract_dj_07_text(path.read_text(encoding="utf-8-sig"))
