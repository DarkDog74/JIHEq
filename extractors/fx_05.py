from __future__ import annotations

import ipaddress
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from .dj_02 import _parse_heading
from .dj_04 import _is_separator_row, _normalize_label, _split_markdown_row
from .errors import ExtractorNotApplicableError, TargetSectionNotFoundError
from .registry import register_extractor


class FX05ExtractionError(ValueError):
    """FX-05 的风险报告资产数量或资产清单记录数无法可靠提取。"""


class FX05SectionNotFoundError(FX05ExtractionError, TargetSectionNotFoundError):
    """风险评估报告缺少“系统组件和单元资产”目标章节。"""


class FX05QuantityTableNotFoundError(
    FX05ExtractionError, ExtractorNotApplicableError
):
    """目标章节没有解析出含有效数量的资产表。"""


class FX05AssetTableNotFoundError(FX05ExtractionError, ExtractorNotApplicableError):
    """资产清单没有解析出资产记录表。"""


class FX05IncompleteMarkdownError(FX05ExtractionError, ExtractorNotApplicableError):
    """FX-05 输入 Markdown 是显式解析失败存根。"""


_NUMBERED_HEADING_RE = re.compile(r"^\d+(?:\.\d+)*[.．、]?\s*")
_TOC_LEADER_RE = re.compile(r"(?:\.{4,}|…{2,}|·{4,})")
_SECTION_NUMBER_RE = re.compile(
    r"(?<![\d.．])(?P<number>\d+(?:\.\d+)+)[.．、]?\s*"
)
_NUMBERED_TITLE_RE = re.compile(
    r"(?<![\d.．])(?P<number>\d+(?:\.\d+)*)[.．、]\s*"
    r"(?P<title>[\u4e00-\u9fff])"
)
_TARGET_TEXT_RE = re.compile(
    r"系\s*统\s*组\s*件\s*和\s*单\s*元\s*资\s*产"
    r"(?:"
    r"(?P<identification>\s*识\s*别)(?=\s|[|：:]|$)|"
    r"(?=\s|[|：:]|$)"
    r")"
    r"(?!\s*(?:价\s*值|风\s*险|赋\s*值|名\s*称))"
)
_TARGET_TITLES = ("系统组件和单元资产", "系统组件和单元资产识别")
_PARSE_ERROR_RE = re.compile(r"<!--\s*PARSE_ERROR\b", re.IGNORECASE)
_QUANTITY_RE = re.compile(r"^(?P<value>\d+)(?:\.0+)?(?:台|套|个|项|部|份)?$")
_IP_TOKEN_SPLIT_RE = re.compile(r"(?:<br\s*/?>|[、,，;；\s]+)", re.IGNORECASE)


def _normalize_heading(title: str) -> str:
    candidate = title.replace("**", "").replace("__", "").replace("`", "")
    candidate = re.sub(r"\s+", "", candidate).strip("：:")
    return _NUMBERED_HEADING_RE.sub("", candidate)


def _section_number(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in value.split("."))


def _flat_target(line: str) -> tuple[tuple[int, ...], str] | None:
    candidate = line.strip()
    if not candidate or _TOC_LEADER_RE.search(candidate) or candidate.startswith("["):
        return None
    target_match = _TARGET_TEXT_RE.search(candidate)
    if target_match is None:
        return None
    number_matches = list(_SECTION_NUMBER_RE.finditer(candidate[: target_match.start()]))
    if not number_matches:
        return None
    number_match = number_matches[-1]
    if not candidate.startswith("|"):
        between = candidate[number_match.end() : target_match.start()]
        if number_match.start() != 0 or re.sub(r"[\s*_`]", "", between):
            return None
    title = (
        "系统组件和单元资产识别"
        if target_match.group("identification")
        else "系统组件和单元资产"
    )
    return _section_number(number_match.group("number")), title


def _is_next_peer_or_higher_heading(
    line: str, current_number: tuple[int, ...]
) -> bool:
    candidate = line.strip()
    if not candidate or _TOC_LEADER_RE.search(candidate) or candidate.startswith("["):
        return False
    for match in _NUMBERED_TITLE_RE.finditer(candidate):
        if not candidate.startswith("|") and match.start() != 0:
            continue
        number = _section_number(match.group("number"))
        if len(number) <= len(current_number) and number > current_number:
            return True
    return False


