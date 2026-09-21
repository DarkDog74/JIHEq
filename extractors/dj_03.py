from __future__ import annotations

import html
import re
from pathlib import Path

from .errors import ExtractorNotApplicableError
from .registry import register_extractor


class DJ03ExtractionError(ValueError):
    """DJ-03 页眉或文档名称无法可靠提取。"""


class DJ03NotApplicableError(DJ03ExtractionError, ExtractorNotApplicableError):
    """输入文档不包含 DJ-03 所需字段。"""


_HEADER_COMMENT_RE = re.compile(
    r"^\s*<!--\s*页\s*眉\s*[：:]\s*(?P<value>.*?)\s*-->\s*$",
    re.IGNORECASE,
)
_HTML_COMMENT_RE = re.compile(r"^\s*<!--.*?-->\s*$")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HEADER_VARIANT_RE = re.compile(
    r"^(?:第(?P<section>\d+)节)?"
    r"(?P<header_type>默认页眉|首页页眉|偶数页眉)="
    r"(?P<value>.+)$"
)


def _clean_text(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = _HTML_TAG_RE.sub("", value)
    value = value.replace("**", "").replace("__", "").replace("`", "")
    value = re.sub(r"^#{1,6}\s+", "", value.strip())
    return re.sub(r"\s+", " ", value).strip()


def _normalize_label(value: str) -> str:
    return re.sub(r"\s+", "", _clean_text(value))


def _split_markdown_row(line: str) -> list[str] | None:
    candidate = line.strip()
    if not candidate.startswith("|"):
        return None

    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for character in candidate[1:]:
        if escaped:
            current.extend(("\\", character))
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "|":
            cells.append(_clean_text("".join(current)).replace(r"\|", "|"))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    if current:
        cells.append(_clean_text("".join(current)).replace(r"\|", "|"))
    return cells


def _select_first_page_header(value: str) -> str:
    """从多节页眉摘要中只选择第一页实际使用的页眉。"""
    parts = [part.strip() for part in re.split(r"[；;]", value) if part.strip()]
    variants: list[tuple[int, str, str]] = []
    for part in parts:
        match = _HEADER_VARIANT_RE.fullmatch(part)
        if match is None:
            return value
        variants.append(
            (
                int(match.group("section") or 1),
                match.group("header_type"),
                match.group("value").strip(),
            )
        )

    if not variants:
        return value

    first_section = min(section for section, _, _ in variants)
    first_section_variants = [
        (header_type, header_value)
        for section, header_type, header_value in variants
        if section == first_section
    ]
    # Word 启用“首页不同”时，第一页使用首页页眉；否则使用默认页眉。
    for preferred_type in ("首页页眉", "默认页眉", "偶数页眉"):
        for header_type, header_value in first_section_variants:
            if header_type == preferred_type:
                return header_value
    return value


def _extract_header(lines: list[str]) -> str:
    first_nonempty = next((line for line in lines if line.strip()), None)
    if first_nonempty is None:
        raise DJ03NotApplicableError("文档为空，无法提取第一页页眉")

    comment_match = _HEADER_COMMENT_RE.fullmatch(first_nonempty)
    if comment_match is not None:
        value = _select_first_page_header(_clean_text(comment_match.group("value")))
    elif _HTML_COMMENT_RE.fullmatch(first_nonempty):
        raise DJ03NotApplicableError("第一行不是“页眉：...”字段")
    else:
        # 兼容未保留页眉注释的旧解析结果：第一条非空正文即封面页眉标题。
        value = _clean_text(first_nonempty)

    if not value:
        raise DJ03ExtractionError("第一页页眉内容为空")
    return value


def _extract_document_name(lines: list[str]) -> str:
    candidates: list[tuple[int, str]] = []
    for line_number, line in enumerate(lines, start=1):
        cells = _split_markdown_row(line)
        if cells is None:
            continue
        for index, cell in enumerate(cells):
            if _normalize_label(cell) != "文档名称":
                continue
            value = next((candidate for candidate in cells[index + 1 :] if candidate), "")
            if not value:
                raise DJ03ExtractionError(f"Markdown 第{line_number}行的文档名称为空")
            candidates.append((line_number, value))

    if not candidates:
        raise DJ03NotApplicableError("未找到封面表格中的“文档名称”字段")

    normalized_values = {
        re.sub(r"[\s\u200b\ufeff]+", "", value): value
        for _, value in candidates
    }
    if len(normalized_values) > 1:
        locations = "、".join(str(line_number) for line_number, _ in candidates)
        raise DJ03ExtractionError(f"发现多个不同的文档名称，Markdown 行号：{locations}")
    return candidates[0][1]


def extract_dj_03_text(markdown: str) -> dict[str, str]:
    lines = markdown.splitlines()
    return {
        "页眉": _extract_header(lines),
        "文档名称": _extract_document_name(lines),
    }


@register_extractor("DJ-03", document_name_contains=("定级报告",))
def extract_dj_03(path: Path) -> dict[str, str]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入 Markdown 不存在：{path}")
    return extract_dj_03_text(path.read_text(encoding="utf-8-sig"))
