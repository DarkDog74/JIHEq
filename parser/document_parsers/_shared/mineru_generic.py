# -*- coding: utf-8 -*-
"""Convert MinerU content_list_v2 output to ordered, business-friendly JSON.

This module is intentionally category-neutral.  It preserves document order,
builds a heading tree, copies image assets, and converts HTML tables to either
key/value objects, record arrays, or a lossless matrix fallback.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import statistics
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


PARSER_VERSION = "1.0.0"


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n+ *", "\n", text)
    return text.strip()


def inline_text(value: Any) -> str:
    """Extract only human-visible text from MinerU's nested inline objects."""
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return clean_text("\n".join(filter(None, (inline_text(item) for item in value))))
    if not isinstance(value, dict):
        return ""
    if isinstance(value.get("content"), str):
        return clean_text(value["content"])
    preferred = (
        "title_content",
        "paragraph_content",
        "list_content",
        "page_header_content",
        "page_footer_content",
        "page_number_content",
        "page_aside_text_content",
        "image_caption",
        "chart_caption",
        "table_caption",
        "table_footnote",
        "math_content",
    )
    parts = [inline_text(value[key]) for key in preferred if key in value]
    return clean_text("\n".join(filter(None, parts)))


@dataclass(frozen=True)
class Cell:
    row: int
    column: int
    row_span: int
    column_span: int
    text: str
    is_header: bool = False

    @property
    def column_end(self) -> int:
        return self.column + self.column_span


@dataclass
class TableModel:
    cells: list[Cell]
    row_count: int
    column_count: int

    def cell_at(self, row: int, column: int) -> Cell | None:
        for cell in self.cells:
            if (
                cell.row <= row < cell.row + cell.row_span
                and cell.column <= column < cell.column + cell.column_span
            ):
                return cell
        return None

    def origin_cells(self, row: int) -> list[Cell]:
        return sorted((cell for cell in self.cells if cell.row == row), key=lambda cell: cell.column)

    def expanded_value(self, row: int, column: int) -> str:
        cell = self.cell_at(row, column)
        return cell.text if cell is not None else ""

    def data_value(self, row: int, column: int) -> str:
        """Propagate vertical merges, but write horizontal merges only once."""
        cell = self.cell_at(row, column)
        if cell is None or column != cell.column:
            return ""
        return cell.text

    def raw_matrix(self) -> list[list[str]]:
        matrix = [["" for _ in range(self.column_count)] for _ in range(self.row_count)]
        for cell in self.cells:
            matrix[cell.row][cell.column] = cell.text
        return matrix

    def merges(self) -> list[dict[str, int]]:
        return [
            {
                "起始行": cell.row + 1,
                "起始列": cell.column + 1,
                "跨行数": cell.row_span,
                "跨列数": cell.column_span,
            }
            for cell in self.cells
            if cell.row_span > 1 or cell.column_span > 1
        ]


class _TableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, int, int, bool]]] = []
        self._row: list[tuple[str, int, int, bool]] | None = None
        self._cell: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"}:
            values = dict(attrs)
            self._cell = {
                "parts": [],
                "row_span": _positive_int(values.get("rowspan")),
                "column_span": _positive_int(values.get("colspan")),
                "is_header": tag == "th",
            }
        elif tag == "br" and self._cell is not None:
            self._cell["parts"].append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"p", "li"} and self._cell is not None:
            self._cell["parts"].append("\n")
        elif tag in {"td", "th"} and self._cell is not None:
            if self._row is not None:
                self._row.append(
                    (
                        clean_text("".join(self._cell["parts"])),
                        self._cell["row_span"],
                        self._cell["column_span"],
                        self._cell["is_header"],
                    )
                )
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell["parts"].append(data)


def _positive_int(value: Any) -> int:
    try:
        return max(1, int(value or 1))
    except (TypeError, ValueError):
        return 1


