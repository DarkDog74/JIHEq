from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from .common import stage_audit_artifacts
from .dj_02 import (
    DJ02ExtractionError,
    DJ02NotApplicableError,
    _extract_assignment_section,
    _FIELD_HEADING_LABELS,
    _subsection_for_field,
    extract_dj_02_text,
)
from .errors import ExtractorNotApplicableError
from .fhx_03 import FHX03ExtractionError, extract_fhx_03_text
from .registry import register_extractor


class DJ01ExtractionError(ValueError):
    """DJ-01 三要素赋值理由或细化指标章节无法可靠提取。"""


class DJ01NotApplicableError(DJ01ExtractionError, ExtractorNotApplicableError):
    """输入文档不包含 DJ-01 需要的赋值理由章节。"""


_ELEMENTS = (
    ("社会影响力", "社会影响力赋值"),
    ("规模和服务范围", "规模和服务范围赋值"),
    ("提供服务的重要性", "提供服务的重要性赋值"),
)

_REFERENCE_ROOT = Path(__file__).resolve().parents[1] / "Reference_documents"
_TAXONOMY_PATH = _REFERENCE_ROOT / "系统类型划分.json"
_STANDARD_PATH = _REFERENCE_ROOT / "网络和系统定级要素赋值细化指标.md"
_GENERIC_SECTION_TITLE = "其他业务系统"
_LEVEL2_HEADING_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_LEVEL1_HEADING_RE = re.compile(r"^#\s+")


def _normalize_type_text(value: str) -> str:
    """类型与标题的统一排版：全角括号、各类破折号、其它→其他、空白。"""
    text = str(value).replace("（", "(").replace("）", ")")
    text = re.sub(r"[－—–\-]", "-", text)
    text = text.replace("其它", "其他")
    return re.sub(r"\s+", "", text)


