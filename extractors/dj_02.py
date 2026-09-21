from __future__ import annotations

import re
import html
import unicodedata
from fractions import Fraction
from pathlib import Path
from typing import Any

from .errors import ExtractorNotApplicableError
from .registry import register_extractor


class DJ02ExtractionError(ValueError):
    """DJ-02 所需字段无法可靠提取。"""


class DJ02NotApplicableError(DJ02ExtractionError, ExtractorNotApplicableError):
    """输入文档不包含 DJ-02 需要的定级报告章节。"""


_HEADING_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
_TARGET_TITLE_RE = re.compile(r"^1\s*[.．、]\s*赋值理由$")
_EMPTY_HTML_ANCHOR_RE = re.compile(r"<a\b[^>]*>\s*</a>", re.IGNORECASE)
_NUMBER_MAP = {
    "１": 1,
    "２": 2,
    "３": 3,
    "４": 4,
    "５": 5,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
}
_FIELD_PATTERNS = {
    "社会影响力赋值": re.compile(
        r"社会影响力\s*(?:的)?\s*赋值\s*(?:应|建议|取最高值)?\s*为?\s*(?P<value>[1-5１-５一二三四五])"
    ),
    "规模和服务范围赋值": re.compile(
        r"规模和服务范围\s*(?:的)?\s*赋值\s*(?:应|建议|取最高值)?\s*为?\s*(?P<value>[1-5１-５一二三四五])"
    ),
    "提供服务的重要性赋值": re.compile(
        r"提供服务\s*(?:的)?\s*重要性\s*赋值\s*(?:应|建议|取最高值)?\s*为?\s*(?P<value>[1-5１-５一二三四五])"
    ),
}
_FIELD_HEADING_LABELS = {
    "社会影响力赋值": "社会影响力赋值",
    "规模和服务范围赋值": "规模和服务范围赋值",
    "提供服务的重要性赋值": "提供服务的重要性赋值",
}
_FIELD_HEADING_ALIASES = {
    "提供服务的重要性赋值": ("所提供服务重要性赋值",),
}
_GENERIC_VALUE_RE = re.compile(
    r"赋值\s*(?:应|建议|取最高值)?\s*为?\s*(?P<value>[1-5１-５一二三四五])"
)
# 只接受测试集中出现过的完整章节标题。匹配时会忽略章节编号、空格和
# Markdown 强调标记，但不会使用关键词子串匹配。
_METHOD_SECTION_TITLES = frozenset(
    {
        "采用定级方法及选择权重理由",
        "采用的定级方法及选择权重的理由",
        "安全等级的计算方法",
    }
)
_DECLARED_LEVEL_RE = re.compile(
    r"安全(?:保护)?等级\s*(?:确定)?\s*(?:为|是|：|:)\s*第?\s*"
    r"(?P<value>[1-5１-５一二三四五])\s*级"
)


def _strip_outer_emphasis(value: str) -> str:
    value = value.strip().replace(r"\.", ".")
    return value.replace("**", "").replace("__", "").strip()


def _parse_heading(line: str) -> tuple[int, str] | None:
    candidate = line.strip()
    if candidate.startswith(("**#", "__#")):
        candidate = candidate[2:]
    if candidate.endswith(("**", "__")):
        candidate = candidate[:-2].rstrip()

    match = _HEADING_RE.match(candidate)
    if match is None:
        return None
    return len(match.group("marks")), _strip_outer_emphasis(match.group("title"))


def _normalize_heading_label(title: str) -> str:
    candidate = re.sub(r"\s+", "", _strip_outer_emphasis(title))
    return re.sub(r"^\d+[.．、]?", "", candidate)


def _is_method_section_heading(title: str) -> bool:
    return _normalize_heading_label(title) in _METHOD_SECTION_TITLES


