from __future__ import annotations

from pathlib import Path
from typing import Any

from .fx_13 import (
    FX13ExtractionError,
    FX13IncompleteMarkdownError,
    FX13TableNotFoundError,
    FX13TableStructureError,
    _extract_measure_type_documents,
    _extract_type_column,
    _target_section,
)
from .registry import register_extractor


FX14ExtractionError = FX13ExtractionError
FX14IncompleteMarkdownError = FX13IncompleteMarkdownError
FX14TableNotFoundError = FX13TableNotFoundError
FX14TableStructureError = FX13TableStructureError

_TARGET_KEYWORD = "预防性安全措施"
_EVIDENCE_FIELD = "预防性安全措施证据"


def extract_fx_14_text(markdown: str) -> dict[str, Any]:
    section, title_line, title, source = _target_section(markdown)
    type_column_exists, keyword_evidence = _extract_type_column(
        section, target_keyword=_TARGET_KEYWORD
    )
    return {
        "目标表标题": title,
        "目标表标题行": title_line,
        "定位方式": source,
        "类型列存在": type_column_exists,
        _EVIDENCE_FIELD: keyword_evidence,
    }


def extract_fx_14_documents(primary_path: Path) -> dict[str, Any]:
    return _extract_measure_type_documents(
        primary_path,
        text_extractor=extract_fx_14_text,
        evidence_field=_EVIDENCE_FIELD,
    )


@register_extractor("FX-14", document_name_contains=("风险评估报告.md",))
def extract_fx_14(path: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(path, Path):
        raise FX14ExtractionError("FX-14 需要一份标准风险评估报告作为主输入")
    return extract_fx_14_documents(path)
