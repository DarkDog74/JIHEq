from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .dj_02 import _parse_heading
from .dj_03 import _normalize_label, _split_markdown_row
from .errors import ExtractorNotApplicableError, TargetSectionNotFoundError
from .registry import register_extractor


class FX09ExtractionError(ValueError):
    """FX-09 的威胁识别及赋值表无法可靠提取。"""


class FX09SectionNotFoundError(FX09ExtractionError, TargetSectionNotFoundError):
    """风险评估报告中不存在“威胁识别及赋值表”标题。"""


class FX09TableHeaderNotFoundError(FX09ExtractionError, ExtractorNotApplicableError):
    """目标标题范围内没有可识别的威胁表表头。"""


class FX09IncompleteMarkdownError(FX09ExtractionError, ExtractorNotApplicableError):
    """风险评估报告 Markdown 明显解析不完整。"""


_REQUIRED_COLUMNS = ("威胁来源", "威胁种类", "威胁行为")
_APPENDIX_PREFIX_RE = re.compile(
    r"^附录[A-Za-zＡ-Ｚａ-ｚ0-9一二三四五六七八九十]*[.．、:：\-—]?"
)
_PLAIN_TARGET_RE = re.compile(
    r"^\s*(?P<title>(?:附录\s*[A-Za-zＡ-Ｚａ-ｚ0-9一二三四五六七八九十]+\s*)?"
    r"威胁\s*识别\s*及\s*赋值表)(?P<rest>.*)$"
)
_PLAIN_APPENDIX_RE = re.compile(
    r"^\s*附录\s*[A-Za-zＡ-Ｚａ-ｚ0-9一二三四五六七八九十]+(?:\s|[.．、:：])"
)
_TOC_LEADER_RE = re.compile(r"(?:\.{4,}|…{2,}|·{4,})")


def _is_target_heading(title: str) -> bool:
    label = _normalize_label(title)
    label = _APPENDIX_PREFIX_RE.sub("", label)
    return label == "威胁识别及赋值表"


def _target_section(markdown: str) -> tuple[list[str], int, int, str]:
    lines = markdown.splitlines()
    matches: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        heading = _parse_heading(line)
        if heading is None:
            continue
        level, title = heading
        if _is_target_heading(title):
            matches.append((index, level, title))

    if len(matches) > 1:
        locations = "、".join(str(index + 1) for index, _, _ in matches)
        raise FX09ExtractionError(
            f"风险评估报告中发现多个“威胁识别及赋值表”标题，行号：{locations}"
        )
    if matches:
        start, level, title = matches[0]
        end = len(lines)
        for index in range(start + 1, len(lines)):
            heading = _parse_heading(lines[index])
            if heading is not None and heading[0] <= level:
                end = index
                break
        heading_line = start + 1
        return lines[start + 1 : end], heading_line, heading_line, title

    # PDF/Word 的旧解析结果可能不保留 Markdown 标题，并把表题、表头和数据
    # 压到同一行。只接受行首完整表题，排除目录点线和正文中的“见附录B”引用。
    plain_matches: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith("[") or _TOC_LEADER_RE.search(line):
            continue
        match = _PLAIN_TARGET_RE.match(line)
        if match is not None:
            plain_matches.append((index, match.group("title").strip()))
            continue
        row = _split_markdown_row(line)
        if row is not None:
            title_cell = next((cell for cell in row if _is_target_heading(cell)), None)
            if title_cell is not None:
                plain_matches.append((index, title_cell))

    if not plain_matches:
        nonempty_count = sum(bool(line.strip()) for line in lines)
        if nonempty_count <= 10:
            raise FX09IncompleteMarkdownError(
                f"风险评估报告 Markdown 仅有{nonempty_count}行非空内容，解析结果不完整"
            )
        raise FX09SectionNotFoundError(
            "风险评估报告中未找到标题“威胁识别及赋值表”"
        )
    # 跨页表格可能在每页重复相同表题；从首次表题开始统一检查整段。
    start, title = plain_matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _parse_heading(lines[index]) is not None:
            end = index
            break
        if _PLAIN_APPENDIX_RE.match(lines[index]) and not _PLAIN_TARGET_RE.match(lines[index]):
            end = index
            break
        row = _split_markdown_row(lines[index])
        if row is not None and any(
            _normalize_label(cell).startswith("附录") and not _is_target_heading(cell)
            for cell in row
        ):
            end = index
            break
    heading_line = start + 1
    # 包含标题本行，以兼容“表题 + 表头 + 数据”被压成同一行的情况。
    return lines[start:end], heading_line - 1, heading_line, title


def _plain_column_presence(value: str) -> dict[str, bool]:
    label = _normalize_label(value)
    return {
        "威胁来源": "威胁来源" in label,
        # Word 多行表头被扁平化时，列名可能交错成“威胁种 … 类”；
        # “威胁种”没有其他常见表头含义，可作为该列的保守证据。
        "威胁种类": "威胁种类" in label or "威胁种" in label,
        # “威胁行为能力赋值”不是“威胁行为”列。
        "威胁行为": re.search(r"威胁行为(?!能力)", label) is not None,
    }


def _candidate_score(value: str, presence: dict[str, bool]) -> tuple[int, int]:
    label = _normalize_label(value)
    looks_like_threat_header = (
        "威胁" in label and "序号" in label and ("资产" in label or "来源" in label)
    )
    return sum(presence.values()), int(looks_like_threat_header)


def _extract_header(
    section_lines: list[str], section_heading_line: int
) -> tuple[str, int, dict[str, bool], list[str]]:
    candidates: list[
        tuple[tuple[int, int], int, int, str, dict[str, bool], list[str]]
    ] = []

    for index, line in enumerate(section_lines):
        row = _split_markdown_row(line)
        if row is None:
            if not line.strip():
                continue
            presence = _plain_column_presence(line)
            score = _candidate_score(line, presence)
            if score[1] or score[0] >= 2:
                candidates.append(
                    (score, -index, index, "扁平化表头文本", presence, [])
                )
            continue

        normalized_cells = [_normalize_label(cell) for cell in row]
        presence = {
            column: column in normalized_cells for column in _REQUIRED_COLUMNS
        }
        combined = "|".join(row)
        score = _candidate_score(combined, presence)
        if score[1] or score[0] >= 2:
            candidates.append(
                (score, -index, index, "Markdown表格", presence, row)
            )

    if not candidates:
        raise FX09TableHeaderNotFoundError(
            "“威胁识别及赋值表”标题范围内未识别到威胁表表头"
        )

    _, _, relative_index, source_type, presence, cells = max(candidates)
    markdown_line = section_heading_line + relative_index + 1
    return source_type, markdown_line, presence, cells


def extract_fx_09_text(markdown: str) -> dict[str, Any]:
    section_lines, section_line_base, heading_line, title = _target_section(markdown)
    source_type, header_line, presence, cells = _extract_header(
        section_lines, section_line_base
    )
    return {
        "目标表标题": title,
        "目标表标题行": heading_line,
        "表头来源": source_type,
        "表头行": header_line,
        "识别到的表头列": cells,
        "列检查": [
            {
                "列名": column,
                "存在": presence[column],
                "Markdown行号": [header_line] if presence[column] else [],
            }
            for column in _REQUIRED_COLUMNS
        ],
    }


@register_extractor("FX-09", document_name_contains=("风险评估报告.md",))
def extract_fx_09(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入风险评估报告 Markdown 不存在：{path}")
    return extract_fx_09_text(path.read_text(encoding="utf-8-sig"))
