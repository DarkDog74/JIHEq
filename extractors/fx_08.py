from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from .dj_02 import _parse_heading
from .dj_03 import _normalize_label, _split_markdown_row
from .dj_04 import _is_separator_row
from .errors import ExtractorNotApplicableError, TargetSectionNotFoundError
from .registry import register_extractor


class FX08ExtractionError(ValueError):
    """FX-08 的系统组件/单元资产赋值表无法可靠提取。"""


class FX08AssetIdentificationNotFoundError(
    FX08ExtractionError, TargetSectionNotFoundError
):
    """风险评估报告中不存在支持的系统组件/单元资产赋值表标题。"""


class FX08IncompleteMarkdownError(FX08ExtractionError, ExtractorNotApplicableError):
    """FX-08 输入 Markdown 是显式解析失败存根。"""


_PRIMARY_TARGET_TITLE = "系统组件和单元资产赋值表"
_FALLBACK_TARGET_TITLE = "系统单元及系统组件赋值表"
_TARGET_TITLES = (_PRIMARY_TARGET_TITLE, _FALLBACK_TARGET_TITLE)
_TARGET_TITLE_DESCRIPTION = "或".join(f"“{title}”" for title in _TARGET_TITLES)
_REQUIRED_COLUMNS = ("保密性", "完整性", "可用性")
_PARSE_ERROR_RE = re.compile(r"<!--\s*PARSE_ERROR\b", re.IGNORECASE)
_TOC_LEADER_RE = re.compile(r"(?:\.{4,}|…{2,}|·{4,})")
_NUMBER_RE = re.compile(r"[+-]?\d+(?:\.\d+)?")
_SERIAL_RE = re.compile(r"(?:[A-Za-zＡ-Ｚａ-ｚ]?\d+(?:\.\d+)*)")
_PAGE_MARK_RE = re.compile(r"第\s*\d+\s*页.*共\s*\d+\s*页")
_EMBEDDED_OBJECT_RE = re.compile(
    r"(?:application/vnd\.ms-ole-object|Embedded object:\s*Excel)", re.IGNORECASE
)


def _is_target_title(value: str, target_title: str) -> bool:
    label = _normalize_label(value)
    if label == target_title:
        return True
    return re.fullmatch(
        rf"(?:附录A)?A?[.．]?\d+(?:[.．]\d+)*[.．、:：]?{re.escape(target_title)}",
        label,
        flags=re.IGNORECASE,
    ) is not None


def _line_has_target_title(line: str, target_title: str) -> bool:
    if _TOC_LEADER_RE.search(line) or line.lstrip().startswith("["):
        return False
    row = _split_markdown_row(line)
    if row is not None:
        return any(target_title in _normalize_label(cell) for cell in row)
    return _is_target_title(line, target_title)


def _line_has_next_table_title(line: str) -> bool:
    row = _split_markdown_row(line)
    values = row if row is not None else [line]
    for value in values:
        label = _normalize_label(value)
        if re.search(r"A[.．](?:[4-9]|\d{2,})", label, flags=re.IGNORECASE):
            if "赋值表" in label:
                return True
        if re.search(r"附录[B-ZＢ-Ｚ]", label, flags=re.IGNORECASE):
            return True
    return False


