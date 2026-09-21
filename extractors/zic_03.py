from __future__ import annotations

from pathlib import Path
from typing import Any

from .dj_04 import _is_separator_row, _normalize_label, _split_markdown_row
from .registry import register_extractor


class ZIC03ExtractionError(ValueError):
    """ZIC-03 的资产清单表格无法可靠提取。"""


def _is_asset_header(cells: list[str]) -> bool:
    labels = {_normalize_label(cell) for cell in cells}
    return "资产IP" in labels and "资产类型" in labels


def extract_zic_03_text(markdown: str) -> dict[str, Any]:
    headers: list[str] | None = None
    is_asset_table = False
    asset_table_count = 0
    asset_row_count = 0
    incomplete_rows: list[dict[str, object]] = []

    for markdown_line, line in enumerate(markdown.splitlines(), start=1):
        cells = _split_markdown_row(line)
        if cells is None:
            headers = None
            is_asset_table = False
            continue
        if _is_separator_row(cells):
            continue
        if headers is None:
            headers = cells
            is_asset_table = _is_asset_header(cells)
            if is_asset_table:
                asset_table_count += 1
            continue
        if not is_asset_table:
            continue

        # 全空预留行不是资产记录，不参与完整性稽核。
        if not any(cell.strip() for cell in cells):
            continue
        if len(cells) > len(headers) and any(cell.strip() for cell in cells[len(headers) :]):
            raise ZIC03ExtractionError(
                f"Markdown第{markdown_line}行的资产数据列数超过表头列数"
            )

        asset_row_count += 1
        missing_fields = [
            (header.strip() or f"第{index + 1}列")
            for index, header in enumerate(headers)
            if _normalize_label(header) != "备注"
            and (index >= len(cells) or not cells[index].strip())
        ]
        if missing_fields:
            incomplete_rows.append(
                {
                    "资产清单行号": asset_row_count,
                    "Markdown行号": markdown_line,
                    "缺失字段": missing_fields,
                }
            )

    if asset_table_count == 0:
        raise ZIC03ExtractionError("资产清单中未找到包含“资产IP”和“资产类型”列的数据表格")
    if asset_row_count == 0:
        raise ZIC03ExtractionError("资产清单数据表格中没有资产记录")

    return {
        "资产记录行数": asset_row_count,
        "不完整资产记录": incomplete_rows,
    }


@register_extractor("ZIC-03", document_name_contains=("资产清单",))
def extract_zic_03(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入 Markdown 不存在：{path}")
    return extract_zic_03_text(path.read_text(encoding="utf-8-sig"))
