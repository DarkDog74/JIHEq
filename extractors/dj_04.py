from __future__ import annotations

import html
import re
from pathlib import Path

from .errors import RevisionHistoryNotFoundError
from .registry import register_extractor


class DJ04ExtractionError(ValueError):
    """DJ-04 修订历史无法可靠提取。"""


class DJ04NotApplicableError(DJ04ExtractionError, RevisionHistoryNotFoundError):
    """输入文档不包含 DJ-04 所需的修订历史表。"""


_REVISION_TITLES = frozenset({"修订历史", "修订历史(REVISIONHISTORY)"})
_REQUIRED_COLUMNS = ("版本号", "日期", "作者")
_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _clean_cell(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = _HTML_TAG_RE.sub("", value)
    value = value.replace("**", "").replace("__", "").replace("`", "")
    value = value.replace(r"\|", "|")
    return re.sub(r"\s+", " ", value).strip()


def _normalize_label(value: str) -> str:
    value = _clean_cell(value).replace("（", "(").replace("）", ")")
    return re.sub(r"\s+", "", value).upper()


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
            cells.append(_clean_cell("".join(current)))
            current = []
        else:
            current.append(character)
    if escaped:
        current.append("\\")
    if current:
        cells.append(_clean_cell("".join(current)))
    return cells


def _is_separator_row(cells: list[str]) -> bool:
    nonempty = [cell for cell in cells if cell]
    return bool(nonempty) and all(_SEPARATOR_CELL_RE.fullmatch(cell) for cell in nonempty)


def _column_indexes(header: list[str]) -> dict[str, int] | None:
    indexes: dict[str, int] = {}
    for index, cell in enumerate(header):
        label = _normalize_label(cell)
        if label in _REQUIRED_COLUMNS:
            if label in indexes:
                raise DJ04ExtractionError(f"修订历史表头重复出现“{label}”列")
            indexes[label] = index
    if set(indexes) != set(_REQUIRED_COLUMNS):
        return None
    if not (indexes["版本号"] < indexes["日期"] < indexes["作者"]):
        raise DJ04ExtractionError("修订历史表的版本号、日期、作者列顺序异常")
    return indexes


def _value_in_zone(cells: list[str], start: int, end: int) -> str:
    values = [
        cells[index]
        for index in range(start, min(end + 1, len(cells)))
        if cells[index]
    ]
    return " ".join(values)


def extract_dj_04_text(markdown: str) -> dict[str, list[dict[str, object]]]:
    lines = markdown.splitlines()
    title_candidates: list[int] = []
    parsed_rows: dict[int, list[str]] = {}

    for line_number, line in enumerate(lines, start=1):
        cells = _split_markdown_row(line)
        if cells is None:
            continue
        parsed_rows[line_number] = cells
        if any(_normalize_label(cell) in _REVISION_TITLES for cell in cells):
            title_candidates.append(line_number)

    if not title_candidates:
        raise DJ04NotApplicableError("未找到完整的“修订历史 (REVISION HISTORY)”表标题")
    if len(title_candidates) > 1:
        locations = "、".join(str(value) for value in title_candidates)
        raise DJ04ExtractionError(f"发现多个修订历史表标题，Markdown 行号：{locations}")

    title_line = title_candidates[0]
    header_line: int | None = None
    indexes: dict[str, int] | None = None
    for line_number in range(title_line + 1, len(lines) + 1):
        cells = parsed_rows.get(line_number)
        if cells is None:
            break
        if _is_separator_row(cells):
            continue
        candidate_indexes = _column_indexes(cells)
        if candidate_indexes is not None:
            header_line = line_number
            indexes = candidate_indexes
            break

    if header_line is None or indexes is None:
        raise DJ04ExtractionError("修订历史标题下未找到版本号、日期、作者完整表头")

    zones = {
        "版本号": (0, indexes["版本号"]),
        "日期": (indexes["版本号"] + 1, indexes["日期"]),
        "作者": (indexes["日期"] + 1, indexes["作者"]),
    }
    revision_rows: list[dict[str, object]] = []
    table_row_number = 0
    for line_number in range(header_line + 1, len(lines) + 1):
        cells = parsed_rows.get(line_number)
        if cells is None:
            break
        if _is_separator_row(cells):
            continue
        table_row_number += 1
        row = {
            field: _value_in_zone(cells, *zones[field])
            for field in _REQUIRED_COLUMNS
        }
        if not any(row.values()):
            continue
        revision_rows.append(
            {
                "行号": table_row_number,
                "Markdown行号": line_number,
                **row,
            }
        )

    return {"修订历史": revision_rows}


@register_extractor("DJ-04", document_name_contains=("定级报告",))
def extract_dj_04(path: Path) -> dict[str, list[dict[str, object]]]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入 Markdown 不存在：{path}")
    return extract_dj_04_text(path.read_text(encoding="utf-8-sig"))
