from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import ExtractorNotApplicableError
from .registry import register_extractor


_IMAGE_MARKER_RE = re.compile(r"\[\[\s*IMAGE\b[^\]]*\]\]", re.IGNORECASE)
_PARSE_ERROR_RE = re.compile(r"<!--\s*PARSE_ERROR\b", re.IGNORECASE)
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+")


class FX04NoUsableReportError(ExtractorNotApplicableError):
    """FX-04 找到风险评估报告，但所有副本都解析失败。"""


def _nearest_nonempty_line(lines: list[str], start: int, step: int) -> int | None:
    index = start + step
    while 0 <= index < len(lines):
        if lines[index].strip():
            return index
        index += step
    return None


def extract_fx_04_text(markdown: str, *, source_name: str = "风险评估报告.md") -> dict[str, Any]:
    """提取“拓扑图”文字与其所在标题范围内 IMAGE 标识的客观位置关系。"""
    lines = markdown.splitlines()
    parse_error = _PARSE_ERROR_RE.search(markdown) is not None
    records: list[dict[str, Any]] = []
    associated_image_lines: set[int] = set()

    for index, line in enumerate(lines):
        if "拓扑图" not in line:
            continue

        section_end = next(
            (
                following_index
                for following_index in range(index + 1, len(lines))
                if _MARKDOWN_HEADING_RE.match(lines[following_index].strip())
            ),
            len(lines),
        )
        candidate_indexes: list[int] = list(range(index, section_end))
        previous_index = _nearest_nonempty_line(lines, index, -1)
        if previous_index is not None:
            candidate_indexes.append(previous_index)

        image_lines = sorted(
            candidate_index + 1
            for candidate_index in set(candidate_indexes)
            if _IMAGE_MARKER_RE.search(lines[candidate_index])
        )
        associated_image_lines.update(image_lines)
        records.append(
            {
                "Markdown行号": index + 1,
                "文件": source_name,
                "拓扑图文字": line.strip(),
                "关联IMAGE行号": image_lines,
            }
        )

    return {
        "文件": source_name,
        "解析异常": parse_error,
        "拓扑图文字数量": len(records),
        "关联IMAGE标识数量": len(associated_image_lines),
        "拓扑图证据": records,
    }


def extract_fx_04_documents(primary_path: Path) -> dict[str, Any]:
    primary_path = Path(primary_path)
    if not primary_path.is_file():
        raise FileNotFoundError(f"输入风险评估报告 Markdown 不存在：{primary_path}")

    report_paths = [primary_path]
    results = [
        extract_fx_04_text(
            path.read_text(encoding="utf-8-sig"),
            source_name=path.name,
        )
        for path in report_paths
    ]
    usable_results = [item for item in results if not item["解析异常"]]
    if not usable_results:
        names = "、".join(item["文件"] for item in results)
        raise FX04NoUsableReportError(
            f"风险评估报告全部为显式解析失败存根：{names}"
        )

    evidence = [
        record
        for item in usable_results
        for record in item["拓扑图证据"]
    ]
    associated_images = {
        (record["文件"], line_number)
        for record in evidence
        for line_number in record["关联IMAGE行号"]
    }
    return {
        "风险评估报告文件数": len(results),
        "可稽核报告文件数": len(usable_results),
        "解析异常报告数": len(results) - len(usable_results),
        "拓扑图文字数量": len(evidence),
        "关联IMAGE标识数量": len(associated_images),
        "拓扑图证据": evidence,
        "报告检查结果": results,
    }


@register_extractor("FX-04", document_name_contains=("风险评估报告.md",))
def extract_fx_04(path: Path) -> dict[str, Any]:
    return extract_fx_04_documents(Path(path))
