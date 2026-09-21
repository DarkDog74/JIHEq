from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .dj_04 import _is_separator_row, _normalize_label, _split_markdown_row
from .registry import register_extractor


class FHX03ExtractionError(ValueError):
    """FHX-03 的评测表或备案网络类型无法可靠提取。"""


_TITLE_RE = re.compile(
    r"^(?:\d{4}\s*年度\s*)?通信网络安全防护符合性评测表\s*[-－—]\s*(?P<network_type>.+?)\s*$"
)


def _plain_text(value: str) -> str:
    candidate = value.strip().replace("**", "").replace("__", "").replace("`", "")
    candidate = re.sub(r"^#{1,6}\s+", "", candidate)
    return candidate.strip()


def extract_fhx_03_text(markdown: str) -> dict[str, Any]:
    for markdown_line, line in enumerate(markdown.splitlines(), start=1):
        cells = _split_markdown_row(line)
        if cells is not None:
            if _is_separator_row(cells):
                continue
            candidates = [cell for cell in cells if cell.strip()]
        else:
            candidate = _plain_text(line)
            candidates = [candidate] if candidate else []

        for candidate in candidates:
            title = _plain_text(candidate)
            match = _TITLE_RE.fullmatch(title)
            if match is None:
                continue
            network_type = match.group("network_type").strip()
            if not network_type:
                raise FHX03ExtractionError("符合性评测表标题中的网络类型为空")
            return {
                "评测表标题": title,
                "网络类型": network_type,
                "Markdown行号": markdown_line,
            }

    raise FHX03ExtractionError(
        "未找到“通信网络安全防护符合性评测表－<网络类型>”格式的真实标题"
    )


def extract_filing_network_type(payload: Mapping[str, Any]) -> dict[str, str]:
    target_label = _normalize_label("网络/系统单元类型")
    matched_keys = [
        key
        for key in payload
        if re.sub(r"[\\*＊]", "", _normalize_label(str(key))) == target_label
    ]
    if not matched_keys:
        raise FHX03ExtractionError(
            "备案信息表中未找到 key 为“网络/系统单元类型”的字段"
        )
    if len(matched_keys) > 1:
        raise FHX03ExtractionError(
            "备案信息表中存在多个“网络/系统单元类型”字段"
        )

    key = matched_keys[0]
    value = payload[key]
    if not isinstance(value, str) or not value.strip():
        raise FHX03ExtractionError("备案信息表“网络/系统单元类型”值为空或格式无效")
    return {"备案字段名": str(key), "备案网络/系统单元类型": value.strip()}


def extract_fhx_03_documents(
    assessment_path: Path, filing_path: Path
) -> dict[str, Any]:
    if not assessment_path.is_file():
        raise FileNotFoundError(f"输入符合性评测表 Markdown 不存在：{assessment_path}")
    if not filing_path.is_file():
        raise FileNotFoundError(f"输入备案信息表 JSON 不存在：{filing_path}")

    assessment = extract_fhx_03_text(
        assessment_path.read_text(encoding="utf-8-sig")
    )
    try:
        filing_payload = json.loads(filing_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise FHX03ExtractionError(f"备案信息表 JSON 格式错误：{error}") from error
    if not isinstance(filing_payload, Mapping):
        raise FHX03ExtractionError("备案信息表 JSON 顶层必须是对象")
    return {**assessment, **extract_filing_network_type(filing_payload)}


@register_extractor(
    "FHX-03",
    document_name_contains=("符合性评测表.md",),
    additional_document_name_groups=(("备案信息表",),),
    additional_document_file_suffix_groups=((".json",),),
)
def extract_fhx_03(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if isinstance(source, Path) or len(source) != 2:
        raise FHX03ExtractionError(
            "FHX-03 需要同时输入符合性评测表 Markdown 和备案信息表 JSON"
        )
    return extract_fhx_03_documents(source[0], source[1])
