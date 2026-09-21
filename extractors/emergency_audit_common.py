"""应急预案与演练报告稽核项共用的受控 Markdown 提取能力。"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from parser.emergency_evidence import parse_date_evidence

from .errors import ExtractorNotApplicableError, RequiredDocumentNotFoundError


class EmergencyAuditExtractionError(ExtractorNotApplicableError):
    """应急材料存在，但无法按统一结构可靠提取。"""


REPORT_SUBJECT_TITLES = ("演练科目内容", "演练科目及内容", "演练项目")
PHOTO_SECTION_TITLES = ("演练现场照片记录", "演练现场", "现场照片")

_PURPOSE_FILENAMES = {
    "应急预案": "应急预案.md",
    "应急演练报告": "应急演练报告.md",
}
_TOC_LINK_RE = re.compile(r"\]\(#_Toc\d+\)", re.IGNORECASE)
_MARKDOWN_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s*(?P<text>.*)$")
_CUSTOM_IMAGE_RE = re.compile(r"\[\[IMAGE\b[^\]]*\]\]", re.IGNORECASE)
_CUSTOM_RASTER_IMAGE_RE = re.compile(
    r"\[\[IMAGE\b[^\]]*(?:type=[\"']image/|file=[\"'][^\"']+\.(?:png|jpe?g|gif|bmp|webp|tiff?))[^\]]*\]\]",
    re.IGNORECASE,
)
_MARKDOWN_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\((?:<)?[^)>]+\.(?:png|jpe?g|gif|bmp|webp|tiff?)(?:>)?\)",
    re.IGNORECASE,
)
_MARKDOWN_ANY_IMAGE_RE = re.compile(r"!\[[^\]]*\]\((?:<[^>]+>|[^)]+)\)")
_TABLE_SEPARATOR_RE = re.compile(r"^:?-{3,}:?$")
_SCENARIO_RE = re.compile(
    r"模拟场景(?:[一二三四五六七八九十\d]+)?\s*[:：]\s*(?P<subject>.+)"
)
_RESPONSE_LEVEL_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<level>IV|III|II|I|Ⅳ|Ⅲ|Ⅱ|Ⅰ|[一二三四]|[1-4])\s*级",
    re.IGNORECASE,
)
_NUMBER_PREFIX_RE = re.compile(
    r"^(?:"
    r"第?[一二三四五六七八九十百\d]+章[、.．]?|"
    r"[一二三四五六七八九十百\d]+[、.．]|"
    r"[（(][一二三四五六七八九十百\d]+[）)]"
    r")\s*"
)
_TOP_LEVEL_PLAIN_RE = re.compile(r"^[一二三四五六七八九十百]+[、.．]\s*\S+")


@dataclass(frozen=True)
class SourceLine:
    number: int
    raw: str


@dataclass(frozen=True)
class LocatedSection:
    requested_title: str
    actual_title: str
    title_line: int
    heading_level: int | None
    content_lines: tuple[SourceLine, ...]
    boundary_line: SourceLine | None


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise EmergencyAuditExtractionError(
            f"应急材料分析 JSON 格式错误：{error}"
        ) from error
    if not isinstance(payload, Mapping):
        raise EmergencyAuditExtractionError("应急材料分析 JSON 顶层必须是对象")
    if not isinstance(payload.get("files"), list):
        raise EmergencyAuditExtractionError("应急材料分析 JSON 缺少 files 数组")
    return payload


def read_current_emergency_markdown(
    manifest_path: Path, purpose: str
) -> tuple[Path, str, Mapping[str, Any]]:
    """按清单中的标准文件名读取最新预案或最新演练报告。"""

    expected_name = _PURPOSE_FILENAMES.get(purpose)
    if expected_name is None:
        raise ValueError(f"不支持的应急材料用途：{purpose}")
    manifest_path = Path(manifest_path)
    payload = _read_json(manifest_path)
    records = [
        item
        for item in payload["files"]
        if isinstance(item, Mapping) and item.get("用途") == purpose
    ]
    if not records:
        raise RequiredDocumentNotFoundError(
            f"该系统没有{purpose}原件", requirement=purpose
        )

    candidates = [
        item
        for item in records
        if item.get("解析状态") == "success"
        and item.get("标准Markdown") == expected_name
    ]
    if not candidates:
        failures = "；".join(
            f"{item.get('源文件') or '未知文件'}："
            f"{item.get('错误原因') or '未生成当前标准Markdown'}"
            for item in records
        )
        raise EmergencyAuditExtractionError(
            f"{purpose}原件存在但未成功生成{expected_name}：{failures}"
        )
    if len(candidates) > 1:
        names = "、".join(str(item.get("源文件") or "未知文件") for item in candidates)
        raise EmergencyAuditExtractionError(
            f"应急材料清单中存在多份当前{purpose}：{names}"
        )

    path = manifest_path.parent / expected_name
    if not path.is_file():
        raise EmergencyAuditExtractionError(f"标准Markdown不存在：{path}")
    try:
        markdown = path.read_text(encoding="utf-8-sig")
    except UnicodeError as error:
        raise EmergencyAuditExtractionError(f"标准Markdown编码异常：{path}") from error
    return path, markdown, candidates[0]


def _remove_inline_images(value: str) -> str:
    value = _CUSTOM_IMAGE_RE.sub("", value)
    return _MARKDOWN_ANY_IMAGE_RE.sub("", value)


def clean_display_text(value: str) -> str:
    text = _remove_inline_images(value)
    text = re.sub(r"^#{1,6}\s*", "", text.strip())
    text = text.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ")
    text = re.sub(r"</?[^>]+>", " ", text)
    text = text.replace("**", "").replace("__", "").replace("`", "")
    text = text.strip(" \t#*_-")
    return re.sub(r"\s+", " ", text).strip()


def _heading(line: str) -> tuple[int, str] | None:
    match = _MARKDOWN_HEADING_RE.match(line.strip())
    if match is None:
        return None
    return len(match.group("marks")), clean_display_text(match.group("text"))


def _without_number_prefix(value: str) -> str:
    text = clean_display_text(value)
    previous = None
    while text != previous:
        previous = text
        text = _NUMBER_PREFIX_RE.sub("", text).strip()
    return text.lstrip("：: ").rstrip("：: ")


def _is_toc_title(line: str) -> bool:
    return _without_number_prefix(line) == "目录"


def _is_top_level_plain_section(line: str) -> bool:
    text = clean_display_text(line)
    return bool(_TOP_LEVEL_PLAIN_RE.match(text)) and len(text) <= 100


def body_lines(markdown: str) -> list[SourceLine]:
    """保留正文与目录前材料，删除目录区和目录链接。"""

    raw_lines = markdown.splitlines()
    excluded: set[int] = set()
    for start, raw in enumerate(raw_lines):
        if not _is_toc_title(raw):
            continue
        end = len(raw_lines)
        for index in range(start + 1, len(raw_lines)):
            candidate = raw_lines[index]
            if _TOC_LINK_RE.search(candidate):
                continue
            if _heading(candidate) is not None or _is_top_level_plain_section(candidate):
                end = index
                break
        excluded.update(range(start, end))
        break

    return [
        SourceLine(index + 1, raw)
        for index, raw in enumerate(raw_lines)
        if index not in excluded and not _TOC_LINK_RE.search(raw)
    ]


def _line_starts_with_title(line: str, title: str) -> bool:
    heading = _heading(line)
    if heading is not None:
        return _without_number_prefix(heading[1]) == title
    label = _without_number_prefix(line)
    return len(label) <= 120 and label == title


def find_named_section(
    markdown: str, titles: Iterable[str]
) -> LocatedSection | None:
    """按给定顺序逐个尝试标题，命中第一个可用标题后停止。"""

    lines = body_lines(markdown)
    for requested_title in titles:
        matches = [
            index
            for index, line in enumerate(lines)
            if _line_starts_with_title(line.raw, requested_title)
        ]
        if not matches:
            continue
        if len(matches) > 1:
            locations = "、".join(str(lines[index].number) for index in matches)
            raise EmergencyAuditExtractionError(
                f"正文中存在多个“{requested_title}”标题，行号：{locations}"
            )

        start = matches[0]
        start_heading = _heading(lines[start].raw)
        heading_level = start_heading[0] if start_heading is not None else None
        actual_title = (
            start_heading[1]
            if start_heading is not None
            else clean_display_text(lines[start].raw)
        )
        end = len(lines)
        boundary: SourceLine | None = None
        for index in range(start + 1, len(lines)):
            candidate_heading = _heading(lines[index].raw)
            is_boundary = False
            if heading_level is not None:
                is_boundary = (
                    candidate_heading is not None
                    and candidate_heading[0] <= heading_level
                )
            else:
                is_boundary = candidate_heading is not None or _is_top_level_plain_section(
                    lines[index].raw
                )
            if is_boundary:
                end = index
                boundary = lines[index]
                break
        return LocatedSection(
            requested_title=requested_title,
            actual_title=actual_title,
            title_line=lines[start].number,
            heading_level=heading_level,
            content_lines=tuple(lines[start + 1 : end]),
            boundary_line=boundary,
        )
    return None


def section_evidence(section: LocatedSection) -> dict[str, Any]:
    return {
        "匹配标题": section.actual_title,
        "使用标题别名": section.requested_title,
        "标题行号": section.title_line,
    }


def find_body_markdown_heading(markdown: str, keyword: str) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for line in body_lines(markdown):
        heading = _heading(line.raw)
        if heading is None or keyword not in heading[1]:
            continue
        evidence.append(
            {"行号": line.number, "标题层级": heading[0], "标题": heading[1]}
        )
    return evidence


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    body = stripped[1:-1] if stripped.endswith("|") else stripped[1:]
    return [cell.replace("\\|", "|").strip() for cell in re.split(r"(?<!\\)\|", body)]


def _iter_tables(lines: list[str]) -> Iterable[tuple[int, int, list[list[str]]]]:
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


def _normalized_header(value: str) -> str:
    return re.sub(r"[\s*_:：、，,。；;（）()]+", "", value)


def _topic_parts(value: str) -> list[str]:
    text = clean_display_text(value)
    text = re.sub(r"^[-+*]\s*", "", text)
    text = re.sub(r"^\d+[、.．]\s*", "", text)
    text = re.split(r"[（(]\s*演练时间\s*[:：]", text, maxsplit=1)[0]
    parts = [part.strip(" ：:。.") for part in re.split(r"[、，,；;/]", text)]
    return [part for part in parts if part and not part.startswith("附上")]


def normalize_topic(value: str) -> str:
    """只处理格式差异，不删除“攻击/事件/演练”等业务词。"""

    text = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[\s、，,。.:：；;/'\"“”‘’（）()\[\]【】_-]+", "", text)


def normalize_topics(values: Iterable[str]) -> list[str]:
    normalized = {normalize_topic(value) for value in values if normalize_topic(value)}
    return sorted(normalized)


def extract_latest_plan_drill_subjects(markdown: str) -> dict[str, Any] | None:
    """从预案管理名录的演练记录列选日期最新的一行。"""

    lines = markdown.splitlines()
    records: list[dict[str, Any]] = []
    for start, end, rows in _iter_tables(lines):
        scope = "\n".join(lines[max(0, start - 5) : end + 1])
        if "预案管理名录" not in _normalized_header(scope):
            continue
        header_index: int | None = None
        column_index: int | None = None
        for row_index, row in enumerate(rows):
            for cell_index, cell in enumerate(row):
                if _normalized_header(cell) == "演练记录":
                    header_index = row_index
                    column_index = cell_index
                    break
            if header_index is not None:
                break
        if header_index is None or column_index is None:
            continue

        for row_index in range(header_index + 1, len(rows)):
            row = rows[row_index]
            if column_index >= len(row):
                continue
            cell = row[column_index].strip()
            if not cell or _TABLE_SEPARATOR_RE.fullmatch(cell.replace(" ", "")):
                continue
            match = re.search(r"开展演练\s*[:：]\s*(?P<subject>.+)", cell, re.DOTALL)
            if match is None:
                continue
            subject_text = re.split(r"<br\s*/?>", match.group("subject"), maxsplit=1)[0]
            topics = _topic_parts(subject_text)
            if not topics:
                continue
            line_number = start + row_index + 1
            dates = parse_date_evidence(
                cell,
                line_number=line_number,
                context=lines[start + row_index],
                field="演练记录",
            )
            latest_date = max((item.start_date for item in dates), default=None)
            records.append(
                {
                    "演练记录原文": cell,
                    "演练科目": topics,
                    "演练科目比较值": normalize_topics(topics),
                    "行号": line_number,
                    "记录日期": latest_date.isoformat() if latest_date else None,
                    "_date": latest_date,
                }
            )

    if not records:
        return None
    dated = [record for record in records if isinstance(record["_date"], date)]
    if dated:
        selected = max(dated, key=lambda item: (item["_date"], item["行号"]))
    elif len(records) == 1:
        selected = records[0]
    else:
        raise EmergencyAuditExtractionError(
            "预案管理名录存在多条演练记录，但记录中没有可用于确定最新项的日期"
        )
    return {key: value for key, value in selected.items() if key != "_date"}


def extract_report_drill_subjects(markdown: str) -> dict[str, Any] | None:
    section = find_named_section(markdown, REPORT_SUBJECT_TITLES)
    if section is None:
        return None

    scenario_topics: list[str] = []
    scenario_lines: list[int] = []
    for line in section.content_lines:
        plain = clean_display_text(line.raw)
        match = _SCENARIO_RE.search(plain)
        if match is None:
            continue
        scenario_topics.extend(_topic_parts(match.group("subject")))
        scenario_lines.append(line.number)

    topics = scenario_topics
    evidence_lines = scenario_lines
    raw_subject = "、".join(scenario_topics)
    if not topics:
        for line in section.content_lines:
            if (
                not clean_display_text(line.raw)
                or line.raw.lstrip().startswith("|")
                or _CUSTOM_IMAGE_RE.search(line.raw)
                or _MARKDOWN_ANY_IMAGE_RE.search(line.raw)
            ):
                continue
            candidate = clean_display_text(line.raw)
            topics = _topic_parts(candidate)
            if topics:
                raw_subject = candidate
                evidence_lines = [line.number]
                break

    return {
        "演练科目章节": section_evidence(section),
        "演练科目原文": raw_subject,
        "演练科目": topics,
        "演练科目比较值": normalize_topics(topics),
        "科目内容行号": evidence_lines,
    }


def extract_response_levels(markdown: str) -> dict[str, Any]:
    mapping = {
        "1": 1,
        "一": 1,
        "I": 1,
        "Ⅰ": 1,
        "2": 2,
        "二": 2,
        "II": 2,
        "Ⅱ": 2,
        "3": 3,
        "三": 3,
        "III": 3,
        "Ⅲ": 3,
        "4": 4,
        "四": 4,
        "IV": 4,
        "Ⅳ": 4,
    }
    evidence: list[dict[str, Any]] = []
    levels: set[int] = set()
    for line in body_lines(markdown):
        plain = clean_display_text(line.raw)
        if "响应" not in plain:
            continue
        line_levels: list[int] = []
        for match in _RESPONSE_LEVEL_RE.finditer(plain):
            token = unicodedata.normalize("NFKC", match.group("level")).upper()
            level = mapping.get(token) or mapping.get(match.group("level"))
            if level is not None:
                levels.add(level)
                line_levels.append(level)
        if line_levels:
            evidence.append(
                {"行号": line.number, "原文": plain, "响应级别": sorted(set(line_levels))}
            )
    return {"响应级别": sorted(levels), "响应级别证据": evidence}


def section_contains_keyword(section: LocatedSection, keyword: str) -> list[dict[str, Any]]:
    return [
        {"行号": line.number, "原文": clean_display_text(line.raw)}
        for line in section.content_lines
        if keyword in clean_display_text(line.raw)
    ]


def section_has_image_marker(section: LocatedSection) -> tuple[bool, list[int]]:
    """边界标题前的图片仍属于上一节，兼容 Word 图片与下一标题同段。"""

    candidates = list(section.content_lines)
    if section.boundary_line is not None:
        candidates.append(section.boundary_line)
    lines = [
        line.number
        for line in candidates
        if _CUSTOM_RASTER_IMAGE_RE.search(line.raw) or _MARKDOWN_IMAGE_RE.search(line.raw)
    ]
    return bool(lines), lines