def _target_section_for_title(
    lines: list[str], target_title: str
) -> tuple[list[str], int, int, str] | None:
    heading_matches: list[tuple[int, int, str]] = []
    for index, line in enumerate(lines):
        heading = _parse_heading(line)
        if heading is not None and _is_target_title(heading[1], target_title):
            heading_matches.append((index, heading[0], heading[1]))

    if len(heading_matches) > 1:
        locations = "、".join(str(index + 1) for index, _, _ in heading_matches)
        raise FX08ExtractionError(
            f"风险评估报告中发现多个“{target_title}”标题，行号：{locations}"
        )
    if heading_matches:
        start, level, title = heading_matches[0]
        end = len(lines)
        for index in range(start + 1, len(lines)):
            heading = _parse_heading(lines[index])
            if heading is not None and heading[0] <= level:
                end = index
                break
        heading_line = start + 1
        return lines[start + 1 : end], heading_line, heading_line, title

    plain_matches = [
        index
        for index, line in enumerate(lines)
        if _line_has_target_title(line, target_title)
    ]
    if not plain_matches:
        return None
    if len(plain_matches) > 1:
        locations = "、".join(str(index + 1) for index in plain_matches)
        raise FX08ExtractionError(
            f"风险评估报告中发现多个压平标题“{target_title}”，行号：{locations}"
        )

    start = plain_matches[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _line_has_next_table_title(lines[index]):
            # 旧 PDF 转换结果可能把目标表最后一条记录和 A.4 表题压在同一行。
            end = index + 1
            break
    heading_line = start + 1
    return lines[start:end], start, heading_line, target_title


def _target_section(markdown: str) -> tuple[list[str], int, int, str]:
    lines = markdown.splitlines()
    for target_title in _TARGET_TITLES:
        section = _target_section_for_title(lines, target_title)
        if section is not None:
            return section
    raise FX08AssetIdentificationNotFoundError(
        f"风险评估报告中未找到标题{_TARGET_TITLE_DESCRIPTION}"
    )


def _column_positions(cells: list[str]) -> dict[str, int | None]:
    labels = [_normalize_label(cell) for cell in cells]
    return {
        column: next(
            (index for index, label in enumerate(labels) if column in label), None
        )
        for column in _REQUIRED_COLUMNS
    }


def _find_header(
    section_lines: list[str], line_base: int
) -> tuple[int | None, dict[str, int | None], list[str]]:
    candidates: list[tuple[int, int, dict[str, int | None], list[str]]] = []
    for relative_index, line in enumerate(section_lines):
        cells = _split_markdown_row(line)
        if cells is None or _is_separator_row(cells):
            continue
        positions = _column_positions(cells)
        score = sum(position is not None for position in positions.values())
        if score:
            candidates.append((score, -relative_index, positions, cells))

    if not candidates:
        return None, {column: None for column in _REQUIRED_COLUMNS}, []
    _, negative_index, positions, cells = max(candidates, key=lambda item: item[:2])
    relative_index = -negative_index
    return line_base + relative_index + 1, positions, cells


def _is_number(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).strip()
    return _NUMBER_RE.fullmatch(normalized) is not None


def _record_values(
    cells: list[str], positions: dict[str, int | None]
) -> dict[str, str]:
    exact = {
        column: cells[position].strip()
        if position is not None and position < len(cells)
        else ""
        for column, position in positions.items()
    }
    if all(_is_number(value) for value in exact.values()):
        return exact

    nonempty = [cell.strip() for cell in cells if cell.strip()]
    # 目标表最后四个有效单元格固定为 C/I/A/综合赋值。按尾部取值可兼容
    # PDF 跨页后合并单元格展开、空槽数量变化导致的物理列偏移。
    if len(nonempty) >= 4:
        tail_values = nonempty[-4:-1]
        if all(_is_number(value) for value in tail_values):
            return dict(zip(_REQUIRED_COLUMNS, tail_values, strict=True))
    return exact


def _record_serial(cells: list[str]) -> str | None:
    first = next((cell.strip() for cell in cells if cell.strip()), "")
    normalized = unicodedata.normalize("NFKC", first)
    return normalized if _SERIAL_RE.fullmatch(normalized) else None


def _detached_value_groups(line: str) -> list[list[str]]:
    normalized = unicodedata.normalize("NFKC", line).strip()
    if not normalized:
        return []
    values = normalized.split()
    if len(values) % 4 or not all(_is_number(value) for value in values):
        return []
    return [values[index : index + 4] for index in range(0, len(values), 4)]


def extract_fx_08_text(markdown: str) -> dict[str, Any]:
    if _PARSE_ERROR_RE.search(markdown):
        raise FX08IncompleteMarkdownError(
            f"风险评估报告为显式解析失败存根，无法检查{_TARGET_TITLE_DESCRIPTION}"
        )

    section_lines, line_base, heading_line, title = _target_section(markdown)
    header_line, positions, header_cells = _find_header(section_lines, line_base)
    records: list[dict[str, Any]] = []
    detached_values: list[tuple[int, list[str]]] = []

    if header_line is None and any(
        _EMBEDDED_OBJECT_RE.search(line) for line in section_lines
    ):
        raise FX08IncompleteMarkdownError(
            f"“{title}”是未展开的内嵌 Excel 对象，无法检查表头和逐行赋值"
        )

    if header_line is not None:
        header_relative_index = header_line - line_base - 1
        for relative_index, line in enumerate(section_lines):
            if relative_index <= header_relative_index:
                continue
            boundary = _line_has_next_table_title(line)
            cells = _split_markdown_row(line)
            if cells is None:
                for values in _detached_value_groups(line):
                    detached_values.append(
                        (line_base + relative_index + 1, values)
                    )
                if boundary:
                    break
                continue
            if _is_separator_row(cells) or _PAGE_MARK_RE.search(line):
                if boundary:
                    break
                continue
            if any(
                column in _normalize_label(" ".join(cells))
                for column in _REQUIRED_COLUMNS
            ):
                if boundary:
                    break
                continue

            serial = _record_serial(cells)
            if serial is not None:
                values = _record_values(cells, positions)
                missing = [
                    column for column, value in values.items() if not _is_number(value)
                ]
                records.append(
                    {
                        "赋值行号": len(records) + 1,
                        "Markdown行号": line_base + relative_index + 1,
                        "记录编号": serial,
                        **values,
                        "缺少赋值": missing,
                        "是否完整赋值": not missing,
                    }
                )
            if boundary:
                break

    pending_records = [
        record
        for record in records
        if all(not record[column] for column in _REQUIRED_COLUMNS)
    ]
    # PDF 跨页时可能把表格右侧 C/I/A/Vc 四列整体拆成后续纯数字行。
    # 只有记录数和数字四元组数完全一致时才顺序回填，数量不一致不猜测。
    if detached_values and len(detached_values) == len(pending_records):
        for record, (value_line, values) in zip(
            pending_records, detached_values, strict=True
        ):
            for column, value in zip(_REQUIRED_COLUMNS, values[:3], strict=True):
                record[column] = value
            record["缺少赋值"] = []
            record["是否完整赋值"] = True
            record["赋值来源"] = "PDF分离数字行"
            record["赋值Markdown行号"] = value_line

    return {
        "目标表标题": title,
        "目标表标题行": heading_line,
        "表头行": header_line,
        "识别到的表头列": header_cells,
        "列检查": [
            {
                "列名": column,
                "存在": positions[column] is not None,
                "Markdown行号": [header_line]
                if header_line is not None and positions[column] is not None
                else [],
            }
            for column in _REQUIRED_COLUMNS
        ],
        "赋值记录": records,
        "分离赋值四元组数": len(detached_values),
    }


@register_extractor("FX-08", document_name_contains=("风险评估报告.md",))
def extract_fx_08(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入风险评估报告 Markdown 不存在：{path}")
    return extract_fx_08_text(path.read_text(encoding="utf-8-sig"))
