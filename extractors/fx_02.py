from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from .dj_02 import _extract_declared_level, _normalize_heading_label, _parse_heading
from .dj_04 import _normalize_label, _split_markdown_row
from .errors import MultipleRequiredDocumentsError, RequiredDocumentNotFoundError
from .registry import register_extractor


class FX02ExtractionError(ValueError):
    """FX-02 所需的安全等级或风险评估日期无法可靠提取。"""


_DATE_RE = re.compile(
    r"(?<!\d)(?P<year>20\d{2})\s*[./年-]\s*(?P<month>\d{1,2})"
    r"\s*[./月-]\s*(?P<day>\d{1,2})\s*日?(?!\d)"
)
_DATE_LABEL_RE = re.compile(r"检验开始|检验结束|到样日期|签发日期")
_DIRECT_END_DATE_RE = re.compile(
    r"检验结束\s*(?:日期)?\s*[：:]?\s*" + _DATE_RE.pattern
)


def _parse_date_match(match: re.Match[str]) -> date:
    raw = match.group(0).strip()
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError as error:
        raise FX02ExtractionError(f"检验结束日期无效：{raw}") from error


def _conclusion_section(markdown: str) -> str:
    lines = markdown.splitlines()
    heading_candidates: list[int] = []
    table_candidates: list[int] = []

    for index, line in enumerate(lines):
        heading = _parse_heading(line)
        if heading is not None and _normalize_heading_label(heading[1]) == "检验结论":
            heading_candidates.append(index)
            continue
        cells = _split_markdown_row(line)
        if cells is None:
            continue
        nonempty = [cell for cell in cells if cell.strip()]
        if len(nonempty) == 1 and _normalize_label(nonempty[0]).endswith("检验结论"):
            table_candidates.append(index)

    candidates = heading_candidates or table_candidates
    if not candidates:
        raise FX02ExtractionError("风险评估报告中未找到“检验结论”标题")
    if len(candidates) > 1:
        locations = "、".join(str(index + 1) for index in candidates)
        raise FX02ExtractionError(f"风险评估报告中发现多个“检验结论”标题，行号：{locations}")

    start = candidates[0]
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _parse_heading(lines[index]) is not None:
            end = index
            break
    return "\n".join(lines[start + 1 : end])


def extract_inspection_end_date(markdown: str) -> date:
    section = _conclusion_section(markdown)
    matches: list[re.Match[str]] = []

    for line in section.splitlines():
        cells = _split_markdown_row(line)
        if cells is None:
            continue
        for index, cell in enumerate(cells):
            normalized = _normalize_label(cell)
            if normalized == "检验结束":
                for value in cells[index + 1 :]:
                    candidate = _DATE_RE.search(value)
                    if candidate is not None:
                        matches.append(candidate)
                        break
                continue
            if "检验结束" not in normalized:
                continue
            labels = list(_DATE_LABEL_RE.finditer(cell))
            target_positions = [
                position
                for position, label in enumerate(labels)
                if label.group(0) == "检验结束"
            ]
            if len(target_positions) != 1:
                continue
            date_matches = list(_DATE_RE.finditer(" ".join(cells[index + 1 :])))
            target_position = target_positions[0]
            if target_position < len(date_matches):
                matches.append(date_matches[target_position])

    if not matches:
        direct_match = _DIRECT_END_DATE_RE.search(section)
        if direct_match is not None:
            matches.append(direct_match)
    if not matches:
        raise FX02ExtractionError("“检验结论”中未找到“检验结束”日期")

    values = {_parse_date_match(match) for match in matches}
    if len(values) > 1:
        shown = "、".join(sorted(value.isoformat() for value in values))
        raise FX02ExtractionError(f"“检验结论”中出现多个检验结束日期：{shown}")
    return values.pop()


def extract_fx_02_data(
    grading_report: str,
    current_risk_report: str | None = None,
    previous_risk_report: str | None = None,
) -> dict[str, Any]:
    declared_level = _extract_declared_level(grading_report)
    if declared_level is None:
        raise FX02ExtractionError(
            "定级报告的定级方法/安全等级计算方法章节中未找到声明安全等级"
        )
    if declared_level == 1:
        return {
            "声明安全等级": declared_level,
            "当前风险评估检验结束日期": None,
            "上一份风险评估检验结束日期": None,
            "时间间隔天数": None,
        }
    if current_risk_report is None or previous_risk_report is None:
        raise FX02ExtractionError("二级及以上系统需要当前和上一份风险评估报告")

    current_date = extract_inspection_end_date(current_risk_report)
    previous_date = extract_inspection_end_date(previous_risk_report)
    return {
        "声明安全等级": declared_level,
        "当前风险评估检验结束日期": current_date.isoformat(),
        "上一份风险评估检验结束日期": previous_date.isoformat(),
        "时间间隔天数": (current_date - previous_date).days,
    }


def _select_exact_file(unit_dir: Path, filename: str) -> Path:
    matches = sorted(
        (path for path in unit_dir.rglob(filename) if path.is_file()),
        key=lambda path: str(path.relative_to(unit_dir)),
    )
    if not matches:
        raise RequiredDocumentNotFoundError(
            f"单位目录中没有“{filename}”：{unit_dir}", requirement=filename
        )
    if len(matches) > 1:
        names = "、".join(str(path.relative_to(unit_dir)) for path in matches)
        raise MultipleRequiredDocumentsError(
            f"单位目录中找到多份“{filename}”：{names}"
        )
    return matches[0]


@register_extractor("FX-02", document_name_contains=("定级报告",))
def extract_fx_02(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(source, Path):
        raise FX02ExtractionError("FX-02 只接受一份定级报告作为主输入")
    if not source.is_file():
        raise FileNotFoundError(f"输入定级报告不存在：{source}")

    grading_report = source.read_text(encoding="utf-8-sig")
    declared_level = _extract_declared_level(grading_report)
    if declared_level is None:
        raise FX02ExtractionError(
            "定级报告的定级方法/安全等级计算方法章节中未找到声明安全等级"
        )
    if declared_level == 1:
        return extract_fx_02_data(grading_report)

    current_path = _select_exact_file(source.parent, "风险评估报告.md")
    previous_path = _select_exact_file(source.parent, "上一份风险评估报告.md")
    return extract_fx_02_data(
        grading_report,
        current_path.read_text(encoding="utf-8-sig"),
        previous_path.read_text(encoding="utf-8-sig"),
    )