def _target_section(markdown: str) -> tuple[list[str], int, str, str]:
    lines = markdown.splitlines()
    matches: dict[str, list[tuple[int, int, str]]] = {
        title: [] for title in _TARGET_TITLES
    }
    for index, line in enumerate(lines):
        heading = _parse_heading(line)
        if heading is None:
            continue
        level, title = heading
        normalized = _normalize_heading(title)
        if normalized in matches:
            matches[normalized].append((index, level, title))

    flat_matches: dict[str, list[tuple[int, tuple[int, ...]]]] = {
        title: [] for title in _TARGET_TITLES
    }
    for index, line in enumerate(lines):
        match = _flat_target(line)
        if match is not None:
            number, title = match
            flat_matches[title].append((index, number))

    # 精确标题优先；仅当精确标题不存在时，接受主流模板中的“……识别”标题。
    for target_title in _TARGET_TITLES:
        markdown_selected = matches[target_title]
        if len(markdown_selected) > 1:
            locations = "、".join(str(index + 1) for index, _, _ in markdown_selected)
            raise FX05ExtractionError(
                f"风险评估报告中发现多个目标标题，行号：{locations}"
            )
        if markdown_selected:
            start, level, title = markdown_selected[0]
            end = len(lines)
            for index in range(start + 1, len(lines)):
                heading = _parse_heading(lines[index])
                if heading is not None and heading[0] <= level:
                    end = index
                    break
            return lines[start + 1 : end], start + 1, title, "Markdown标题"

        flat_selected = flat_matches[target_title]
        if len(flat_selected) > 1:
            locations = "、".join(str(index + 1) for index, _ in flat_selected)
            raise FX05ExtractionError(
                f"风险评估报告中发现多个压平目标标题，行号：{locations}"
            )
        if flat_selected:
            start, number = flat_selected[0]
            end = len(lines)
            for index in range(start + 1, len(lines)):
                if _is_next_peer_or_higher_heading(lines[index], number):
                    end = index
                    break
            return lines[start:end], start + 1, target_title, "PDF压平编号标题"

    raise FX05SectionNotFoundError(
        "风险评估报告中未找到标题“系统组件和单元资产”或“系统组件和单元资产识别”"
    )


def _iter_markdown_tables(lines: Iterable[str]) -> Iterable[tuple[int, list[list[str]]]]:
    material = list(lines)
    index = 0
    while index < len(material):
        first = _split_markdown_row(material[index])
        if first is None:
            index += 1
            continue
        start_line = index + 1
        rows: list[list[str]] = []
        has_separator = False
        while index < len(material):
            row = _split_markdown_row(material[index])
            if row is None:
                break
            rows.append(row)
            has_separator = has_separator or _is_separator_row(row)
            index += 1
        if has_separator:
            yield start_line, rows


def _normalized_cells(row: list[str]) -> list[str]:
    return [_normalize_label(cell) for cell in row]


def _parse_quantity(value: str) -> int | None:
    candidate = _normalize_label(value).replace(",", "").replace("，", "")
    if not candidate or candidate in {"/", "-", "—", "无", "不适用"}:
        return None
    match = _QUANTITY_RE.fullmatch(candidate)
    return int(match.group("value")) if match is not None else None


def extract_quantity_tables(
    lines: Iterable[str], *, line_offset: int = 0
) -> tuple[int, list[dict[str, object]]]:
    details: list[dict[str, object]] = []
    total = 0
    for table_number, (start_line, rows) in enumerate(
        _iter_markdown_tables(lines), start=1
    ):
        data_rows = [row for row in rows if not _is_separator_row(row)]
        for header_index, header in enumerate(data_rows):
            quantity_columns = [
                index
                for index, label in enumerate(_normalized_cells(header))
                if label == "数量"
            ]
            if not quantity_columns:
                continue

            values: list[int] = []
            for row in data_rows[header_index + 1 :]:
                labels = _normalized_cells(row)
                # PDF 多页表格常把多个子表压成一个 Markdown 表；遇到下一表头即结束。
                if any(label == "序号" for label in labels):
                    break
                for column in quantity_columns:
                    if column >= len(row):
                        continue
                    quantity = _parse_quantity(row[column])
                    if quantity is not None:
                        values.append(quantity)

            if not values:
                continue
            subtotal = sum(values)
            total += subtotal
            details.append(
                {
                    "表格序号": table_number,
                    "表头行": line_offset + start_line + header_index,
                    "数量列": [column + 1 for column in quantity_columns],
                    "数量值": values,
                    "数量合计": subtotal,
                }
            )
    return total, details


def _normalize_ip(value: str) -> str | None:
    candidate = unicodedata.normalize("NFKC", value).strip()
    candidate = candidate.strip("，,;；。．（）()[]【】")
    if not candidate:
        return None
    try:
        return str(ipaddress.ip_address(candidate)).casefold()
    except ValueError:
        try:
            return str(ipaddress.ip_interface(candidate).ip).casefold()
        except ValueError:
            return None


def _row_ips(row: list[str]) -> list[str]:
    values: list[str] = []
    for cell in row:
        for token in _IP_TOKEN_SPLIT_RE.split(cell):
            normalized = _normalize_ip(token)
            if normalized is not None:
                values.append(normalized)
    return sorted(set(values))