def _extract_assignment_section(markdown: str) -> str:
    lines = markdown.splitlines()
    candidates: list[tuple[int, int]] = []

    for index, line in enumerate(lines):
        heading = _parse_heading(line)
        if heading is not None and heading[0] >= 2 and _TARGET_TITLE_RE.fullmatch(heading[1]):
            candidates.append((index, heading[0]))

    if not candidates:
        # 少数旧模板没有“1. 赋值理由”总标题，只保留三个编号字段小标题。
        # 该兼容分支要求三个小标题全部且各自只出现一次，不能仅凭正文关键词进入。
        field_heading_indexes: dict[str, list[int]] = {
            label: _field_heading_indexes(lines, label)
            for label in _FIELD_HEADING_LABELS.values()
        }
        if not all(len(indexes) == 1 for indexes in field_heading_indexes.values()):
            raise DJ02NotApplicableError("未找到完整的“1. 赋值理由”章节标题")
        start_index = min(indexes[0] for indexes in field_heading_indexes.values())
    else:
        if len(candidates) > 1:
            locations = "、".join(str(index + 1) for index, _ in candidates)
            raise DJ02ExtractionError(f"发现多个“1. 赋值理由”章节，行号：{locations}")
        start_index, _target_level = candidates[0]

    method_candidates: list[int] = []
    for index in range(start_index + 1, len(lines)):
        heading = _parse_heading(lines[index])
        if heading is not None and _is_method_section_heading(heading[1]):
            method_candidates.append(index)

    if not method_candidates:
        raise DJ02ExtractionError(
            "“1. 赋值理由”之后未找到完整的定级方法/安全等级计算方法章节标题"
        )
    if len(method_candidates) > 1:
        locations = "、".join(str(index + 1) for index in method_candidates)
        raise DJ02ExtractionError(
            f"“1. 赋值理由”之后发现多个定级方法/安全等级计算方法章节，行号：{locations}"
        )

    end_index = method_candidates[0]
    content_start = start_index + 1 if candidates else start_index
    return "\n".join(lines[content_start:end_index])


def _normalize_number(value: str) -> int:
    return _NUMBER_MAP.get(value, int(value) if value.isascii() else 0)


def _normalized_field_heading(line: str) -> str:
    candidate = (
        line.strip()
        .replace(r"\)", ")")
        .replace("**", "")
        .replace("__", "")
    )
    candidate = re.sub(r"^#{1,6}\s+", "", candidate)
    candidate = re.sub(r"^[-*+]\s+", "", candidate)
    candidate = re.sub(r"^\d+\s*[)）.．、]\s*", "", candidate)
    candidate = _EMPTY_HTML_ANCHOR_RE.sub("", candidate).strip()
    return _strip_outer_emphasis(candidate)


def _is_field_heading(line: str, label: str) -> bool:
    candidate = _normalized_field_heading(line)
    return candidate == label or candidate in _FIELD_HEADING_ALIASES.get(label, ())


def _field_heading_indexes(lines: list[str], label: str) -> list[int]:
    """标准标题优先；只有标准标题不存在时才尝试同义旧模板标题。"""
    primary = [
        index
        for index, line in enumerate(lines)
        if _normalized_field_heading(line) == label
    ]
    if primary:
        return primary
    aliases = _FIELD_HEADING_ALIASES.get(label, ())
    return [
        index
        for index, line in enumerate(lines)
        if _normalized_field_heading(line) in aliases
    ]


def _subsection_for_field(section: str, label: str) -> str | None:
    lines = section.splitlines()
    starts = _field_heading_indexes(lines, label)
    if len(starts) != 1:
        return None
    start = starts[0]
    end = len(lines)
    all_labels = tuple(_FIELD_HEADING_LABELS.values())
    for index in range(start + 1, len(lines)):
        if any(_is_field_heading(lines[index], other) for other in all_labels):
            end = index
            break
        heading = _parse_heading(lines[index])
        if heading is not None and _is_method_section_heading(heading[1]):
            end = index
            break
    return "\n".join(lines[start + 1 : end])