def parse_html_table(value: str) -> TableModel | None:
    if not value or "<table" not in value.casefold():
        return None
    parser = _TableHTMLParser()
    try:
        parser.feed(value)
    except Exception:
        return None
    cells: list[Cell] = []
    occupied: set[tuple[int, int]] = set()
    max_column = 0
    max_row = len(parser.rows)
    for row_index, source_row in enumerate(parser.rows):
        column = 0
        for text, row_span, column_span, is_header in source_row:
            while (row_index, column) in occupied:
                column += 1
            cell = Cell(row_index, column, row_span, column_span, text, is_header)
            cells.append(cell)
            for target_row in range(row_index, row_index + row_span):
                for target_column in range(column, column + column_span):
                    occupied.add((target_row, target_column))
            max_row = max(max_row, row_index + row_span)
            max_column = max(max_column, column + column_span)
            column += column_span
    if not cells or not max_column:
        return None
    return TableModel(cells, max_row, max_column)


_FIELD_HINTS = (
    "序号", "名称", "类型", "类别", "项目", "系统", "单位", "部门", "人员", "姓名",
    "时间", "日期", "地点", "地址", "电话", "联系方式", "版本", "范围", "对象", "功能",
    "内容", "描述", "结论", "依据", "来源", "等级", "数量", "型号", "品牌", "厂商",
    "风险", "威胁", "脆弱性", "资产", "措施", "建议", "情况", "状态", "是否", "备注",
    "IP", "端口", "URL", "域名", "负责人", "角色", "权值", "概率", "影响", "结果",
)


def _field_label(value: str) -> str:
    return re.sub(r"\s+", "", clean_text(value)).strip("：:")


def _label_score(value: str) -> float:
    text = _field_label(value)
    if not text or len(text) > 48:
        return 0.0
    if re.fullmatch(r"[\d.,%/\-年月日:： ]+", text):
        return 0.0
    if re.search(r"https?://|@|\b\d{1,3}(?:\.\d{1,3}){3}\b", text, re.I):
        return 0.0
    if text.endswith(("。", "；", ";")):
        return 0.1
    if text.endswith("*") or any(hint.casefold() in text.casefold() for hint in _FIELD_HINTS):
        return 1.0
    if len(text) <= 12 and not re.search(r"[，。；！？]", text):
        return 0.55
    return 0.2


def _unique_name(base: str, used: dict[str, int]) -> str:
    base = _field_label(base) or "未命名字段"
    count = used.get(base, 0) + 1
    used[base] = count
    return base if count == 1 else f"{base}（{count}）"


def _looks_like_record_header(model: TableModel) -> bool:
    if model.row_count < 2 or model.column_count < 2:
        return False
    first = [cell for cell in model.origin_cells(0) if cell.text]
    second = [cell for cell in model.origin_cells(1) if cell.text]
    if len(first) < 2 or not second:
        return False
    # A vertically fixed label plus a horizontal group is the canonical
    # two-level record header.  Conversely, a 2-cell row followed by a
    # 4-cell row is commonly a form row (label + colspan value), not a header.
    if any(cell.row_span > 1 for cell in first) and any(cell.column_span > 1 for cell in first):
        return True
    if (
        len(first) == len(second) == 2
        and first[1].column_span > 1
        and second[1].column_span > 1
    ):
        return False
    if len(first) != len(second):
        return False
    first_score = sum(_label_score(cell.text) for cell in first) / len(first)
    second_score = sum(_label_score(cell.text) for cell in second) / len(second)
    return first_score >= 0.75 and second_score <= 0.55


