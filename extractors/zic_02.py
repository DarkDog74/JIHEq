from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .dj_04 import _is_separator_row, _split_markdown_row
from .registry import register_extractor


class ZIC02ExtractionError(ValueError):
    """ZIC-02 的资产类型列无法可靠提取。"""


_CATALOG_PATH = Path(__file__).parent.parent / "reference_data" / "zic_02_asset_types.json"


def _normalize_asset_type(value: str) -> str:
    """仅消除排版差异，不把不同业务类型视为同一类型。"""
    return re.sub(r"\s+", "", value).replace("（", "(").replace("）", ")").replace("其它", "其他")


def _load_standard_types() -> set[str]:
    try:
        payload = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        types = payload["asset_types"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ZIC02ExtractionError(f"无法读取资产类型划分标准表：{error}") from error
    if not isinstance(types, list) or not all(isinstance(value, str) for value in types):
        raise ZIC02ExtractionError("资产类型划分标准表格式无效")
    return {_normalize_asset_type(value) for value in types}


def extract_zic_02_text(markdown: str) -> dict[str, Any]:
    standard_types = _load_standard_types()
    lines = markdown.splitlines()
    headers: list[str] | None = None
    type_index: int | None = None
    row_count = 0
    invalid_types: list[dict[str, object]] = []

    for markdown_line, line in enumerate(lines, start=1):
        cells = _split_markdown_row(line)
        if cells is None:
            headers = None
            type_index = None
            continue
        if _is_separator_row(cells):
            continue
        if headers is None:
            headers = cells
            normalized_headers = [_normalize_asset_type(value) for value in headers]
            type_index = (
                normalized_headers.index("资产类型")
                if "资产类型" in normalized_headers
                else None
            )
            continue
        if type_index is None:
            continue

        row_count += 1
        asset_type = cells[type_index].strip() if type_index < len(cells) else ""
        normalized_type = _normalize_asset_type(asset_type)
        if normalized_type in standard_types:
            continue
        invalid_types.append(
            {
                "资产清单行号": row_count,
                "Markdown行号": markdown_line,
                "资产类型": asset_type,
                "标准化资产类型": normalized_type,
            }
        )

    if row_count == 0:
        raise ZIC02ExtractionError("资产清单中未找到包含“资产类型”列的数据表格")
    return {
        "资产类型行数": row_count,
        "不在资产类型划分标准表的类型": invalid_types,
    }


@register_extractor("ZIC-02", document_name_contains=("资产清单",))
def extract_zic_02(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入 Markdown 不存在：{path}")
    return extract_zic_02_text(path.read_text(encoding="utf-8-sig"))
