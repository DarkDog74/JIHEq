from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from .dj_03 import _normalize_label, _split_markdown_row
from .dj_04 import _is_separator_row
from .errors import ExtractorNotApplicableError, TargetSectionNotFoundError
from .fx_09 import (
    FX09IncompleteMarkdownError,
    FX09SectionNotFoundError,
    _is_target_heading,
    _target_section,
)
from .registry import register_extractor


class FX10ExtractionError(ExtractorNotApplicableError):
    """FX-10 的威胁赋值记录无法可靠提取。"""


class FX10SectionNotFoundError(FX10ExtractionError, TargetSectionNotFoundError):
    """风险评估报告中不存在“附录 B 威胁识别及赋值表”。"""


class FX10TableHeaderNotFoundError(FX10ExtractionError):
    """保留的兼容异常：当前规则不再要求恢复具体赋值表头。"""


class FX10UnstructuredTableError(FX10ExtractionError):
    """保留的兼容异常：当前规则允许检查被压平的普通文本。"""


_NUMBER_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")
_PAGE_MARK_RE = re.compile(r"第\s*\d+\s*页.*共\s*\d+\s*页")
_IMAGE_RE = re.compile(r"^(?:\[\[IMAGE\b|!\[)", re.IGNORECASE)
_OTHER_APPENDIX_RE = re.compile(
    r"附录[AC-ZＡＣ-Ｚ0-9一二三四五六七八九十]"
)


def _numbers(value: str) -> list[str]:
    return _NUMBER_RE.findall(unicodedata.normalize("NFKC", value))


def _is_next_appendix(line: str) -> bool:
    row = _split_markdown_row(line)
    values = row if row is not None else [line]
    for value in values:
        label = _normalize_label(value)
        if _OTHER_APPENDIX_RE.search(label) and not _is_target_heading(label):
            return True
    return False


def _serial_index(cells: list[str]) -> int | None:
    labels = [_normalize_label(cell) for cell in cells]
    return next(
        (index for index, label in enumerate(labels) if label == "序号"), None
    )


def _is_header_text(value: str) -> bool:
    label = _normalize_label(value)
    return (
        "序号" in label
        and "威胁" in label
        and ("赋值" in label or "威胁结果" in label)
    )


def _record(
    *,
    record_number: int,
    markdown_line: int,
    source_type: str,
    original: str,
    numeric_source: str,
) -> dict[str, Any]:
    values = _numbers(numeric_source)
    compact = re.sub(r"\s+", " ", original).strip()
    return {
        "赋值行号": record_number,
        "Markdown行号": markdown_line,
        "记录来源": source_type,
        "原文摘要": compact[:300] + ("…" if len(compact) > 300 else ""),
        "数字": values,
        "是否有数字": bool(values),
    }


def extract_fx_10_text(markdown: str) -> dict[str, Any]:
    try:
        section_lines, line_base, heading_line, title = _target_section(markdown)
    except FX09SectionNotFoundError as error:
        raise FX10SectionNotFoundError(str(error)) from error
    except FX09IncompleteMarkdownError as error:
        raise FX10ExtractionError(str(error)) from error

    records: list[dict[str, Any]] = []
    serial_index: int | None = None

    for relative_index, line in enumerate(section_lines):
        if _is_next_appendix(line):
            break
        stripped = line.strip()
        if not stripped or _PAGE_MARK_RE.search(stripped) or _IMAGE_RE.match(stripped):
            continue
        if _is_target_heading(stripped):
            continue

        cells = _split_markdown_row(line)
        if cells is not None:
            if _is_separator_row(cells):
                continue
            combined = " ".join(cell for cell in cells if cell).strip()
            if not combined:
                continue
            if _is_header_text(combined) and not _numbers(combined):
                serial_index = _serial_index(cells)
                continue

            nonempty_cells = [cell for cell in cells if cell.strip()]
            if len(nonempty_cells) < 2 and not _numbers(combined):
                continue
            numeric_source = " ".join(
                cell for index, cell in enumerate(cells) if index != serial_index
            )
            records.append(
                _record(
                    record_number=len(records) + 1,
                    markdown_line=line_base + relative_index + 1,
                    source_type="Markdown表格",
                    original=combined,
                    numeric_source=numeric_source,
                )
            )
            continue

        if _is_header_text(stripped) and not _numbers(stripped):
            continue
        # 普通文本中只能保留物理行边界；去掉行首序号后再寻找赋值数字。
        numeric_source = re.sub(r"^\s*\d+[.．、]?\s+", "", stripped)
        records.append(
            _record(
                record_number=len(records) + 1,
                markdown_line=line_base + relative_index + 1,
                source_type="压平文本",
                original=stripped,
                numeric_source=numeric_source,
            )
        )

    return {
        "目标表标题": title,
        "目标表标题行": heading_line,
        "检查规则": "附录B每条记录行至少出现一个赋值数字",
        "赋值记录": records,
    }


@register_extractor("FX-10", document_name_contains=("风险评估报告.md",))
def extract_fx_10(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入风险评估报告 Markdown 不存在：{path}")
    return extract_fx_10_text(path.read_text(encoding="utf-8-sig"))