def _values_for_field(section: str, field_name: str, pattern: re.Pattern[str]) -> set[int]:
    values = {_normalize_number(match.group("value")) for match in pattern.finditer(section)}
    if values:
        return values

    subsection = _subsection_for_field(section, _FIELD_HEADING_LABELS[field_name])
    if subsection is None:
        return set()
    return {
        _normalize_number(match.group("value"))
        for match in _GENERIC_VALUE_RE.finditer(subsection)
    }


def _extract_method_section(markdown: str) -> str | None:
    lines = markdown.splitlines()
    candidates: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        heading = _parse_heading(line)
        if heading is not None and _is_method_section_heading(heading[1]):
            candidates.append((index, heading[0]))

    if not candidates:
        return None
    if len(candidates) > 1:
        locations = "、".join(str(index + 1) for index, _ in candidates)
        raise DJ02ExtractionError(
            f"发现多个定级方法/安全等级计算方法章节，行号：{locations}"
        )

    start_index, _target_level = candidates[0]
    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        heading = _parse_heading(lines[index])
        # 部分由 DOC/DOCX 转出的 Markdown 标题层级不稳定，例如目标章节
        # 是一级标题，而紧随其后的“3、安全保护等级”却是二级标题。
        # 因此这里以“下一个标题”为章节边界，避免误取后续等级结论。
        if heading is not None:
            end_index = index
            break
    return "\n".join(lines[start_index + 1 : end_index])


def _extract_declared_level(markdown: str) -> int | None:
    method_section = _extract_method_section(markdown)
    if method_section is None:
        return None
    plain = (
        method_section.replace("**", "")
        .replace("__", "")
        .replace("*", "")
        .replace("`", "")
    )
    matches = list(_DECLARED_LEVEL_RE.finditer(plain))
    if not matches:
        return None
    # 声明等级只取定级方法/安全等级计算方法章节中的结论。
    return _normalize_number(matches[-1].group("value"))


def extract_dj_02_text(markdown: str) -> dict[str, int | None]:
    """保留三要素和等级的原有接口，供 DJ-01 等稽核项独立复用。"""
    section = _extract_assignment_section(markdown)
    result: dict[str, int] = {}
    missing_fields: list[str] = []

    for field_name, pattern in _FIELD_PATTERNS.items():
        values = _values_for_field(section, field_name, pattern)
        if not values:
            missing_fields.append(field_name)
            continue
        if len(values) > 1:
            raise DJ02ExtractionError(
                f"字段“{field_name}”在赋值理由章节中出现冲突值：{sorted(values)}"
            )
        result[field_name] = values.pop()

    if missing_fields:
        raise DJ02ExtractionError("赋值理由章节缺少字段：" + "、".join(missing_fields))
    result["声明安全等级"] = _extract_declared_level(markdown)
    return result


class DJ02CalculationExtractionError(DJ02ExtractionError, ExtractorNotApplicableError):
    """计算章节中的字段冲突或格式异常，不作为程序异常。"""


_NUMERIC_LITERAL = r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)"
_WEIGHT_LITERAL = rf"{_NUMERIC_LITERAL}(?:/{_NUMERIC_LITERAL})?"
_WEIGHT_ASSIGNMENT_RE = re.compile(
    rf"(?<![A-Za-z0-9_αβγ])(?P<symbols>[αβγ](?:=[αβγ])*)=(?P<value>{_WEIGHT_LITERAL})(?![\dA-Za-z_./%+*^=×÷-])"
)
_WEIGHT_LIST_RE = re.compile(
    rf"α[、,]β[、,]γ分别表示[^。；\n]*?权重[，,]分别为"
    rf"(?P<alpha>{_WEIGHT_LITERAL})[、,](?P<beta>{_WEIGHT_LITERAL})[、,]"
    rf"(?P<gamma>{_WEIGHT_LITERAL})(?![\dA-Za-z_./%+*^=×÷-])"
)
_DECLARED_K_VALUE_RE = re.compile(
    rf"(?<![A-Za-z0-9_αβγ])k=(?P<value>{_NUMERIC_LITERAL})(?![\dA-Za-z_./%+*^=×÷-])", re.IGNORECASE
)