@lru_cache(maxsize=1)
def _load_type_candidates() -> dict[str, str]:
    """系统类型划分.json 的候选名（归一化）→ 网络类型；类型名优先于同名单元。"""
    if not _TAXONOMY_PATH.is_file():
        raise DJ01ExtractionError(f"参考标准缺失：{_TAXONOMY_PATH}")
    try:
        payload = json.loads(_TAXONOMY_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise DJ01ExtractionError(f"系统类型划分 JSON 格式错误：{error}") from error
    candidates: dict[str, str] = {}
    for network_type in payload.get("types") or []:
        normalized = _normalize_type_text(network_type)
        if normalized:
            candidates[normalized] = network_type
    for unit, network_type in (payload.get("单元到类型") or {}).items():
        normalized = _normalize_type_text(unit)
        if normalized:
            candidates.setdefault(normalized, network_type)
    if not candidates:
        raise DJ01ExtractionError("系统类型划分 JSON 中没有可用的类型候选")
    return candidates


def _match_network_type(value: str) -> tuple[str, str] | None:
    """在类型候选中唯一定位 value 所属网络类型；返回 (网络类型, 命中说明)。

    规则：归一化后精确命中优先；否则取双向包含中最长候选，且最长候选必须
    只指向一个网络类型，否则视为歧义不命中。
    """
    candidates = _load_type_candidates()
    normalized = _normalize_type_text(value)
    if len(normalized) < 2:
        return None
    exact = candidates.get(normalized)
    if exact is not None:
        return exact, f"精确命中[{value.strip()}]"
    hits = [
        (name, network_type)
        for name, network_type in candidates.items()
        if len(name) >= 2 and (name in normalized or normalized in name)
    ]
    if not hits:
        return None
    hits.sort(key=lambda item: len(item[0]), reverse=True)
    longest_types = {
        network_type for name, network_type in hits if len(name) == len(hits[0][0])
    }
    if len(longest_types) != 1:
        return None
    name, network_type = hits[0]
    return network_type, f"包含命中（“{value.strip()}”含“{name}”）"


@lru_cache(maxsize=1)
def _load_standard_sections() -> tuple[dict[str, str], ...]:
    """细化指标按二级标题切节；每节含编号、标题、归一标题和完整原文。"""
    if not _STANDARD_PATH.is_file():
        raise DJ01ExtractionError(f"参考标准缺失：{_STANDARD_PATH}")
    lines = _STANDARD_PATH.read_text(encoding="utf-8-sig").splitlines()
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _LEVEL2_HEADING_RE.match(line)
        if match is not None:
            starts.append((index, match.group("title")))

    sections: list[dict[str, str]] = []
    for position, (start, raw_title) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        body: list[str] = [f"## {raw_title}"]
        for line in lines[start + 1 : end]:
            if _LEVEL1_HEADING_RE.match(line):
                break
            body.append(line)
        number_match = re.match(r"^(\d+(?:\.\d+)*)\s*(.*)$", raw_title)
        number = number_match.group(1) if number_match else ""
        title = number_match.group(2) if number_match else raw_title
        sections.append(
            {
                "编号": number,
                "标题": title,
                "归一标题": _normalize_type_text(title),
                "原文": "\n".join(body).rstrip() + "\n",
            }
        )
    if not sections:
        raise DJ01ExtractionError("细化指标中未找到任何二级章节")
    return tuple(sections)


def _find_section_for_type(network_type: str) -> dict[str, str] | None:
    sections = _load_standard_sections()
    normalized = _normalize_type_text(network_type)
    for section in sections:
        if section["归一标题"] == normalized:
            return section
    for section in sections:
        title = section["归一标题"]
        if len(title) >= 2 and (normalized in title or title in normalized):
            return section
    return None


def _find_generic_section() -> dict[str, str]:
    for section in _load_standard_sections():
        if _GENERIC_SECTION_TITLE in section["归一标题"]:
            return section
    raise DJ01ExtractionError("细化指标中未找到“其他业务系统”通用赋值章节")


def _unmatched_sections(basis: str) -> dict[str, Any]:
    return {
        "匹配状态": "未匹配专用章节",
        "匹配依据": basis,
        "专用章节号": None,
        "专用章节标题": None,
        "专用章节原文": None,
        "通用章节原文": _find_generic_section()["原文"],
    }


def _locate_standard_sections(
    *, network_type_keyword: str, assessment_title: str
) -> dict[str, Any]:
    """拿评测表标题横线后的网络类型词，在《系统类型划分》中定位网络类型，
    再切出细化指标对应二级章节原文，并附带“其他业务系统”通用赋值表。

    全部为通用算法（精确/最长包含匹配），不使用系统名白名单或固定行号。
    """
    matched = _match_network_type(network_type_keyword)
    if matched is None:
        return _unmatched_sections(
            "符合性评测表标题“"
            f"{assessment_title}”的网络类型“{network_type_keyword}”"
            "未在《系统类型划分》中唯一定位"
        )
    network_type, note = matched
    section = _find_section_for_type(network_type)
    if section is None:
        return _unmatched_sections(
            f"评测表网络类型“{network_type_keyword}”{note} → {network_type}，"
            "但细化指标中未找到对应二级章节"
        )
    return {
        "匹配状态": "评测表标题命中",
        "匹配依据": (
            f"评测表标题“{assessment_title}”横线后取词[{network_type_keyword}]"
            f"{note} → {network_type}"
        ),
        "专用章节号": section["编号"],
        "专用章节标题": section["标题"],
        "专用章节原文": section["原文"],
        "通用章节原文": _find_generic_section()["原文"],
    }


def _extract_assessment_title(assessment_markdown: str) -> dict[str, Any]:
    """复用 FHX-03 的标题提取逻辑，取“…符合性评测表－<网络类型>”横线后关键词。"""
    try:
        return extract_fhx_03_text(assessment_markdown)
    except FHX03ExtractionError as error:
        raise DJ01ExtractionError(f"符合性评测表标题无法提取：{error}") from error


def _reason_paragraphs(markdown: str) -> dict[str, str]:
    try:
        section = _extract_assignment_section(markdown)
    except DJ02NotApplicableError as error:
        raise DJ01NotApplicableError(str(error)) from error
    except DJ02ExtractionError as error:
        raise DJ01ExtractionError(str(error)) from error

    paragraphs: dict[str, str] = {}
    missing: list[str] = []
    for element, label in _ELEMENTS:
        subsection = _subsection_for_field(section, label)
        if subsection is None or not subsection.strip():
            missing.append(label)
            continue
        paragraphs[element] = subsection.strip()
    if missing:
        raise DJ01ExtractionError(
            "赋值理由章节缺少字段小节：" + "、".join(missing)
        )
    return paragraphs


def extract_dj_01_data(
    report_markdown: str, assessment_markdown: str
) -> dict[str, Any]:
    try:
        values = extract_dj_02_text(report_markdown)
    except DJ02NotApplicableError as error:
        raise DJ01NotApplicableError(str(error)) from error
    except DJ02ExtractionError as error:
        raise DJ01ExtractionError(str(error)) from error

    paragraphs = _reason_paragraphs(report_markdown)
    assignments = []
    for element, label in _ELEMENTS:
        assignments.append(
            {
                "要素": element,
                "理由原文": paragraphs[element],
                "报告赋值": values[label],
            }
        )

    assessment = _extract_assessment_title(assessment_markdown)
    network_type_keyword = str(assessment["网络类型"])
    return {
        "赋值理由": assignments,
        "评测表标题": str(assessment["评测表标题"]),
        "评测表网络类型": network_type_keyword,
        "细化指标章节": _locate_standard_sections(
            network_type_keyword=network_type_keyword,
            assessment_title=str(assessment["评测表标题"]),
        ),
    }


@register_extractor(
    "DJ-01",
    document_name_contains=("定级报告",),
    additional_document_name_groups=(("符合性评测表.md",),),
)
def extract_dj_01(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(source, tuple) or len(source) != 2:
        raise DJ01ExtractionError("DJ-01 需要定级报告 Markdown 和符合性评测表 Markdown")
    report_path, assessment_path = source
    if not report_path.is_file():
        raise FileNotFoundError(f"输入定级报告不存在：{report_path}")
    if not assessment_path.is_file():
        raise FileNotFoundError(f"输入符合性评测表不存在：{assessment_path}")

    fields = extract_dj_01_data(
        report_path.read_text(encoding="utf-8-sig"),
        assessment_path.read_text(encoding="utf-8-sig"),
    )
    stage_audit_artifacts("DJ-01", source, fields)
    return fields
