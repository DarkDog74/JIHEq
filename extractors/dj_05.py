from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from .dj_04 import (
    DJ04ExtractionError,
    DJ04NotApplicableError,
    _normalize_label,
    _REVISION_TITLES,
    _split_markdown_row,
    extract_dj_04_text,
)
from .errors import RevisionHistoryNotFoundError
from .registry import register_extractor


class DJ05ExtractionError(ValueError):
    """DJ-05 所需字段无法可靠提取。"""


class DJ05NotApplicableError(DJ05ExtractionError, RevisionHistoryNotFoundError):
    """输入文档不包含 DJ-05 所需的修订历史。"""


_COVER_VERSION_RE = re.compile(
    r"(?<![A-Za-z0-9])V(?:ER)?\s*\.?\s*[:：]?\s*"
    r"(?P<value>\d+(?:\s*\.\s*\d+)+)",
    re.IGNORECASE,
)
_COVER_MONTH_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月"
)
_FULL_WIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")


def _plain_cover_text(cover: str) -> str:
    """Remove Markdown emphasis that may split one visible cover value."""
    return re.sub(r"(?:\*\*|__)", "", cover).replace("\\", "")


def _revision_title_index(lines: list[str]) -> int:
    candidates: list[int] = []
    for index, line in enumerate(lines):
        cells = _split_markdown_row(line)
        if cells is not None and any(
            _normalize_label(cell) in _REVISION_TITLES for cell in cells
        ):
            candidates.append(index)
    if not candidates:
        raise DJ05NotApplicableError("未找到完整的修订历史表标题")
    if len(candidates) > 1:
        locations = "、".join(str(index + 1) for index in candidates)
        raise DJ05ExtractionError(f"发现多个修订历史表标题，Markdown 行号：{locations}")
    return candidates[0]


def _single_or_none(values: list[str], field_name: str) -> str | None:
    unique = list(dict.fromkeys(values))
    if not unique:
        return None
    if len(unique) > 1:
        raise DJ05ExtractionError(
            f"封面出现多个不同的{field_name}：{'、'.join(unique)}"
        )
    return unique[0]


def _extract_cover_version(cover: str) -> str | None:
    cover = _plain_cover_text(cover)
    values = [
        re.sub(r"\s+", "", match.group("value")).translate(_FULL_WIDTH_DIGITS)
        for match in _COVER_VERSION_RE.finditer(cover.translate(_FULL_WIDTH_DIGITS))
    ]
    return _single_or_none(values, "版本号")


def _extract_cover_month(cover: str) -> str | None:
    cover = _plain_cover_text(cover)
    values: list[str] = []
    for match in _COVER_MONTH_RE.finditer(cover.translate(_FULL_WIDTH_DIGITS)):
        year = int(match.group("year"))
        month = int(match.group("month"))
        if not 1 <= month <= 12:
            raise DJ05ExtractionError(f"封面月份无效：{year}年{month}月")
        values.append(f"{year:04d}-{month:02d}")
    return _single_or_none(values, "日期")


def _normalize_version(value: object) -> str:
    text = str(value or "").translate(_FULL_WIDTH_DIGITS).strip()
    text = re.sub(r"^V(?:ER)?\s*\.?\s*[:：]?\s*", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s+", "", text)


def _parse_revision_date(value: object, row_number: object) -> tuple[tuple[int, int, int], str]:
    text = str(value or "").translate(_FULL_WIDTH_DIGITS).strip()
    if not text:
        raise DJ05ExtractionError(f"修订历史第{row_number}行缺少日期，无法确定最新记录")

    normalized = text.replace("年", "-").replace("月", "-").replace("日", "")
    normalized = re.sub(r"[./]", "-", normalized)
    normalized = re.sub(r"\s+", "", normalized)
    normalized = re.sub(r"-+", "-", normalized).strip("-")
    parts = normalized.split("-")
    if len(parts) not in (2, 3) or not all(part.isdigit() for part in parts):
        raise DJ05ExtractionError(
            f"修订历史第{row_number}行日期无法识别：{text}"
        )

    year, month = int(parts[0]), int(parts[1])
    day = int(parts[2]) if len(parts) == 3 else 1
    try:
        date(year, month, day)
    except ValueError as error:
        raise DJ05ExtractionError(
            f"修订历史第{row_number}行日期无效：{text}"
        ) from error

    display = f"{year:04d}-{month:02d}"
    if len(parts) == 3:
        display += f"-{day:02d}"
    return (year, month, day), display


def _is_repeated_header(row: dict[str, object]) -> bool:
    version = _normalize_label(str(row.get("版本号", "")))
    revision_date = _normalize_label(str(row.get("日期", "")))
    author = _normalize_label(str(row.get("作者", "")))
    return (
        version == "版本号"
        and revision_date == "日期"
        and author in {"作者", "修改人"}
    )


def _latest_revision(markdown: str) -> tuple[str | None, str | None]:
    try:
        extracted_rows = extract_dj_04_text(markdown)["修订历史"]
    except DJ04NotApplicableError as error:
        raise DJ05NotApplicableError(str(error)) from error
    except DJ04ExtractionError as error:
        raise DJ05ExtractionError(str(error)) from error

    candidates: list[tuple[tuple[int, int, int], str, str, object]] = []
    for row in extracted_rows:
        if _is_repeated_header(row):
            continue
        row_number = row.get("行号", "?")
        version = _normalize_version(row.get("版本号"))
        if not version:
            raise DJ05ExtractionError(
                f"修订历史第{row_number}行缺少版本号，无法确定最新记录"
            )
        date_key, normalized_date = _parse_revision_date(row.get("日期"), row_number)
        candidates.append((date_key, version, normalized_date, row_number))

    if not candidates:
        return None, None

    latest_date_key = max(candidate[0] for candidate in candidates)
    latest = [candidate for candidate in candidates if candidate[0] == latest_date_key]
    latest_versions = {candidate[1] for candidate in latest}
    if len(latest_versions) > 1:
        versions = "、".join(sorted(latest_versions))
        raise DJ05ExtractionError(
            f"最新修订日期存在多个不同版本号：{versions}"
        )
    return latest[0][1], latest[0][2]


def extract_dj_05_text(markdown: str) -> dict[str, str | None]:
    lines = markdown.splitlines()
    title_index = _revision_title_index(lines)
    cover = "\n".join(lines[:title_index])
    latest_version, latest_date = _latest_revision(markdown)
    return {
        "封面版本": _extract_cover_version(cover),
        "封面日期": _extract_cover_month(cover),
        "最新修订版本": latest_version,
        "最新修订日期": latest_date,
    }


@register_extractor("DJ-05", document_name_contains=("定级报告",))
def extract_dj_05(path: Path) -> dict[str, str | None]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入 Markdown 不存在：{path}")
    return extract_dj_05_text(path.read_text(encoding="utf-8-sig"))