def _calculation_plain_text(section: str) -> str:
    # 图片元数据、注释和链接目标不属于正文中的数值证据。
    text = re.sub(r"<!--.*?-->|\[\[IMAGE\b.*?\]\]", "", section, flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^\n]*?\)", "", text)
    text = re.sub(r"\[([^\]]*)\]\([^\n]*?\)", r"\1", text)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = unicodedata.normalize("NFKC", html.unescape(text))
    text = text.replace("**", "").replace("__", "").replace("`", "")
    # 仅去除成对的强调标记，不删除 1.4*0 里的乘号。
    text = re.sub(r"(?<![A-Za-z0-9])\*([^*\n]+)\*(?![A-Za-z0-9])", r"\1", text)
    text = text.replace(r"\=", "=").replace(r"\.", ".").replace("−", "-")
    # 去除横向空白，但保留段落边界，不跨段拼接任意数值。
    return re.sub(r"[^\S\n]+", "", text)


def extract_dj_02_calculation_text(markdown: str) -> dict[str, Any]:
    """完整 DJ-02 输入：原有四字段 + 计算章节的实际权重及声明 k。"""
    fields: dict[str, Any] = extract_dj_02_text(markdown)
    section = _extract_method_section(markdown) or ""
    # DOC 转换有时将下一节保留为普通编号标题，仅收紧新增计算字段范围。
    section_lines = section.splitlines()
    for index, line in enumerate(section_lines):
        title = _strip_outer_emphasis(line)
        if re.fullmatch(r"(?:[-+]\s*)?(?:\d+|[一二三四五六七八九十]+)[.．、)）]\s*.*安全(?:保护)?等级[：:]?", title):
            section = "\n".join(section_lines[:index])
            break
    plain = _calculation_plain_text(section)
    values: dict[str, list[str]] = {symbol: [] for symbol in "αβγ"}
    for match in _WEIGHT_ASSIGNMENT_RE.finditer(plain):
        for symbol in match.group("symbols").split("="):
            values[symbol].append(match.group("value"))
    for match in _WEIGHT_LIST_RE.finditer(plain):
        for symbol, group in zip("αβγ", ("alpha", "beta", "gamma")):
            values[symbol].append(match.group(group))

    for symbol, literals in values.items():
        try:
            numbers = {Fraction(value) for value in literals}
        except (ValueError, ZeroDivisionError) as error:
            raise DJ02CalculationExtractionError(f"权重{symbol}数值格式无效") from error
        if len(numbers) > 1:
            raise DJ02CalculationExtractionError(f"计算章节中权重{symbol}存在冲突值：{literals}")
        try:
            fields[f"权重{symbol}"] = float(numbers.pop()) if numbers else None
        except OverflowError as error:
            raise DJ02CalculationExtractionError(f"权重{symbol}数值超出范围") from error

    declared_k = {Fraction(match.group("value")) for match in _DECLARED_K_VALUE_RE.finditer(plain)}
    if len(declared_k) > 1:
        raise DJ02CalculationExtractionError("计算章节中声明k值存在冲突值")
    try:
        fields["声明k值"] = float(declared_k.pop()) if declared_k else None
    except OverflowError as error:
        raise DJ02CalculationExtractionError("声明k值数值超出范围") from error
    fields["计算章节原文"] = section.strip()
    return fields


@register_extractor("DJ-02", document_name_contains=("定级报告",))
def extract_dj_02(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入 Markdown 不存在：{path}")
    return extract_dj_02_calculation_text(path.read_text(encoding="utf-8-sig"))
