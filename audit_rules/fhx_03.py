from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .registry import register_rule


_CATALOG_PATH = Path(__file__).parent.parent / "reference_data" / "fhx_03_network_types.json"
_HIERARCHY_SEPARATORS = frozenset("/／:：>＞-－—–")


def _normalize_network_type(value: str) -> str:
    return (
        re.sub(r"\s+", "", value)
        .replace("—", "－")
        .replace("-", "－")
        .replace("(", "（")
        .replace(")", "）")
        .casefold()
    )


def _load_type_catalog() -> tuple[set[str], dict[str, set[str]], dict[str, str]]:
    try:
        payload = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        values = payload["network_types"]
        relations = payload.get("parent_child_relations", [])
        aliases = payload.get("comparison_aliases", {})
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise ValueError(f"无法读取符合性评测表网络类型标准：{error}") from error
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("符合性评测表网络类型标准格式无效")
    standard_types = {_normalize_network_type(value) for value in values}
    if not isinstance(relations, list):
        raise ValueError("网络类型父子关系格式无效")
    children_by_parent: dict[str, set[str]] = {}
    for index, relation in enumerate(relations, start=1):
        if (
            not isinstance(relation, list)
            or len(relation) != 2
            or not all(isinstance(value, str) for value in relation)
        ):
            raise ValueError(f"网络类型父子关系第{index}项格式无效")
        parent, child = map(_normalize_network_type, relation)
        if parent not in standard_types or child not in standard_types:
            raise ValueError(f"网络类型父子关系第{index}项引用了标准表外类型")
        children_by_parent.setdefault(parent, set()).add(child)
    if not isinstance(aliases, dict) or not all(
        isinstance(source, str) and isinstance(target, str)
        for source, target in aliases.items()
    ):
        raise ValueError("网络类型比较别名格式无效")
    normalized_aliases = {
        _normalize_network_type(source): _normalize_network_type(target)
        for source, target in aliases.items()
    }
    if any(target not in standard_types for target in normalized_aliases.values()):
        raise ValueError("网络类型比较别名指向了标准表外类型")
    return standard_types, children_by_parent, normalized_aliases


def _load_standard_types() -> set[str]:
    return _load_type_catalog()[0]


def _contains_hierarchy_segment(value: str, candidate: str) -> bool:
    start = 0
    while True:
        position = value.find(candidate, start)
        if position < 0:
            return False
        before_ok = position == 0 or value[position - 1] in _HIERARCHY_SEPARATORS
        end = position + len(candidate)
        after_ok = end == len(value) or value[end] in _HIERARCHY_SEPARATORS
        if before_ok and after_ok:
            return True
        start = position + 1


def _resolve_catalog_types(
    value: str, standard_types: set[str], aliases: Mapping[str, str]
) -> set[str]:
    normalized = _normalize_network_type(value)
    candidates = {
        candidate
        for candidate in standard_types
        if _contains_hierarchy_segment(normalized, candidate)
    }
    alias = aliases.get(normalized)
    if alias is not None:
        candidates.add(alias)
    return candidates


def _is_ancestor(
    ancestor: str, descendant: str, children_by_parent: Mapping[str, set[str]]
) -> bool:
    pending = list(children_by_parent.get(ancestor, ()))
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == descendant:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(children_by_parent.get(current, ()))
    return False


def _match_filing_type(
    assessment_type: str,
    filing_type: str,
    standard_types: set[str],
    children_by_parent: Mapping[str, set[str]],
    aliases: Mapping[str, str],
) -> tuple[bool, str | None]:
    assessment = _normalize_network_type(assessment_type)
    filing = _normalize_network_type(filing_type)
    if assessment == filing:
        return True, "值一致"

    filing_candidates = _resolve_catalog_types(filing_type, standard_types, aliases)
    for candidate in filing_candidates:
        if assessment == candidate:
            return True, "父子类"
        if _is_ancestor(assessment, candidate, children_by_parent) or _is_ancestor(
            candidate, assessment, children_by_parent
        ):
            return True, "父子类"
    return False, None


@register_rule(
    "FHX-03",
    required_fields=(
        "评测表标题",
        "网络类型",
        "Markdown行号",
        "备案网络/系统单元类型",
    ),
)
def audit_fhx_03(fields: Mapping[str, Any]) -> dict[str, Any]:
    title = str(fields.get("评测表标题") or "").strip()
    network_type = str(fields.get("网络类型") or "").strip()
    filing_network_type = str(fields.get("备案网络/系统单元类型") or "").strip()
    markdown_line = fields.get("Markdown行号")
    if not title or not network_type or not filing_network_type:
        raise ValueError("符合性评测表标题、网络类型或备案网络/系统单元类型为空")
    if not isinstance(markdown_line, int) or isinstance(markdown_line, bool) or markdown_line <= 0:
        raise ValueError("Markdown行号格式无效")

    standard_types, children_by_parent, aliases = _load_type_catalog()
    catalog_match = _normalize_network_type(network_type) in standard_types
    filing_match: bool | None = None
    match_relation: str | None = None
    if catalog_match:
        filing_match, match_relation = _match_filing_type(
            network_type,
            filing_network_type,
            standard_types,
            children_by_parent,
            aliases,
        )
    issues: list[str] = []
    if not catalog_match:
        issues.append(f"网络类型“{network_type}”不在标准类型表中")
    elif not filing_match:
        issues.append(
            f"符合性评测表网络类型“{network_type}”与备案信息表“{filing_network_type}”不一致"
        )
    passed = catalog_match and filing_match is True
    if not catalog_match:
        message = f"网络类型“{network_type}”不在标准类型表中，未比较备案类型"
    elif filing_match:
        message = (
            f"网络类型“{network_type}”在标准类型表中，且与备案信息表"
            f"按{match_relation}匹配"
        )
    else:
        message = (
            f"网络类型“{network_type}”在标准类型表中，但与备案信息表"
            f"“{filing_network_type}”不一致"
        )
    return {
        "item_id": "FHX-03",
        "passed": passed,
        "network_type": network_type,
        "filing_network_type": filing_network_type,
        "catalog_match": catalog_match,
        "filing_match": filing_match,
        "match_relation": match_relation,
        "issues": issues,
        "message": message,
    }
