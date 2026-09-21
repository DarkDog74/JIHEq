"""应急预案与演练报告的受控日期证据提取。"""

from __future__ import annotations

import calendar
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable


_FULL_CHINESE_DATE_RE = re.compile(
    r"(?P<year>20\d{2})\s*年\s*(?P<month>1[0-2]|0?[1-9])\s*月\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])\s*日"
)
_FULL_SEPARATED_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2})\s*(?P<separator>[-/.])\s*"
    r"(?P<month>1[0-2]|0?[1-9])\s*(?P=separator)\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])(?!\d)"
)
_MONTH_DATE_RE = re.compile(
    r"(?P<year>20\d{2})\s*年\s*(?P<month>1[0-2]|0?[1-9])\s*月(?!\s*\d\s*日)"
)
_TABLE_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")
_REVISION_HEADERS = ("修订记录", "修订日期", "修改记录", "更新日期", "版本日期")
_REVISION_TABLE_TITLES = ("预案管理名录", "预案修订记录", "修订记录", "修订历史")


@dataclass(frozen=True)
class DateEvidence:
    raw: str
    start_date: date
    end_date: date
    precision: str
    line_start: int
    line_end: int
    context: str
    field: str

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["start_date"] = self.start_date.isoformat()
        payload["end_date"] = self.end_date.isoformat()
        return payload


def _normalize_text(value: str) -> str:
    text = value.replace("**", "").replace("__", "").replace("<br>", " ")
    return re.sub(r"[\s、，,.:：;；()（）\[\]【】]+", "", text)


def _build_evidence(
    match: re.Match[str],
    *,
    precision: str,
    line_number: int,
    context: str,
    field: str,
) -> DateEvidence | None:
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day")) if precision == "day" else 1
    try:
        start = date(year, month, day)
    except ValueError:
        return None
    end = (
        start
        if precision == "day"
        else date(year, month, calendar.monthrange(year, month)[1])
    )
    return DateEvidence(
        raw=match.group(0),
        start_date=start,
        end_date=end,
        precision=precision,
        line_start=line_number,
        line_end=line_number,
        context=context.strip(),
        field=field,
    )


def parse_date_evidence(
    text: str,
    *,
    line_number: int,
    context: str,
    field: str,
) -> list[DateEvidence]:
    """解析日精度和月精度日期，保留可计算的起止范围。"""

    results: list[DateEvidence] = []
    occupied: list[tuple[int, int]] = []
    for pattern in (_FULL_CHINESE_DATE_RE, _FULL_SEPARATED_DATE_RE):
        for match in pattern.finditer(text):
            evidence = _build_evidence(
                match,
                precision="day",
                line_number=line_number,
                context=context,
                field=field,
            )
            if evidence is not None:
                results.append(evidence)
                occupied.append(match.span())
    for match in _MONTH_DATE_RE.finditer(text):
        if any(start <= match.start() < end for start, end in occupied):
            continue
        evidence = _build_evidence(
            match,
            precision="month",
            line_number=line_number,
            context=context,
            field=field,
        )
        if evidence is not None:
            results.append(evidence)
    return sorted(results, key=lambda item: (item.start_date, item.end_date))


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    body = stripped[1:-1] if stripped.endswith("|") else stripped[1:]
    cells = re.split(r"(?<!\\)\|", body)
    return [cell.replace("\\|", "|").strip() for cell in cells]


def _iter_markdown_tables(lines: list[str]) -> Iterable[tuple[int, int, list[list[str]]]]:
    index = 0
    while index < len(lines):
        if not lines[index].lstrip().startswith("|"):
            index += 1
            continue
        start = index
        rows: list[list[str]] = []
        while index < len(lines) and lines[index].lstrip().startswith("|"):
            rows.append(_split_table_row(lines[index]))
            index += 1
        yield start, index - 1, rows


def extract_revision_date_evidence(markdown: str) -> list[DateEvidence]:
    """仅从“预案管理名录/修订记录”表的修订日期列取值。"""

    lines = markdown.splitlines()
    records: list[DateEvidence] = []
    for start, end, rows in _iter_markdown_tables(lines):
        header_index = None
        revision_column = None
        for row_index, cells in enumerate(rows):
            for column_index, cell in enumerate(cells):
                normalized = _normalize_text(cell)
                if any(alias in normalized for alias in _REVISION_HEADERS):
                    header_index = row_index
                    revision_column = column_index
                    break
            if header_index is not None:
                break
        if header_index is None or revision_column is None:
            continue

        scope = "\n".join(lines[max(0, start - 5) : end + 1])
        normalized_scope = _normalize_text(scope)
        if not any(title in normalized_scope for title in _REVISION_TABLE_TITLES):
            continue

        for row_index in range(header_index + 1, len(rows)):
            cells = rows[row_index]
            if revision_column >= len(cells):
                continue
            cell = cells[revision_column].strip()
            if not cell or _TABLE_SEPARATOR_RE.fullmatch(cell.replace(" ", "")):
                continue
            line_number = start + row_index + 1
            records.extend(
                parse_date_evidence(
                    cell,
                    line_number=line_number,
                    context=lines[start + row_index],
                    field="修订记录",
                )
            )
    return records


def _is_time_people_heading(line: str) -> bool:
    normalized = _normalize_text(line.lstrip("#").strip())
    return "时间地点人员" in normalized


def _is_direct_drill_time_line(line: str) -> bool:
    normalized = _normalize_text(line.lstrip("#").strip())
    if "演练时间段" in normalized or "演练时间安排" in normalized:
        return False
    return (
        normalized.startswith("演练时间")
        or normalized.startswith("时间")
        or normalized.startswith("一时间")
    )


def extract_drill_date_evidence(markdown: str) -> DateEvidence | None:
    """仅从“演练时间/时间、地点、人员”小节提取演练日期。"""

    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if not _is_time_people_heading(line):
            continue
        for candidate_index in range(index, min(len(lines), index + 14)):
            candidate = lines[candidate_index]
            if candidate_index > index and candidate.startswith("# "):
                break
            if not (_is_direct_drill_time_line(candidate) or candidate_index == index):
                continue
            evidences = parse_date_evidence(
                candidate,
                line_number=candidate_index + 1,
                context=candidate,
                field="演练时间",
            )
            if evidences:
                return evidences[0]

    # 兼容“演练时间：YYYY年...”作为独立标题/字段的表单型报告。
    for index, line in enumerate(lines):
        if not _is_direct_drill_time_line(line):
            continue
        evidences = parse_date_evidence(
            line,
            line_number=index + 1,
            context=line,
            field="演练时间",
        )
        if evidences:
            return evidences[0]
    return None
