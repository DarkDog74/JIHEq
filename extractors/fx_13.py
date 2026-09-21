from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .dj_02 import _parse_heading
from .dj_04 import _is_separator_row, _normalize_label, _split_markdown_row
from .errors import ExtractorNotApplicableError, TargetSectionNotFoundError
from .registry import register_extractor


class FX13ExtractionError(ValueError):
    """FX-13 的已有安全措施分析表无法可靠提取。"""


class FX13TableNotFoundError(FX13ExtractionError, TargetSectionNotFoundError):
    """风险评估报告中没有已有安全措施分析表。"""


class FX13IncompleteMarkdownError(FX13ExtractionError, ExtractorNotApplicableError):
    """风险评估报告 Markdown 明确记录了解析失败。"""


class FX13TableStructureError(FX13ExtractionError, ExtractorNotApplicableError):
    """目标表存在，但没有可供稽核的表格结构。"""


_TABLE_TITLE = "已有安全措施分析表"
_TARGET_KEYWORD = "保护性安全措施"
_APPENDIX_PREFIX_RE = re.compile(
    r"^附录[A-ZＡ-Ｚ0-9一二三四五六七八九十]*[.．、:：\-—]?"
)
_PLAIN_APPENDIX_RE = re.compile(
    r"^附录\s*[A-ZＡ-Ｚ0-9一二三四五六七八九十]+(?:\s|[.．、:：])"
)
_TOC_LEADER_RE = re.compile(r"(?:\.{4,}|…{2,}|·{4,})")


def _is_target_title(value: str) -> bool:
    label = _APPENDIX_PREFIX_RE.sub("", _normalize_label(value))
    return label == _TABLE_TITLE


def _is_collapsed_target_row(line: str) -> bool:
    cells = _split_markdown_row(line)
    if cells is None:
        return False
    combined = _normalize_label(" ".join(cells))
    return (
        _TABLE_TITLE in combined
        and "已有安全措施" in combined
        and "类型" in combined
    )


def _section_end(lines: list[str], start: int, *, heading_level: int | None) -> int:
    for index in range(start + 1, len(lines)):
        heading = _parse_heading(lines[index])
        if heading is not None:
            if heading_level is None or heading[0] <= heading_level:
                return index
            continue

        cells = _split_markdown_row(lines[index])
        candidates = cells if cells is not None else [lines[index].strip()]
        for value in candidates:
            label = _normalize_label(value)
            if label.startswith("附录") and not _is_target_title(value):
                return index
        if _PLAIN_APPENDIX_RE.match(lines[index].strip()) and not _is_target_title(
            lines[index]
        ):
            return index
    return len(lines)


def _target_section(
    markdown: str,
) -> tuple[list[tuple[int, str]], int, str, str]:
    if "<!-- PARSE_ERROR" in markdown or "风险评估报告解析失败" in markdown:
        raise FX13IncompleteMarkdownError(
            "风险评估报告 Markdown 标记为解析失败，无法检查已有安全措施分析表"
        )

    lines = markdown.splitlines()
    heading_matches: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        heading = _parse_heading(line)
        if heading is not None and _is_target_title(heading[1]):
            heading_matches.append((index, heading[0], heading[1]))

    if len(heading_matches) > 1:
        locations = "、".join(str(index + 1) for index, _, _ in heading_matches)
        raise FX13ExtractionError(
            f"风险评估报告中发现多个“{_TABLE_TITLE}”标题，行号：{locations}"
        )
    if heading_matches:
        start, level, title = heading_matches[0]
        end = _section_end(lines, start, heading_level=level)
        section = [(index + 1, lines[index]) for index in range(start + 1, end)]
        return section, start + 1, title, "Markdown标题"

    plain_matches: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("[") or _TOC_LEADER_RE.search(line):
            continue
        if _is_target_title(stripped):
            plain_matches.append((index, stripped, "独占行表题"))
        elif _is_collapsed_target_row(line):
            plain_matches.append((index, _TABLE_TITLE, "压平表格行"))
        elif (cells := _split_markdown_row(line)) is not None and any(
            _is_target_title(cell) for cell in cells
        ):
            plain_matches.append((index, _TABLE_TITLE, "表格内独立表题"))

    if not plain_matches:
        raise FX13TableNotFoundError(
            f"风险评估报告中未找到“{_TABLE_TITLE}”"
        )
    if len(plain_matches) > 1:
        locations = "、".join(str(index + 1) for index, _, _ in plain_matches)
        raise FX13ExtractionError(
            f"风险评估报告中发现多个“{_TABLE_TITLE}”表题，行号：{locations}"
        )

    start, title, source = plain_matches[0]
    end = _section_end(lines, start, heading_level=None)
    first = start if source == "压平表格行" else start + 1
    section = [(index + 1, lines[index]) for index in range(first, end)]
    return section, start + 1, title, source


