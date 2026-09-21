from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import ExtractorNotApplicableError
from .registry import register_extractor


_TARGET_TITLE = "系统资产风险分析"
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s*(?P<title>.+?)\s*#*\s*$")
_NUMBERING_RE = re.compile(r"^\d+(?:\s*[.．]\s*\d+)*\s*[.．、]?\s*")
_TARGET_TEXT_RE = re.compile(r"系\s*统\s*资\s*产\s*风\s*险\s*分\s*析")
_CHAPTER_NUMBER_RE = re.compile(r"\d+\s*[.．]\s*\d+")
_TOC_LEADER_RE = re.compile(r"(?:\.{4,}|…{2,}|·{4,})")
_TOC_LINK_RE = re.compile(r"^\[(?P<title>.+?)\]\([^)]+\)\s*$")
_PARSE_ERROR_RE = re.compile(r"<!--\s*PARSE_ERROR\b", re.IGNORECASE)


class FX17NoUsableReportError(ExtractorNotApplicableError):
    """FX-17 找到风险评估报告，但所有副本都解析失败。"""


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
    """识别正文标题；正文标题丢失时，接受严格的编号目录项作为结构证据。"""
    candidate = line.strip()
    if not candidate:
        return None

    markdown_match = _MARKDOWN_HEADING_RE.fullmatch(candidate)
    if markdown_match and _normalize_title(markdown_match.group("title")) == _TARGET_TITLE:
        return "Markdown标题"

    toc_link = _TOC_LINK_RE.fullmatch(candidate)
    if toc_link is not None:
        link_title = re.sub(r"\s+\d+\s*$", "", toc_link.group("title"))
        if _CHAPTER_NUMBER_RE.search(link_title) and _normalize_title(link_title) == _TARGET_TITLE:
            return "目录标题项"

    toc_leader = _TOC_LEADER_RE.search(candidate)
    if toc_leader is not None:
        toc_title = candidate[: toc_leader.start()].strip()
        if _CHAPTER_NUMBER_RE.search(toc_title) and _normalize_title(toc_title) == _TARGET_TITLE:
            return "目录标题项"

    if "|" not in candidate and _normalize_title(candidate) == _TARGET_TITLE:
        return "独立行标题"

    # PDF 转 Markdown 时，标题可能与表头、系统名和本节正文压到同一行。
    # 要求保留章节编号，并排除目录点线以及“对应的…结果如下”正文句。
    if candidate.startswith("|") and _CHAPTER_NUMBER_RE.search(candidate):
        for target in _TARGET_TEXT_RE.finditer(candidate):
            prefix = candidate[: target.start()]
            suffix = candidate[target.end() :]
            if (
                _TOC_LEADER_RE.search(suffix) is None
                and re.search(r"对应的\s*$", prefix) is None
                and re.match(r"\s*结果", suffix) is None
            ):
                return "PDF压平表格标题"
    return None


def extract_fx_17_text(
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
        "系统资产风险分析标题证据": evidence,
    }


def extract_fx_17_documents(primary_path: Path) -> dict[str, Any]:
    primary_path = Path(primary_path)
    if not primary_path.is_file():
        raise FileNotFoundError(f"输入风险评估报告 Markdown 不存在：{primary_path}")

    report_paths = [primary_path]
    results = [
        extract_fx_17_text(
            path.read_text(encoding="utf-8-sig"),
            source_name=path.name,
        )
        for path in report_paths
    ]
    usable_results = [item for item in results if not item["解析异常"]]
    if not usable_results:
        names = "、".join(str(item["文件"]) for item in results)
        raise FX17NoUsableReportError(
            f"风险评估报告全部为显式解析失败存根：{names}"
        )

    evidence = [
        item
        for result in usable_results
        for item in result["系统资产风险分析标题证据"]
    ]
    return {
        "系统资产风险分析标题存在": bool(evidence),
        "系统资产风险分析标题证据": evidence,
        "检查报告数": len(usable_results),
        "解析异常报告数": len(results) - len(usable_results),
    }


@register_extractor("FX-17", document_name_contains=("风险评估报告.md",))
def extract_fx_17(path: Path) -> dict[str, Any]:
    return extract_fx_17_documents(Path(path))
