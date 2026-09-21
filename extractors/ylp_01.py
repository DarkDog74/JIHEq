from __future__ import annotations

from pathlib import Path
from typing import Any

from .common import (
    normalize_heading_title,
    parse_markdown_heading,
    slice_section_by_heading,
)
from .dj_04 import _is_separator_row, _normalize_label, _split_markdown_row
from .errors import ExtractorNotApplicableError, TargetSectionNotFoundError
from .registry import register_extractor


class YLP01ExtractionError(ExtractorNotApplicableError):
    """YLP-01 的基础安全演练计划表无法可靠提取。"""


class YLP01SectionNotFoundError(YLP01ExtractionError, TargetSectionNotFoundError):
    """演练计划 Markdown 中不存在“基础安全演练计划”工作表。"""


_TARGET_SECTION = "基础安全演练计划"
_EVENT_GROUPS = ("恶意程序类", "网络攻击类", "安全隐患类")
_CHECK_MARK = "√"


def _target_section(markdown: str) -> tuple[list[str], int, int]:
    lines = markdown.splitlines()
    matches: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        heading = parse_markdown_heading(line)
        if heading is None:
            continue
        level, title = heading
        if normalize_heading_title(title) == _TARGET_SECTION:
            matches.append((index, level))

    if not matches:
        raise YLP01SectionNotFoundError(
            "演练计划中不存在“基础安全演练计划”工作表"
        )
    if len(matches) > 1:
        locations = "、".join(str(index + 1) for index, _level in matches)
        raise YLP01ExtractionError(
            f"演练计划中发现多个“基础安全演练计划”标题，Markdown行号：{locations}"
        )

    start_index, level = matches[0]
    return slice_section_by_heading(lines, start_index, level), start_index + 2, start_index + 1


def _header_indexes(cells: list[str]) -> tuple[int, int, int | None] | None:
    labels = [_normalize_label(cell) for cell in cells]
    required = {"参演系统", "计划演练时间", *_EVENT_GROUPS}
    if not required.issubset(labels):
        return None

    system_index = labels.index("参演系统")
    plan_time_index = labels.index("计划演练时间")
    owner_index = labels.index("归属单位") if "归属单位" in labels else None
    if system_index >= plan_time_index - 1:
        raise YLP01ExtractionError(
            "基础安全演练计划表中“参演系统”和“计划演练时间”之间没有事件列"
        )
    group_indexes = [labels.index(group) for group in _EVENT_GROUPS]
    if not (
        system_index < group_indexes[0] < group_indexes[1] < group_indexes[2] < plan_time_index
    ):
        raise YLP01ExtractionError("基础安全演练计划表的三类事件列顺序异常")
    return system_index, plan_time_index, owner_index


def _event_labels(
    header: list[str], subheader: list[str] | None, start: int, end: int
) -> dict[int, str]:
    labels: dict[int, str] = {}
    current_group = ""
    for index in range(start, end):
        group = header[index].strip() if index < len(header) else ""
        if _normalize_label(group) in _EVENT_GROUPS:
            current_group = group
        event = subheader[index].strip() if subheader is not None and index < len(subheader) else ""
        if event:
            labels[index] = f"{current_group}/{event}" if current_group else event
        elif current_group:
            labels[index] = current_group
        else:
            labels[index] = f"第{index + 1}列"
    return labels


def _contains_checkmark(value: str) -> bool:
    return _CHECK_MARK in value


def extract_ylp_01_text(markdown: str) -> dict[str, Any]:
    section, first_line_number, heading_line_number = _target_section(markdown)

    parsed_rows: list[tuple[int, list[str]]] = []
    for offset, line in enumerate(section):
        cells = _split_markdown_row(line)
        if cells is None or _is_separator_row(cells):
            continue
        parsed_rows.append((first_line_number + offset, cells))

    header_candidates: list[tuple[int, int, list[str], tuple[int, int, int | None]]] = []
    for row_index, (markdown_line, cells) in enumerate(parsed_rows):
        indexes = _header_indexes(cells)
        if indexes is not None:
            header_candidates.append((row_index, markdown_line, cells, indexes))

    if not header_candidates:
        raise YLP01ExtractionError(
            "“基础安全演练计划”工作表中未找到包含三类事件、参演系统和计划演练时间的表头"
        )
    if len(header_candidates) > 1:
        locations = "、".join(str(item[1]) for item in header_candidates)
        raise YLP01ExtractionError(
            f"“基础安全演练计划”工作表中发现多个目标表头，Markdown行号：{locations}"
        )

    header_row_index, header_line, header, indexes = header_candidates[0]
    system_index, plan_time_index, owner_index = indexes
    event_start = system_index + 1
    event_end = plan_time_index

    subheader: list[str] | None = None
    for _line_number, candidate in parsed_rows[header_row_index + 1 :]:
        system_value = candidate[system_index].strip() if system_index < len(candidate) else ""
        if system_value:
            break
        if any(
            candidate[index].strip()
            for index in range(event_start, min(event_end, len(candidate)))
        ):
            subheader = candidate
            break

    column_labels = _event_labels(header, subheader, event_start, event_end)
    records: list[dict[str, Any]] = []
    for markdown_line, cells in parsed_rows[header_row_index + 1 :]:
        system = cells[system_index].strip() if system_index < len(cells) else ""
        if not system or _normalize_label(system) == "参演系统":
            continue

        selected_events: list[str] = []
        selected_cells: list[dict[str, Any]] = []
        for index in range(event_start, event_end):
            value = cells[index].strip() if index < len(cells) else ""
            if not _contains_checkmark(value):
                continue
            selected_events.append(column_labels[index])
            selected_cells.append(
                {
                    "列号": index + 1,
                    "事件": column_labels[index],
                    "单元格值": value,
                }
            )

        owner = (
            cells[owner_index].strip()
            if owner_index is not None and owner_index < len(cells)
            else ""
        )
        records.append(
            {
                "计划行号": len(records) + 1,
                "Markdown行号": markdown_line,
                "归属单位": owner,
                "参演系统": system,
                "勾选数量": len(selected_cells),
                "已勾选事件": selected_events,
                "勾选单元格": selected_cells,
            }
        )

    if not records:
        raise YLP01ExtractionError(
            "“基础安全演练计划”目标表中没有“参演系统”非空的有效计划行"
        )

    return {
        "目标工作表": _TARGET_SECTION,
        "目标工作表标题Markdown行号": heading_line_number,
        "目标表头Markdown行号": header_line,
        "事件列范围": f"第{event_start + 1}列至第{event_end}列",
        "事件类别": list(_EVENT_GROUPS),
        "演练计划记录": records,
    }


@register_extractor("YLP-01", document_name_contains=("演练计划.md",))
def extract_ylp_01(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入演练计划 Markdown 不存在：{path}")
    return extract_ylp_01_text(path.read_text(encoding="utf-8-sig"))