def _row_pairs(model: TableModel, row_index: int) -> tuple[str, list[tuple[str, str]]] | None:
    cells = [cell for cell in model.origin_cells(row_index) if cell.text or cell.column_span]
    prefix = ""
    covering_groups = [
        cell for cell in model.cells
        if cell.row < row_index < cell.row + cell.row_span and cell.column == 0 and cell.text
    ]
    if covering_groups:
        prefix = _field_label(covering_groups[-1].text)
    if len(cells) % 2 == 1 and cells:
        first = cells[0]
        if first.row_span > 1 or (_label_score(first.text) >= 0.75 and len(cells) >= 3):
            prefix = _field_label(first.text)
            cells = cells[1:]
    if len(cells) < 2 or len(cells) % 2:
        return None
    pairs: list[tuple[str, str]] = []
    for index in range(0, len(cells), 2):
        label = _field_label(cells[index].text)
        if _label_score(label) < 0.5:
            return None
        pairs.append((label, cells[index + 1].text))
    return prefix, pairs


def _key_value_data(model: TableModel) -> tuple[dict[str, str], list[dict[str, Any]]] | None:
    if _looks_like_record_header(model):
        return None
    parsed_rows: list[tuple[int, str, list[tuple[str, str]]]] = []
    nonempty_rows = 0
    for row_index in range(model.row_count):
        if any(cell.text for cell in model.origin_cells(row_index)):
            nonempty_rows += 1
        parsed = _row_pairs(model, row_index)
        if parsed is not None:
            parsed_rows.append((row_index, parsed[0], parsed[1]))
    pair_count = sum(len(pairs) for _, _, pairs in parsed_rows)
    origins = model.origin_cells(0) if model.row_count == 1 else []
    single_form_row = (
        pair_count == 1
        and len(origins) == 2
        and origins[1].column_span > 1
    )
    if not single_form_row and (
        pair_count < 2 or len(parsed_rows) < max(2, int(nonempty_rows * 0.45))
    ):
        return None
    fields: dict[str, str] = {}
    used: dict[str, int] = {}
    parsed_indexes = {row for row, _, _ in parsed_rows}
    for _, prefix, pairs in parsed_rows:
        for label, value in pairs:
            key = f"{prefix}·{label}" if prefix and not label.startswith(prefix) else label
            fields[_unique_name(key, used)] = clean_text(value)
    other_rows = [
        {"原表行号": row + 1, "内容": model.raw_matrix()[row]}
        for row in range(model.row_count)
        if row not in parsed_indexes
    ]
    return fields, other_rows


def _header_depth(model: TableModel) -> int:
    if model.column_count < 2 or model.row_count < 2:
        return 0
    first_values = [model.expanded_value(0, column) for column in range(model.column_count)]
    first_nonempty = [value for value in first_values if value]
    if len(first_nonempty) < 2:
        return 0
    score = sum(_label_score(value) for value in first_nonempty) / len(first_nonempty)
    if score < 0.5 and not all(len(_field_label(value)) <= 18 for value in first_nonempty):
        return 0
    has_groups = any(cell.row == 0 and cell.column_span > 1 for cell in model.cells)
    if has_groups and model.row_count > 2:
        second = [model.expanded_value(1, column) for column in range(model.column_count)]
        second_nonempty = [value for value in second if value]
        if len(second_nonempty) >= 2:
            return 2
    return 1


def _headers(model: TableModel, depth: int) -> list[str]:
    used: dict[str, int] = {}
    result: list[str] = []
    for column in range(model.column_count):
        parts: list[str] = []
        for row in range(depth):
            value = _field_label(model.expanded_value(row, column))
            if value and value not in parts:
                parts.append(value)
        result.append(_unique_name("·".join(parts) or f"列{column + 1}", used))
    return result


def _slice_table(model: TableModel, start: int, end: int) -> TableModel | None:
    if start >= end:
        return None
    cells: list[Cell] = []
    for cell in model.cells:
        if not (start <= cell.row < end):
            continue
        row_span = min(cell.row_span, end - cell.row)
        cells.append(
            Cell(
                cell.row - start,
                cell.column,
                row_span,
                cell.column_span,
                cell.text,
                cell.is_header,
            )
        )
    if not cells:
        return None
    return TableModel(cells, end - start, model.column_count)


