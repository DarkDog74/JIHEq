from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import ExtractorNotApplicableError
from .registry import register_extractor


_TARGET_TITLE = "残余风险处置"
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s*(?P<title>.+?)\s*#*\s*$")
_NUMBERING_RE = re.compile(r"^\d+(?:\s*[.．]\s*\d+)*\s*[.．、]?\s*")
_TARGET_TEXT_RE = re.compile(r"残\s*余\s*风\s*险\s*处\s*置")
_CHAPTER_NUMBER_RE = re.compile(r"\d+\s*[.．]\s*\d+")
_PARSE_ERROR_RE = re.compile(r"<!--\s*PARSE_ERROR\b", re.IGNORECASE)


class FX19NoUsableReportError(ExtractorNotApplicableError):
    """FX-19 找到风险评估报告，但所有副本都解析失败。"""


def _normalize_title(value: str) -> str:
    candidate = (
        value.strip()
        .replace("**", "")
        .replace("__", "")
        .replace(r"\.", ".")
    )
    candidate = _NUMBERING_RE.sub("", candidate)
    return re.sub(r"\s+", "", candidate).strip("：:")


def _recognize_title(line: str) -> str | None:
    """只识别标题结构，不把目录、链接或正文关键词当成标题。"""
    candidate = line.strip()
    if not candidate:
        return None

    markdown_match = _MARKDOWN_HEADING_RE.fullmatch(candidate)
    if markdown_match and _normalize_title(markdown_match.group("title")) == _TARGET_TITLE:
        return "Markdown标题"

    if "|" not in candidate and _normalize_title(candidate) == _TARGET_TITLE:
        return "独立行标题"

    # PDF 转 Markdown 时，标题可能与上一张表的表头/数据及下一段正文压到同一行。
    # 仅接受同时保留章节编号和目标文字、且不是点线目录项的表格行。
    if (
        candidate.startswith("|")
        and _CHAPTER_NUMBER_RE.search(candidate)
        and _TARGET_TEXT_RE.search(candidate)
        and re.search(r"\.{5,}", candidate) is None
    ):
        return "PDF压平表格标题"
    return None


def extract_fx_19_text(
    markdown: str, *, source_name: str = "风险评估报告.md"
) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        recognition_method = _recognize_title(line)
        if recognition_method is None:
            continue
        evidence.append(
            {
                "文件": source_name,
                "Markdown行号": line_number,
                "标题文本": line.strip(),
                "识别方式": recognition_method,
            }
        )
    return {
        "文件": source_name,
        "解析异常": _PARSE_ERROR_RE.search(markdown) is not None,
        "残余风险处置标题证据": evidence,
    }


def extract_fx_19_documents(primary_path: Path) -> dict[str, Any]:
    primary_path = Path(primary_path)
    if not primary_path.is_file():
        raise FileNotFoundError(f"输入风险评估报告 Markdown 不存在：{primary_path}")

    report_paths = [primary_path]
    results = [
        extract_fx_19_text(
            path.read_text(encoding="utf-8-sig"),
            source_name=path.name,
        )
        for path in report_paths
    ]
    usable_results = [item for item in results if not item["解析异常"]]
    if not usable_results:
        names = "、".join(str(item["文件"]) for item in results)
        raise FX19NoUsableReportError(
            f"风险评估报告全部为显式解析失败存根：{names}"
        )

    evidence = [
        item
        for result in usable_results
        for item in result["残余风险处置标题证据"]
    ]
    return {
        "残余风险处置标题存在": bool(evidence),
        "残余风险处置标题证据": evidence,
        "检查报告数": len(usable_results),
        "解析异常报告数": len(results) - len(usable_results),
    }


@register_extractor("FX-19", document_name_contains=("风险评估报告.md",))
def extract_fx_19(path: Path) -> dict[str, Any]:
    return extract_fx_19_documents(Path(path))