def _flattened_type_value(cells: list[str]) -> str:
    for cell in cells:
        label = _normalize_label(cell)
        if "类型" not in label or label == "类型":
            continue
        value = label.split("类型", 1)[1].split("功能描述", 1)[0]
        if value:
            return value
    return ""


def _extract_type_column(
    section: list[tuple[int, str]],
    *,
    target_keyword: str = _TARGET_KEYWORD,
) -> tuple[bool, list[dict[str, Any]]]:
    saw_table_row = False
    type_column_exists = False
    current_type_index: int | None = None
    evidence: list[dict[str, Any]] = []

    for line_number, line in section:
        cells = _split_markdown_row(line)
        if cells is None or _is_separator_row(cells):
            continue
        saw_table_row = True
        labels = [_normalize_label(cell) for cell in cells]
        type_indexes = [index for index, label in enumerate(labels) if label == "类型"]
        if type_indexes:
            if len(type_indexes) > 1:
                raise FX13ExtractionError(
                    f"已有安全措施分析表在 Markdown 第{line_number}行重复出现“类型”列"
                )
            current_type_index = type_indexes[0]
            type_column_exists = True
            continue

        if current_type_index is not None:
            value = cells[current_type_index].strip() if current_type_index < len(cells) else ""
            if not evidence and target_keyword in _normalize_label(value):
                evidence.append(
                    {
                        "关键词": target_keyword,
                        "Markdown行号": line_number,
                        "来源": "Markdown表格类型列",
                    }
                )
            continue

        flattened_value = _flattened_type_value(cells)
        if flattened_value:
            type_column_exists = True
            if not evidence and target_keyword in flattened_value:
                evidence.append(
                    {
                        "关键词": target_keyword,
                        "Markdown行号": line_number,
                        "来源": "压平表格类型字段",
                    }
                )

    if not saw_table_row:
        raise FX13TableStructureError(
            "“已有安全措施分析表”标题范围内没有 Markdown 表格"
        )
    return type_column_exists, evidence


def extract_fx_13_text(markdown: str) -> dict[str, Any]:
    section, title_line, title, source = _target_section(markdown)
    type_column_exists, keyword_evidence = _extract_type_column(
        section, target_keyword=_TARGET_KEYWORD
    )
    return {
        "目标表标题": title,
        "目标表标题行": title_line,
        "定位方式": source,
        "类型列存在": type_column_exists,
        "保护性安全措施证据": keyword_evidence,
    }


def _extract_measure_type_documents(
    primary_path: Path,
    *,
    text_extractor: Any,
    evidence_field: str,
) -> dict[str, Any]:
    primary_path = Path(primary_path)
    if not primary_path.is_file():
        raise FileNotFoundError(f"输入风险评估报告 Markdown 不存在：{primary_path}")

    report_paths = [primary_path]
    usable: list[tuple[str, dict[str, Any]]] = []
    checks: list[dict[str, Any]] = []
    for path in report_paths:
        try:
            fields = text_extractor(path.read_text(encoding="utf-8-sig"))
        except FX13IncompleteMarkdownError as error:
            checks.append({"文件": path.name, "状态": "解析失败", "错误": str(error)})
        except FX13TableNotFoundError as error:
            checks.append({"文件": path.name, "状态": "无目标表", "错误": str(error)})
        else:
            usable.append((path.name, fields))
            checks.append(
                {
                    "文件": path.name,
                    "状态": "可用",
                    "类型列存在": fields["类型列存在"],
                    "关键词证据数量": len(fields[evidence_field]),
                }
            )

    if not usable:
        parse_error_count = sum(item["状态"] == "解析失败" for item in checks)
        if parse_error_count == len(checks):
            raise FX13IncompleteMarkdownError(
                "风险评估报告全部为解析失败存根，无法检查已有安全措施分析表"
            )
        raise FX13TableNotFoundError(
            "所有可读取的风险评估报告中均未找到“已有安全措施分析表”"
        )

    selected_name, selected_fields = min(
        usable,
        key=lambda item: (
            -int(bool(item[1]["类型列存在"])),
            -len(item[1][evidence_field]),
            item[0],
        ),
    )
    return {
        **selected_fields,
        "使用报告": selected_name,
        "风险评估报告文件数": len(report_paths),
        "解析失败报告数": sum(item["状态"] == "解析失败" for item in checks),
        "报告检查结果": checks,
    }


def extract_fx_13_documents(primary_path: Path) -> dict[str, Any]:
    return _extract_measure_type_documents(
        primary_path,
        text_extractor=extract_fx_13_text,
        evidence_field="保护性安全措施证据",
    )


@register_extractor("FX-13", document_name_contains=("风险评估报告.md",))
def extract_fx_13(path: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(path, Path):
        raise FX13ExtractionError("FX-13 需要一份标准风险评估报告作为主输入")
    return extract_fx_13_documents(path)
