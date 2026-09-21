from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .errors import TargetSectionNotFoundError
from .common import normalize_heading_title, parse_markdown_heading, stage_audit_artifacts
from .registry import register_extractor


class FX11ExtractionError(ValueError):
    """FX-11 脆弱性识别章节无法可靠提取。"""


_NUMBER_RE = re.compile(r"^(\d+(?:[.．]\d+)*)(?:[.．、]|\s|$)")
_TOC_RE = re.compile(r"\.{4,}|\[[^\]]+\]\(#[^)]*\)")
_MAX_SECTION_CHARS = 30000


def _number(title: str) -> tuple[str, ...]:
    match = _NUMBER_RE.match(title.strip())
    return tuple(re.split(r"[.．]", match[1])) if match else ()


def _slice_chapter(lines: list[str], start: int, level: int, title: str) -> str:
    """优先依据编号区分章/节，兼容解析后父子标题均为同一 # 层级。"""
    number = _number(title)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        heading = parse_markdown_heading(lines[index])
        if heading is None:
            continue
        other = _number(heading[1])
        if heading[1].lstrip().startswith("附录"):
            end = index
            break
        if number and other:
            if len(other) > len(number) and other[:len(number)] == number:
                continue
            end = index
            break
        if heading[0] <= level:
            end = index
            break
    return "\n".join(lines[start + 1:end]).strip()


def _extract_section_text(markdown: str) -> tuple[str, str]:
    lines = markdown.splitlines()
    # 优先完整章节；只有概述标题时兼容该独立小节，不限定 5.1。
    for target in ("脆弱性识别", "脆弱性识别概述"):
        for index, line in enumerate(lines):
            if _TOC_RE.search(line):
                continue
            heading = parse_markdown_heading(line)
            if heading is None:
                continue
            if normalize_heading_title(heading[1]) != target:
                continue
            section = _slice_chapter(lines, index, *heading)
            if section:
                return _checked_section(section), "标题"

    # PDF 把标题压入单元格时，从带编号的目标标题截取，不能从正文提及处起取。
    anchor_re = re.compile(r"(?<![\w.])(\d+(?:\.\d+)*\.?)[ \t]*脆弱性识别(?:概述)?(?=\s|[|：:])")
    for index, line in enumerate(lines):
        if _TOC_RE.search(line):
            continue
        match = anchor_re.search(line)
        if match is None:
            continue
        title = match[0].strip()
        section = line[match.end():].strip() + "\n" + _slice_chapter(lines, index, 1, title)
        # 同一单元格中的下一个章标题也必须截断。
        number = _number(title)
        if number:
            next_chapter = int(number[0]) + 1
            section = re.split(rf"(?<![\w.]){next_chapter}[.．、]\s*(?=[\u4e00-\u9fff])|附录\s*[A-Z]\s", section, maxsplit=1)[0]
        if section.strip(" \n|-"):
            return _checked_section(section.strip()), "关键词"
    raise TargetSectionNotFoundError("未找到有效的“脆弱性识别”章节正文")


def _checked_section(section: str) -> str:
    if len(section) > _MAX_SECTION_CHARS:
        raise FX11ExtractionError("脆弱性识别章节超过长度上限，需核对解析边界；不截断后判定")
    return section


def extract_fx_11_text(markdown: str) -> dict[str, Any]:
    section, method = _extract_section_text(markdown)
    return {"脆弱性识别章节文本": section, "章节定位方式": method}


@register_extractor("FX-11", document_name_contains=("风险评估报告",))
def extract_fx_11(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入风险评估报告不存在：{path}")
    fields = extract_fx_11_text(path.read_text(encoding="utf-8-sig"))
    stage_audit_artifacts("FX-11", path, fields)
    return fields
