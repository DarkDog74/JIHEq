from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .registry import register_extractor


_TARGET_TITLE = "业务资产风险分析"
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s*(?P<title>.+?)\s*#*\s*$")
_NUMBERING_RE = re.compile(r"^\d+(?:\s*[.．]\s*\d+)*\s*[.．、]?\s*")
_CHAPTER_NUMBER_RE = re.compile(r"\d+\s*[.．]\s*\d+")
_TARGET_TEXT_RE = re.compile(r"业\s*务\s*资\s*产\s*风\s*险\s*分\s*析")


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
    """识别真实标题结构，并排除目录和普通正文提及。"""
    candidate = line.strip()
    if not candidate:
        return None

    markdown_match = _MARKDOWN_HEADING_RE.fullmatch(candidate)
    if markdown_match and _normalize_title(markdown_match.group("title")) == _TARGET_TITLE:
        return "Markdown标题"

    if "|" not in candidate and _normalize_title(candidate) == _TARGET_TITLE:
        return "独立行标题"

    # PDF 转 Markdown 后，6.3.3 标题可能与上一节表格和本节正文压到一行。
    # 要求同时保留章节编号和目标文字，避免把目录点线或“结果如下”正文当标题。
    if candidate.startswith("|") and _CHAPTER_NUMBER_RE.search(candidate):
        for target in _TARGET_TEXT_RE.finditer(candidate):
            suffix = candidate[target.end() :]
            if (
                re.search(r"\.{5,}", candidate) is None
                and re.match(r"\s*结果", suffix) is None
            ):
                return "PDF压平表格标题"
    return None


def extract_fx_18_text(markdown: str) -> dict[str, Any]:
    evidence: list[dict[str, Any]] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        recognition_method = _recognize_title(line)
        if recognition_method is None:
            continue
        evidence.append(
            {
                "Markdown行号": line_number,
                "标题": line.strip(),
                "识别方式": recognition_method,
            }
        )
    return {"业务资产风险分析标题": evidence}


@register_extractor("FX-18", document_name_contains=("风险评估报告.md",))
def extract_fx_18(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入风险评估报告 Markdown 不存在：{path}")
    return extract_fx_18_text(path.read_text(encoding="utf-8-sig"))