def extract_ip_rows(
    lines: Iterable[str], *, line_offset: int = 0
) -> tuple[int, list[dict[str, object]]]:
    """统计目标章节中含有效 IP 的表格数据行。

    同一行即使包含多个 IP 也只计一份资产；重复 IP 出现在不同行时按行计数。
    """
    details: list[dict[str, object]] = []
    total = 0
    for table_number, (start_line, rows) in enumerate(
        _iter_markdown_tables(lines), start=1
    ):
        ip_rows: list[dict[str, object]] = []
        for row_index, row in enumerate(rows):
            if _is_separator_row(row):
                continue
            ips = _row_ips(row)
            if not ips:
                continue
            ip_rows.append(
                {
                    "行号": line_offset + start_line + row_index,
                    "IP": ips,
                }
            )
        if not ip_rows:
            continue
        total += len(ip_rows)
        details.append(
            {
                "表格序号": table_number,
                "IP行数": len(ip_rows),
                "IP行": ip_rows,
            }
        )
    return total, details


def extract_asset_inventory_count(
    lines: Iterable[str],
) -> tuple[int, list[dict[str, object]]]:
    details: list[dict[str, object]] = []
    total = 0
    for table_number, (start_line, rows) in enumerate(
        _iter_markdown_tables(lines), start=1
    ):
        data_rows = [row for row in rows if not _is_separator_row(row)]
        header_indexes = [
            index
            for index, row in enumerate(data_rows)
            if "资产IP" in _normalized_cells(row)
        ]
        for position, header_index in enumerate(header_indexes):
            end = (
                header_indexes[position + 1]
                if position + 1 < len(header_indexes)
                else len(data_rows)
            )
            record_count = sum(
                1
                for row in data_rows[header_index + 1 : end]
                if any(_normalize_label(cell) for cell in row)
                and "资产IP" not in _normalized_cells(row)
            )
            if not record_count:
                continue
            total += record_count
            details.append(
                {
                    "表格序号": table_number,
                    "表头行": start_line + header_index,
                    "记录数": record_count,
                }
            )
    return total, details


def extract_risk_report_asset_data(risk_markdown: str) -> dict[str, Any]:
    """提取风险评估报告目标章节中的资产数量及定位证据。"""
    if _PARSE_ERROR_RE.search(risk_markdown):
        raise FX05IncompleteMarkdownError(
            "输入为显式解析失败存根：风险评估报告.md"
        )

    section_lines, heading_line, heading_title, location_method = _target_section(
        risk_markdown
    )
    risk_count, quantity_tables = extract_quantity_tables(
        section_lines, line_offset=heading_line
    )
    ip_row_tables: list[dict[str, object]] = []
    quantity_source = "数量列"
    if not quantity_tables:
        risk_count, ip_row_tables = extract_ip_rows(
            section_lines, line_offset=heading_line
        )
        quantity_source = "IP行兜底"
        if not ip_row_tables:
            raise FX05QuantityTableNotFoundError(
                "“系统组件和单元资产”章节中既未解析出含有效数值的“数量”列，"
                "也未解析出含有效IP的表格行"
            )

    return {
        "目标章节标题": heading_title,
        "目标章节标题行": heading_line,
        "目标章节定位方式": location_method,
        "风险评估报告数量来源": quantity_source,
        "风险评估报告数量明细": quantity_tables,
        "风险评估报告IP行明细": ip_row_tables,
        "风险评估报告资产数量": risk_count,
    }


def extract_fx_05_data(risk_markdown: str, asset_markdown: str) -> dict[str, Any]:
    failed_inputs = [
        name
        for name, markdown in (
            ("风险评估报告.md", risk_markdown),
            ("资产清单.md", asset_markdown),
        )
        if _PARSE_ERROR_RE.search(markdown)
    ]
    if failed_inputs:
        raise FX05IncompleteMarkdownError(
            f"输入为显式解析失败存根：{'、'.join(failed_inputs)}"
        )

    risk_fields = extract_risk_report_asset_data(risk_markdown)

    asset_count, asset_tables = extract_asset_inventory_count(
        asset_markdown.splitlines()
    )
    if not asset_tables:
        raise FX05AssetTableNotFoundError(
            "资产清单中未解析出以“资产IP”为表头的资产记录表"
        )

    return {
        **risk_fields,
        "资产清单记录明细": asset_tables,
        "资产清单记录数": asset_count,
    }


@register_extractor(
    "FX-05",
    document_name_contains=("风险评估报告.md",),
    additional_document_name_groups=(("资产清单.md",),),
)
def extract_fx_05(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(source, tuple) or len(source) != 2:
        raise FX05ExtractionError("FX-05 需要风险评估报告 Markdown 和资产清单 Markdown")
    risk_path, asset_path = source
    for path in source:
        if not path.is_file():
            raise FileNotFoundError(f"输入文件不存在：{path}")
    return extract_fx_05_data(
        risk_path.read_text(encoding="utf-8-sig"),
        asset_path.read_text(encoding="utf-8-sig"),
    )
