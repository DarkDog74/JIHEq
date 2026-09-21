from __future__ import annotations

import itertools
import ipaddress
import re
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from .dj_04 import _is_separator_row, _normalize_label
from .errors import ExtractorNotApplicableError
from .fx_05 import _iter_markdown_tables, _target_section
from .registry import register_extractor


class FX07ExtractionError(ExtractorNotApplicableError):
    """FX-07 的风险报告或定级报告无法读取。"""


class FX07GradingReportAssetTableNotFoundError(FX07ExtractionError):
    """保留的兼容异常类；缺少资产表现由规则判为不通过。"""


class FX07IncompleteMarkdownError(FX07ExtractionError):
    """FX-07 输入 Markdown 是显式解析失败存根。"""


_PARSE_ERROR_RE = re.compile(r"<!--\s*PARSE_ERROR\b", re.IGNORECASE)
_APPENDIX_MARKER_RE = re.compile(r"附[^。\n|]*资产[^。\n|]*(?:信息|清单|表)")
_TARGET_FIELDS = ("IP", "类型", "型号")
_EXACT_HEADERS = {"IP": "资产ip", "类型": "资产类型", "型号": "资产型号"}


def _normalized_header(value: str) -> str:
    return _normalize_label(value).casefold()


def _matches_header(field: str, value: str) -> bool:
    normalized = _normalized_header(value)
    if field == "IP":
        return "ip" in normalized
    return field in normalized


def _column_indexes(row: list[str]) -> dict[str, int] | None:
    """从同一表头行中选出 IP、类型、型号三列。

    精确的“资产IP/资产类型/资产型号”优先；精确列不存在时，才使用
    “私网IP地址/设备类型/品牌及型号”一类包含关键词的列。三列必须互不相同。
    """
    candidates: dict[str, list[tuple[int, int]]] = {}
    for field in _TARGET_FIELDS:
        matches: list[tuple[int, int]] = []
        for index, cell in enumerate(row):
            if not _matches_header(field, cell):
                continue
            exact_rank = int(_normalized_header(cell) != _EXACT_HEADERS[field])
            matches.append((exact_rank, index))
        if not matches:
            return None
        candidates[field] = sorted(matches)

    choices: list[
        tuple[tuple[int, tuple[int, ...], tuple[int, ...]], dict[str, int]]
    ] = []
    for combination in itertools.product(
        *(candidates[field] for field in _TARGET_FIELDS)
    ):
        indexes = tuple(item[1] for item in combination)
        if len(set(indexes)) != len(_TARGET_FIELDS):
            continue
        ranks = tuple(item[0] for item in combination)
        mapping = dict(zip(_TARGET_FIELDS, indexes))
        choices.append(((sum(ranks), ranks, indexes), mapping))
    if not choices:
        return None
    return min(choices, key=lambda item: item[0])[1]


def _present_header_fields(row: list[str]) -> set[str]:
    return {
        field
        for field in _TARGET_FIELDS
        if any(_matches_header(field, cell) for cell in row)
    }


def _normalize_value(value: str) -> str:
    candidate = unicodedata.normalize("NFKC", value or "")
    candidate = candidate.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\s+", " ", candidate).strip()


def _normalize_ip(value: str) -> str:
    """统一 IP 大小写、空格和 IPv6 展开/压缩写法。"""
    candidate = re.sub(r"\s+", "", _normalize_value(value)).casefold()
    if not candidate:
        return ""
    try:
        return str(ipaddress.ip_address(candidate)).casefold()
    except ValueError:
        try:
            return str(ipaddress.ip_interface(candidate).ip).casefold()
        except ValueError:
            return candidate


def _best_missing_columns(lines: Iterable[str]) -> list[str]:
    best: set[str] = set()
    for _, rows in _iter_markdown_tables(lines):
        for row in rows:
            if _is_separator_row(row):
                continue
            present = _present_header_fields(row)
            if len(present) > len(best):
                best = present
    return [field for field in _TARGET_FIELDS if field not in best]


