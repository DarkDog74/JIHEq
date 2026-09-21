from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .dj_02 import _parse_heading
from .dj_04 import _is_separator_row, _split_markdown_row
from .errors import TargetSectionNotFoundError
from .registry import register_extractor


class FX16ExtractionError(ValueError):
    """FX-16 的安全风险计算结果附录无法可靠提取。"""


class FX16AppendixNotFoundError(FX16ExtractionError, TargetSectionNotFoundError):
    """风险评估报告中不存在安全风险计算结果附录。"""


_TITLE_RE = re.compile(
    r"附\s*录\s*(?P<label>[A-Za-zＡ-Ｚａ-ｚ]?)\s*"
    r"安\s*全\s*风\s*险\s*计\s*算\s*结\s*果"
)
_APPENDIX_RE = re.compile(r"附\s*录\s*[A-Za-zＡ-Ｚａ-ｚ]")
_REFERENCE_END_RE = re.compile(r"(?:详见|参见|见)\s*$")
_RISK_LEVEL_RE = re.compile(r"很\s*高|中\s*等|很\s*低|高\s*风\s*险|低\s*风\s*险|(?<!很)低")
_NUMBER_RE = re.compile(r"(?<![\d.])\d+(?:\.\d+)?(?![\d.])")
_PAGE_RE = re.compile(r"第\s*\d+\s*页\s*共\s*\d+\s*页")
_TOC_LINK_RE = re.compile(r"^\s*\[[^\]]+\]\s*\(\s*#[^)]+\)\s*$")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", value.replace("**", "").replace("__", ""))


def _title_candidate(line: str) -> tuple[re.Match[str], int | None, str] | None:
    """返回真实附录标题；排除目录点线和“见附录”正文引用。"""
    if _TOC_LINK_RE.fullmatch(line):
        return None
    heading = _parse_heading(line)
    heading_level = heading[0] if heading is not None else None
    candidate = heading[1] if heading is not None else line.strip()
    for match in _TITLE_RE.finditer(candidate):
        prefix = candidate[: match.start()]
        suffix = candidate[match.end() :]
        if re.search(r"\.{5,}", suffix):
            continue
        if _REFERENCE_END_RE.search(prefix):
            continue
        method = (
            "Markdown标题"
            if heading is not None
            else "PDF压平标题"
            if "|" in line or prefix.strip() or suffix.strip()
            else "独立行标题"
        )
        return match, heading_level, method
    return None


def _is_next_appendix_boundary(line: str) -> bool:
    if _TOC_LINK_RE.fullmatch(line):
        return False
    for match in _APPENDIX_RE.finditer(line):
        prefix = line[: match.start()]
        suffix = line[match.end() :]
        if re.search(r"\.{5,}", suffix) or _REFERENCE_END_RE.search(prefix):
            continue
        return True
    return False


def _looks_like_result_data(line: str) -> bool:
    candidate = _PAGE_RE.sub("", line).strip()
    if not candidate:
        return False
    cells = _split_markdown_row(candidate)
    if cells is not None:
        if _is_separator_row(cells) or not any(cell.strip() for cell in cells):
            return False
        candidate = " ".join(cell for cell in cells if cell.strip())

    numbers = _NUMBER_RE.findall(candidate)
    has_risk_level = _RISK_LEVEL_RE.search(candidate) is not None
    # 结构化行一般含序号和风险等级；严重压平的普通文本行至少保留大量数值。
    return (bool(numbers) and has_risk_level) or len(numbers) >= 6


def extract_fx_16_text(markdown: str) -> dict[str, Any]:
    lines = markdown.splitlines()
    matches: list[tuple[int, re.Match[str], int | None, str]] = []
    for index, line in enumerate(lines):
        candidate = _title_candidate(line)
        if candidate is not None:
            match, level, method = candidate
            matches.append((index, match, level, method))

    if not matches:
        raise FX16AppendixNotFoundError(
            "风险评估报告中未找到“附录X 安全风险计算结果”标题"
        )
    if len(matches) > 1:
        locations = "、".join(str(index + 1) for index, *_ in matches)
        raise FX16ExtractionError(
            f"风险评估报告中存在多个安全风险计算结果附录标题，行号：{locations}"
        )

    start, title_match, heading_level, method = matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        heading = _parse_heading(lines[index])
        if (
            heading_level is not None
            and heading is not None
            and heading[0] <= heading_level
        ) or _is_next_appendix_boundary(lines[index]):
            end = index
            break

    data_rows: list[int] = []
    # title_match 可能基于去掉 Markdown 标记后的标题文本，不能直接拿它的偏移
    # 切原始行；重新在原始行定位，才能可靠检查压平在标题同一行的数据。
    original_title_match = _TITLE_RE.search(lines[start])
    title_tail = (
        lines[start][original_title_match.end() :]
        if original_title_match is not None
        else ""
    )
    if _looks_like_result_data(title_tail):
        data_rows.append(start + 1)
    for index in range(start + 1, end):
        if _looks_like_result_data(lines[index]):
            data_rows.append(index + 1)

    title_text = _normalize(title_match.group(0))
    return {
        "安全风险计算结果附录标题": title_text,
        "附录标题Markdown行号": start + 1,
        "附录标题识别方式": method,
        "有内容的数据行": data_rows,
    }


@register_extractor("FX-16", document_name_contains=("风险评估报告.md",))
def extract_fx_16(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入风险评估报告 Markdown 不存在：{path}")
    return extract_fx_16_text(path.read_text(encoding="utf-8-sig"))