def _mixed_regions(model: TableModel) -> list[tuple[str, TableModel]]:
    separators = sorted(
        (
            cell.row,
            clean_text(cell.text),
        )
        for cell in model.cells
        if cell.column == 0
        and cell.column_span == model.column_count
        and cell.row_span == 1
        and clean_text(cell.text)
    )
    if not separators:
        return []
    regions: list[tuple[str, TableModel]] = []
    start = 0
    name = "基本信息"
    for row, separator_name in separators:
        section = _slice_table(model, start, row)
        if section is not None:
            regions.append((name, section))
        name = separator_name
        start = row + 1
    section = _slice_table(model, start, model.row_count)
    if section is not None:
        regions.append((name, section))
    # A single leading caption is just a table name, not a mixed table.
    return regions if len(regions) >= 2 else []


def normalize_table(
    model: TableModel,
    table_number: int,
    page: int,
    caption: str = "",
    *,
    allow_mixed: bool = True,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "类型": "表格",
        "表格编号": f"T{table_number:03d}",
        "页码": page,
    }
    if caption:
        result["表格名称"] = caption
    regions = _mixed_regions(model) if allow_mixed else []
    if regions:
        result["表格类型"] = "混合表"
        result["数据"] = []
        for region_name, region_model in regions:
            normalized = normalize_table(
                region_model,
                table_number,
                page,
                allow_mixed=False,
            )
            region: dict[str, Any] = {
                "区域名称": region_name,
                "区域类型": normalized["表格类型"],
                "内容": normalized["数据"],
            }
            if normalized.get("字段"):
                region["字段"] = normalized["字段"]
            if normalized.get("其他记录"):
                region["其他记录"] = normalized["其他记录"]
            result["数据"].append(region)
    else:
        key_values = _key_value_data(model)
        if key_values is not None:
            fields, other_rows = key_values
            result["表格类型"] = "键值表"
            result["数据"] = fields
            if other_rows:
                result["其他记录"] = other_rows
        else:
            depth = _header_depth(model)
            headers = _headers(model, depth) if depth else [f"列{index + 1}" for index in range(model.column_count)]
            records = [
                {
                    header: model.data_value(row, column)
                    for column, header in enumerate(headers)
                }
                for row in range(depth, model.row_count)
            ]
            result["表格类型"] = "记录表" if depth else "无表头记录"
            result["字段"] = headers
            result["数据"] = records
            if not depth:
                result["原始矩阵"] = model.raw_matrix()
    merges = model.merges()
    if merges:
        result["合并单元格"] = merges
    return result


def _record_rows(model: TableModel, headers: list[str], start_row: int = 0) -> list[dict[str, str]]:
    return [
        {
            header: model.data_value(row, column)
            for column, header in enumerate(headers)
        }
        for row in range(start_row, model.row_count)
    ]


def _nonempty_count(record: dict[str, str]) -> int:
    return sum(bool(clean_text(value)) for value in record.values())


def _join_fragments(left: str, right: str) -> str:
    left = clean_text(left)
    right = clean_text(right)
    if not left:
        return right
    if not right:
        return left
    return f"{left}{right}"


def continuation_records(
    model: TableModel,
    headers: list[str],
    existing_records: list[dict[str, str]],
    *,
    repeated_header_depth: int = 0,
) -> tuple[list[dict[str, str]], bool]:
    """Map a headerless continuation and optionally stitch its split first row."""
    records = _record_rows(model, headers, repeated_header_depth)
    stitched = False
    if not existing_records or len(records) < 2:
        return records, stitched
    first_count = _nonempty_count(records[0])
    following_counts = [_nonempty_count(record) for record in records[1: min(len(records), 8)]]
    normal_count = statistics.median(following_counts) if following_counts else 0
    sparse_limit = max(1, math.ceil(len(headers) * 0.4))
    full_limit = max(2, math.ceil(len(headers) * 0.75))
    if first_count <= sparse_limit and normal_count >= full_limit:
        previous = existing_records[-1]
        for header in headers:
            fragment = records[0].get(header, "")
            if fragment:
                previous[header] = _join_fragments(previous.get(header, ""), fragment)
        records = records[1:]
        stitched = True
    return records, stitched