def _extract_asset_records(
    lines: Iterable[str], *, line_offset: int = 0
) -> dict[str, Any]:
    material = list(lines)
    records: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    table_evidence: list[dict[str, Any]] = []

    for table_number, (start_line, rows) in enumerate(
        _iter_markdown_tables(material), start=1
    ):
        header_indexes = [
            index
            for index, row in enumerate(rows)
            if _column_indexes(row) is not None
        ]
        for position, header_index in enumerate(header_indexes):
            columns = _column_indexes(rows[header_index])
            assert columns is not None
            end = (
                header_indexes[position + 1]
                if position + 1 < len(header_indexes)
                else len(rows)
            )
            table_record_count = 0
            table_incomplete_count = 0
            for row_index in range(header_index + 1, end):
                row = rows[row_index]
                if _is_separator_row(row):
                    continue
                values = {
                    field: _normalize_value(row[column]) if column < len(row) else ""
                    for field, column in columns.items()
                }
                if not any(values.values()):
                    continue
                record = {
                    "IP": _normalize_ip(values["IP"]),
                    "类型": values["类型"],
                    "型号": values["型号"],
                    "行号": line_offset + start_line + row_index,
                }
                missing = [field for field in _TARGET_FIELDS if not record[field]]
                if missing:
                    incomplete.append({**record, "缺失字段": missing})
                    table_incomplete_count += 1
                else:
                    records.append(record)
                    table_record_count += 1

            table_evidence.append(
                {
                    "表格序号": table_number,
                    "表头行": line_offset + start_line + header_index,
                    "列名": {
                        field: rows[header_index][column]
                        for field, column in columns.items()
                    },
                    "完整记录数": table_record_count,
                    "不完整记录数": table_incomplete_count,
                }
            )

    return {
        "记录": records,
        "不完整记录": incomplete,
        "表格证据": table_evidence,
        "缺失列": [] if table_evidence else _best_missing_columns(material),
    }


def _grading_appendix_scope(markdown: str) -> tuple[list[str], int, str]:
    lines = markdown.splitlines()
    marker_indexes = [
        index for index, line in enumerate(lines) if _APPENDIX_MARKER_RE.search(line)
    ]
    if marker_indexes:
        start = marker_indexes[-1] + 1
        return lines[start:], start, "资产附表标记"
    # 部分 Markdown 会丢失附表前的普通段落，此时仅找严格三列同行表头。
    return lines, 0, "全文三列表头兜底"


def extract_fx_07_documents(
    risk_report_path: Path, grading_report_path: Path
) -> dict[str, Any]:
    """提取风险评估报告和定级报告的 (IP, 类型, 型号) 资产记录。"""
    for path in (risk_report_path, grading_report_path):
        if not path.is_file():
            raise FileNotFoundError(f"输入 Markdown 不存在：{path}")

    risk_markdown = risk_report_path.read_text(encoding="utf-8-sig")
    grading_markdown = grading_report_path.read_text(encoding="utf-8-sig")
    failed_inputs = [
        label
        for label, markdown in (
            ("风险评估报告.md", risk_markdown),
            ("定级报告.md", grading_markdown),
        )
        if _PARSE_ERROR_RE.search(markdown)
    ]
    if failed_inputs:
        raise FX07IncompleteMarkdownError(
            f"输入为显式解析失败存根：{'、'.join(failed_inputs)}"
        )

    risk_lines, risk_heading_line, heading_title, location_method = _target_section(
        risk_markdown
    )
    risk_assets = _extract_asset_records(risk_lines, line_offset=risk_heading_line)

    grading_lines, grading_offset, grading_location_method = _grading_appendix_scope(
        grading_markdown
    )
    grading_assets = _extract_asset_records(
        grading_lines, line_offset=grading_offset
    )

    risk_count = len(risk_assets["记录"]) + len(risk_assets["不完整记录"])
    grading_count = len(grading_assets["记录"]) + len(
        grading_assets["不完整记录"]
    )
    return {
        "目标章节标题": heading_title,
        "目标章节标题行": risk_heading_line,
        "目标章节定位方式": location_method,
        "风险评估报告资产记录": risk_assets["记录"],
        "风险评估报告不完整资产记录": risk_assets["不完整记录"],
        "风险评估报告资产表证据": risk_assets["表格证据"],
        "风险评估报告资产表缺失列": risk_assets["缺失列"],
        "风险评估报告资产数量": risk_count,
        "定级报告资产附表定位方式": grading_location_method,
        "定级报告资产记录": grading_assets["记录"],
        "定级报告不完整资产记录": grading_assets["不完整记录"],
        "定级报告资产表证据": grading_assets["表格证据"],
        "定级报告资产表缺失列": grading_assets["缺失列"],
        "定级报告资产数量明细": grading_assets["表格证据"],
        "定级报告资产数量": grading_count,
    }


@register_extractor(
    "FX-07",
    document_name_contains=("风险评估报告.md",),
    additional_document_name_groups=(("定级报告.md",),),
)
def extract_fx_07(paths: Path | tuple[Path, ...]) -> dict[str, Any]:
    if isinstance(paths, Path) or len(paths) != 2:
        raise FX07ExtractionError(
            "FX-07 需要同时输入风险评估报告 Markdown 和定级报告 Markdown"
        )
    return extract_fx_07_documents(paths[0], paths[1])
