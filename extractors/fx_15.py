from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .dj_02 import _parse_heading
from .dj_03 import _normalize_label
from .errors import ExtractorNotApplicableError, TargetSectionNotFoundError
from .registry import register_extractor


class FX15ExtractionError(ValueError):
    """FX-15 的风险计算方法章节无法可靠提取。"""


class FX15SectionNotFoundError(FX15ExtractionError, TargetSectionNotFoundError):
    """风险评估报告中不存在“风险计算方法”标题。"""


class FX15IncompleteMarkdownError(FX15ExtractionError, ExtractorNotApplicableError):
    """风险评估报告 Markdown 明显解析不完整。"""


_METHODS = ("矩阵法", "相乘法")
_NUMBERED_HEADING_RE = re.compile(r"^\d+(?:[.．]\d+)*[.．、]?")


def _normalize_heading(title: str) -> str:
    return _NUMBERED_HEADING_RE.sub("", _normalize_label(title))


def _risk_calculation_section(markdown: str) -> tuple[list[str], int, str]:
    lines = markdown.splitlines()
    matches: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        heading = _parse_heading(line)
        if heading is None:
            continue
        level, title = heading
        if _normalize_heading(title) == "风险计算方法":
            matches.append((index, level, title))

    if not matches:
        if re.search(r"<!--\s*PARSE_ERROR\b", markdown, re.IGNORECASE):
            raise FX15IncompleteMarkdownError(
                "风险评估报告 Markdown 已标记 PARSE_ERROR，解析结果不可用"
            )
        nonempty_count = sum(bool(line.strip()) for line in lines)
        if nonempty_count <= 10:
            raise FX15IncompleteMarkdownError(
                f"风险评估报告 Markdown 仅有{nonempty_count}行非空内容，解析结果不完整"
            )
        raise FX15SectionNotFoundError(
            "风险评估报告中未找到标题“风险计算方法”"
        )
    if len(matches) > 1:
        locations = "、".join(str(index + 1) for index, _, _ in matches)
        raise FX15ExtractionError(
            f"风险评估报告中发现多个标题“风险计算方法”，行号：{locations}"
        )

    start, level, title = matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        heading = _parse_heading(lines[index])
        if heading is not None and heading[0] <= level:
            end = index
            break
    return lines[start + 1 : end], start + 1, title


def extract_fx_15_text(markdown: str) -> dict[str, Any]:
    section_lines, heading_line, title = _risk_calculation_section(markdown)
    checks: list[dict[str, Any]] = []
    matched_methods: list[str] = []
    for method in _METHODS:
        line_numbers = [
            heading_line + index + 1
            for index, line in enumerate(section_lines)
            if method in re.sub(r"\s+", "", line)
        ]
        if line_numbers:
            matched_methods.append(method)
        checks.append(
            {
                "方法": method,
                "存在": bool(line_numbers),
                "Markdown行号": line_numbers,
            }
        )

    return {
        "风险计算方法章节标题": title,
        "风险计算方法章节标题行": heading_line,
        "命中的风险计算方法": matched_methods,
        "方法检查": checks,
    }


@register_extractor("FX-15", document_name_contains=("风险评估报告.md",))
def extract_fx_15(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入风险评估报告 Markdown 不存在：{path}")
    return extract_fx_15_text(path.read_text(encoding="utf-8-sig"))
