from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .dj_04 import _is_separator_row, _split_markdown_row
from .registry import register_extractor


class ZIC01ExtractionError(ValueError):
    """ZIC-01 的资产清单无法可靠提取。"""


_PLACEHOLDER_RE = re.compile(
    r"(?i)(?<![A-Z0-9])X{2,}(?![A-Z0-9])|待定|待补充|N\s*/\s*A"
)


def extract_zic_01_text(markdown: str) -> dict[str, Any]:
    lines = markdown.splitlines()
    headers: list[str] | None = None
    row_count = 0
    findings: list[dict[str, object]] = []

    for markdown_line, line in enumerate(lines, start=1):
        cells = _split_markdown_row(line)
        if cells is None:
            headers = None
            continue
        if _is_separator_row(cells):
            continue
        if headers is None:
            headers = cells
            continue

        row_count += 1
        for index, value in enumerate(cells):
            matches = _PLACEHOLDER_RE.findall(value)
            if not matches:
                continue
            field = headers[index] if index < len(headers) and headers[index] else f"第{index + 1}列"
            findings.append(
                {
                    "资产清单行号": row_count,
                    "Markdown行号": markdown_line,
                    "字段": field,
                    "值": value,
                    "命中占位符": sorted({match.upper() for match in matches}),
                }
            )

    if row_count == 0:
        raise ZIC01ExtractionError("资产清单中未找到数据表格")
    return {
        "资产清单行数": row_count,
        "模糊占位符": findings,
    }


@register_extractor("ZIC-01", document_name_contains=("资产清单",))
def extract_zic_01(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入 Markdown 不存在：{path}")
    return extract_zic_01_text(path.read_text(encoding="utf-8-sig"))