def _bbox(block: dict[str, Any]) -> list[float]:
    value = block.get("bbox")
    if not isinstance(value, list) or len(value) != 4:
        return [0.0, 0.0, 0.0, 0.0]
    try:
        return [float(part) for part in value]
    except (TypeError, ValueError):
        return [0.0, 0.0, 0.0, 0.0]


def _ordered_blocks(pages: list[Any]) -> Iterable[tuple[int, dict[str, Any]]]:
    for page_number, page in enumerate(pages, 1):
        if not isinstance(page, list):
            continue
        indexed = [(index, block) for index, block in enumerate(page) if isinstance(block, dict)]
        indexed.sort(key=lambda item: (_bbox(item[1])[1], _bbox(item[1])[0], item[0]))
        for _, block in indexed:
            yield page_number, block


def _image_source(block: dict[str, Any]) -> str:
    content = block.get("content") if isinstance(block.get("content"), dict) else {}
    source = content.get("image_source") if isinstance(content.get("image_source"), dict) else {}
    return str(source.get("path") or "")


def _copy_image(
    block: dict[str, Any],
    content_dir: Path,
    assets_dir: Path,
    assets_relative: str,
    image_number: int,
    page: int,
) -> tuple[dict[str, Any] | None, str | None]:
    relative = _image_source(block)
    source = (content_dir / relative).resolve() if relative else None
    if source is None or not source.is_file():
        return None, f"第{page}页图片文件不存在: {relative or '(空路径)'}"
    extension = source.suffix.lower() or ".png"
    filename = f"IMG{image_number:03d}{extension}"
    assets_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, assets_dir / filename)
    content = block.get("content") if isinstance(block.get("content"), dict) else {}
    caption = inline_text(content.get("chart_caption") or content.get("image_caption"))
    value: dict[str, Any] = {
        "类型": "图片",
        "图片编号": f"IMG{image_number:03d}",
        "页码": page,
        "地址": f"{assets_relative}/{filename}",
    }
    if caption:
        value["说明"] = caption
    subtype = clean_text(block.get("sub_type"))
    if subtype:
        value["图片类别"] = subtype
    return value, None


def _is_page_number(value: str) -> bool:
    text = _field_label(value)
    return bool(re.fullmatch(r"第?\d+页(?:共\d+页)?", text))


def _display_name(content_path: Path) -> str:
    name = re.sub(r"_content_list_v2$", "", content_path.stem)
    return re.sub(r"^\d+[_\-]", "", name)


def _is_toc_title(value: str) -> bool:
    return _field_label(value) in {"目录", "目录."}


def _looks_like_toc_entry(value: str) -> bool:
    text = clean_text(value)
    return bool(
        re.search(r"(?:\.\s*){2,}\d*\s*$|\.\s*\d+\s*$|[^\d]\.$", text)
    )


def _heading_level(value: str, mineru_level: int, stack: list[dict[str, Any]]) -> int:
    text = clean_text(value)
    compact = re.sub(r"\s+", "", text)
    if _field_label(text) in {"目录", "注意事项", "注意事项.", "声明", "声明.", "总体评价"}:
        return 1
    if re.match(r"^(?:第[一二三四五六七八九十0-9]+章|[一二三四五六七八九十]+[、．.])", compact):
        return 1
    if re.match(r"^(?:\d+[)）]|[（(]\d+[)）])", compact):
        if stack and re.match(r"^(?:\d+[)）]|[（(]\d+[)）])", re.sub(r"\s+", "", stack[-1]["标题"])):
            return stack[-1]["标题级别"]
        return min((stack[-1]["标题级别"] + 1) if stack else 2, 9)
    dotted = re.match(r"^(\d+(?:\.\d+)*)[.．、]?", compact)
    if dotted:
        number = dotted.group(1)
        components = number.split(".")
        if len(components) > 1:
            return min(len(components), 9)
        # In grading reports, Arabic subsections live under a Chinese-numbered
        # chapter.  Elsewhere a single Arabic number is a top-level chapter.
        if any(
            re.match(r"^[一二三四五六七八九十]+[、．.]", re.sub(r"\s+", "", item["标题"]))
            for item in stack
        ):
            return 2
        return 1
    return max(1, min(9, mineru_level))


