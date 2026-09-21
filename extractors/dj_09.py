from __future__ import annotations

from pathlib import Path

from .dj_04 import _is_separator_row, _normalize_label, _split_markdown_row
from .registry import register_extractor


class DJ09ExtractionError(ValueError):
    """DJ-09 资产表无法可靠提取。"""


ASSET_FIELDS = ("资产IP", "资产类型", "资产型号", "设备厂商", "网络位置", "责任人")
_NORMALIZED_FIELDS = {_normalize_label(field): field for field in ASSET_FIELDS}
_NORMALIZED_FIELDS[_normalize_label("网络位置（内网/外网）")] = "网络位置"


def _recognized_fields(cells: list[str]) -> set[str]:
    return {
        field
        for cell in cells
        if (field := _NORMALIZED_FIELDS.get(_normalize_label(cell))) is not None
    }


def _column_indexes(cells: list[str]) -> dict[str, int] | None:
    indexes: dict[str, int] = {}
    for index, cell in enumerate(cells):
        field = _NORMALIZED_FIELDS.get(_normalize_label(cell))
        if field is None:
            continue
        if field in indexes:
            raise DJ09ExtractionError(f"资产表表头重复出现“{field}”列")
        indexes[field] = index
    return indexes if set(indexes) == set(ASSET_FIELDS) else None


def _best_partial_header(markdown: str) -> set[str]:
    best: set[str] = set()
    for line in markdown.splitlines():
        cells = _split_markdown_row(line)
        if cells is None:
            continue
        recognized = _recognized_fields(cells)
        if "资产IP" in recognized and len(recognized) > len(best):
            best = recognized
    return best


def _extract_candidate_tables(markdown: str) -> list[list[dict[str, str]]]:
    lines = markdown.splitlines()
    candidates: list[list[dict[str, str]]] = []
    for header_index, line in enumerate(lines):
        header = _split_markdown_row(line)
        if header is None:
            continue
        indexes = _column_indexes(header)
        if indexes is None:
            continue

        rows: list[dict[str, str]] = []
        for row_line in lines[header_index + 1 :]:
            cells = _split_markdown_row(row_line)
            if cells is None:
                break
            if _is_separator_row(cells) or _column_indexes(cells) is not None:
                continue
            row = {
                field: cells[indexes[field]] if indexes[field] < len(cells) else ""
                for field in ASSET_FIELDS
            }
            if any(row.values()):
                rows.append(row)
        candidates.append(rows)
    return candidates


def extract_asset_table_text(
    markdown: str, document_label: str, *, prefer_last: bool = False
) -> list[dict[str, str]]:
    candidates = _extract_candidate_tables(markdown)
    if not candidates:
        partial_fields = _best_partial_header(markdown)
        if len(partial_fields) >= 2:
            missing_fields = [
                field for field in ASSET_FIELDS if field not in partial_fields
            ]
            raise DJ09ExtractionError(
                f"{document_label}资产表缺少字段：{'、'.join(missing_fields)}"
            )
        raise DJ09ExtractionError(
            f"{document_label}中未找到包含{'、'.join(ASSET_FIELDS)}的资产表"
        )
    rows = (
        candidates[-1]
        if prefer_last
        else max(enumerate(candidates), key=lambda item: (len(item[1]), item[0]))[1]
    )
    if not rows:
        raise DJ09ExtractionError(f"{document_label}的资产表中没有资产记录")
    return rows


def extract_dj_09_documents(
    asset_inventory_path: Path, grading_report_path: Path
) -> dict[str, list[dict[str, str]]]:
    for path in (asset_inventory_path, grading_report_path):
        if not path.is_file():
            raise FileNotFoundError(f"输入 Markdown 不存在：{path}")
    asset_inventory = extract_asset_table_text(
        asset_inventory_path.read_text(encoding="utf-8-sig"), "资产清单"
    )
    report_assets = extract_asset_table_text(
        grading_report_path.read_text(encoding="utf-8-sig"),
        "定级报告",
        prefer_last=True,
    )
    return {
        "全量资产清单": asset_inventory,
        "定级报告末尾资产": report_assets,
    }


@register_extractor(
    "DJ-09",
    document_name_contains=("资产清单",),
    additional_document_name_groups=(("定级报告",),),
)
def extract_dj_09(
    paths: Path | tuple[Path, ...],
) -> dict[str, list[dict[str, str]]]:
    if isinstance(paths, Path) or len(paths) != 2:
        raise DJ09ExtractionError("DJ-09 需要同时输入资产清单和定级报告")
    return extract_dj_09_documents(paths[0], paths[1])