def convert_content_list(content_path: str | Path, output_dir: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    source = Path(content_path).expanduser().resolve()
    values = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(values, list):
        raise ValueError(f"content_list_v2 顶层必须是数组: {source}")
    document_name = _display_name(source)
    output = Path(output_dir).expanduser().resolve()
    assets_name = f"{document_name}.assets"
    assets_dir = output / assets_name
    result: dict[str, Any] = {
        "文档名称": document_name,
        "解析来源": "MinerU content_list_v2",
        "页数": len(values),
        "前置内容": [],
        "章节": [],
    }
    warnings: list[str] = []
    skipped: dict[str, int] = {}
    stack: list[dict[str, Any]] = []
    table_number = 0
    image_number = 0
    promoted_header = False
    active_record_table: dict[str, Any] | None = None
    continuation_merges: list[dict[str, Any]] = []
    pages_with_title = {
        page_number
        for page_number, page_values in enumerate(values, 1)
        if isinstance(page_values, list)
        and any(isinstance(item, dict) and item.get("type") == "title" for item in page_values)
    }

    def append_content(value: dict[str, Any]) -> None:
        target = stack[-1]["内容"] if stack else result["前置内容"]
        target.append(value)

    def add_section(level: int, title: str, page: int) -> None:
        nonlocal stack
        level = max(1, min(9, level))
        if not stack and not result["章节"] and level > 1:
            level = 1
        while stack and stack[-1]["标题级别"] >= level:
            stack.pop()
        section: dict[str, Any] = {
            "类型": "章节",
            "标题级别": level,
            "标题": title,
            "页码": page,
            "内容": [],
        }
        if stack:
            stack[-1]["内容"].append(section)
        else:
            result["章节"].append(section)
        stack.append(section)

    for page, block in _ordered_blocks(values):
        kind = clean_text(block.get("type")).casefold()
        content = block.get("content") if isinstance(block.get("content"), dict) else {}
        if kind == "title":
            active_record_table = None
            title = inline_text(content.get("title_content") or content)
            if title:
                if stack and _is_toc_title(stack[-1]["标题"]) and _looks_like_toc_entry(title):
                    append_content({"类型": "段落", "段落类型": "目录项", "页码": page, "文本": title})
                else:
                    level = _heading_level(title, _positive_int(content.get("level")), stack)
                    add_section(level, title, page)
            continue
        if kind == "page_header":
            text = inline_text(content.get("page_header_content") or content)
            if (
                page == 1
                and page not in pages_with_title
                and not promoted_header
                and text
                and not _is_page_number(text)
            ):
                add_section(1, text, page)
                promoted_header = True
            else:
                skipped[kind] = skipped.get(kind, 0) + 1
            continue
        if kind in {"page_number", "page_footer"}:
            skipped[kind] = skipped.get(kind, 0) + 1
            continue
        if kind in {"paragraph", "text", "list", "index"}:
            text = inline_text(content)
            if text:
                active_record_table = None
                append_content({"类型": "段落", "页码": page, "文本": text})
            continue
        if kind == "page_aside_text":
            text = inline_text(content)
            if text:
                active_record_table = None
                append_content({"类型": "文本", "页码": page, "文本": text})
            continue
        if kind == "table":
            html = str(content.get("html") or "")
            model = parse_html_table(html)
            if model is None:
                skipped["空表格或无法解析表格"] = skipped.get("空表格或无法解析表格", 0) + 1
                continue
            caption = inline_text(content.get("table_caption"))
            current_depth = _header_depth(model)
            current_headers = _headers(model, current_depth) if current_depth else []
            same_headers = bool(
                active_record_table
                and current_headers
                and current_headers == active_record_table["headers"]
            )
            generic_caption = bool(
                not caption
                or _field_label(document_name) in _field_label(caption)
            )
            can_continue = bool(
                active_record_table
                and page == active_record_table["last_page"] + 1
                and model.column_count == active_record_table["column_count"]
                and (
                    same_headers
                    or (generic_caption and _key_value_data(model) is None)
                )
            )
            if can_continue and active_record_table is not None:
                repeated_depth = current_depth if same_headers else 0
                records, stitched = continuation_records(
                    model,
                    active_record_table["headers"],
                    active_record_table["node"]["数据"],
                    repeated_header_depth=repeated_depth,
                )
                active_record_table["node"]["数据"].extend(records)
                active_record_table["last_page"] = page
                active_record_table["node"]["页码范围"] = [
                    active_record_table["start_page"],
                    page,
                ]
                active_record_table["node"]["续表块数"] = (
                    active_record_table["node"].get("续表块数", 0) + 1
                )
                continuation_merges.append(
                    {
                        "目标表格": active_record_table["node"]["表格编号"],
                        "续表页码": page,
                        "继承字段": active_record_table["headers"],
                        "与上一页末行拼接": stitched,
                        "新增记录数": len(records),
                    }
                )
                continue
            table_number += 1
            table_value = normalize_table(model, table_number, page, caption)
            append_content(table_value)
            if (
                table_value.get("表格类型") == "记录表"
                and isinstance(table_value.get("字段"), list)
                and isinstance(table_value.get("数据"), list)
            ):
                active_record_table = {
                    "node": table_value,
                    "headers": table_value["字段"],
                    "column_count": model.column_count,
                    "start_page": page,
                    "last_page": page,
                }
            else:
                active_record_table = None
            continue
        if kind in {"image", "chart"}:
            active_record_table = None
            image_number += 1
            value, warning = _copy_image(
                block, source.parent, assets_dir, assets_name, image_number, page
            )
            if value is not None:
                append_content(value)
            if warning:
                warnings.append(warning)
            continue
        if kind == "equation_interline":
            formula = inline_text(content.get("math_content") or content)
            if formula:
                active_record_table = None
                append_content({"类型": "公式", "页码": page, "文本": formula})
            continue
        active_record_table = None
        skipped[kind or "未知"] = skipped.get(kind or "未知", 0) + 1

    statistics = content_statistics(result)
    audit = {
        "status": "needs_review" if warnings else "ok",
        "parser_version": PARSER_VERSION,
        "source_content_list": str(source),
        "document_name": document_name,
        "page_count": len(values),
        "statistics": statistics,
        "table_types": table_type_statistics(result),
        "continuation_merges": continuation_merges,
        "skipped_blocks": skipped,
        "warnings": warnings,
    }
    return result, audit


def _walk_content(report: dict[str, Any]) -> Iterable[dict[str, Any]]:
    def visit(items: list[Any]) -> Iterable[dict[str, Any]]:
        for item in items:
            if not isinstance(item, dict):
                continue
            yield item
            if item.get("类型") == "章节":
                yield from visit(item.get("内容", []))

    yield from visit(report.get("前置内容", []))
    yield from visit(report.get("章节", []))


def content_statistics(report: dict[str, Any]) -> dict[str, int]:
    result = {"章节": 0, "段落": 0, "文本": 0, "表格": 0, "图片": 0, "公式": 0}
    for item in _walk_content(report):
        kind = item.get("类型")
        if kind in result:
            result[kind] += 1
    return result


def table_type_statistics(report: dict[str, Any]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in _walk_content(report):
        if item.get("类型") != "表格":
            continue
        kind = str(item.get("表格类型") or "未知")
        result[kind] = result.get(kind, 0) + 1
    return result
