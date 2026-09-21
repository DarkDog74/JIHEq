from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .dj_04 import _is_separator_row, _normalize_label, _split_markdown_row
from .errors import RequiredDocumentNotFoundError
from .fhx_04 import _non_substantive_reason
from .registry import register_extractor


class FHX01ExtractionError(ValueError):
    """FHX-01 的检查项编号无法可靠提取。"""


_ID_TEXT = r"\d+(?:\s*[-－—–]\s*\d+){4}"
_FULL_ID_RE = re.compile(rf"^{_ID_TEXT}$")
_PROOF_ID_RE = re.compile(
    rf"^\s*(?:#{{1,6}}\s*)?(?:[-*+]\s*)?(?:\|\s*)?(?:\*\*|__)?"
    rf"(?P<id>{_ID_TEXT})(?!\d|\s*[-－—–]\s*\d)"
)
_DISPIMG_MARKER_RE = re.compile(r"\bDISPIMG\s*\(", re.IGNORECASE)


def _normalize_id(value: str) -> str:
    return re.sub(r"\s*[-－—–]\s*", "-", value.strip())


def _sequence_index(cells: list[str]) -> int | None:
    labels = [_normalize_label(cell) for cell in cells]
    if "序号" not in labels:
        return None
    return labels.index("序号")


def extract_evaluation_ids(markdown: str) -> list[dict[str, object]]:
    sequence_index: int | None = None
    explanation_index: int | None = None
    found_header = False
    items: list[dict[str, object]] = []

    for markdown_line, line in enumerate(markdown.splitlines(), start=1):
        cells = _split_markdown_row(line)
        if cells is None:
            sequence_index = None
            explanation_index = None
            continue
        if _is_separator_row(cells):
            continue
        candidate_index = _sequence_index(cells)
        if candidate_index is not None:
            sequence_index = candidate_index
            labels = [_normalize_label(cell) for cell in cells]
            explanation_index = (
                labels.index("答案说明") if "答案说明" in labels else None
            )
            found_header = True
            continue
        if sequence_index is None:
            continue

        sequence = cells[sequence_index].strip() if sequence_index < len(cells) else ""
        if not _FULL_ID_RE.fullmatch(sequence):
            continue
        explanation = (
            cells[explanation_index].strip()
            if explanation_index is not None and explanation_index < len(cells)
            else ""
        )
        # FHX-01 的口径只要答案说明出现 WPS DISPIMG 标记，
        # 就视为已在评测表内提供证明材料，不再要求独立证明文档。
        # 这是 FHX-01 的专属口径，不改变 FHX-04 的占位符判断。
        non_substantive_reason = (
            None
            if _DISPIMG_MARKER_RE.search(explanation)
            else _non_substantive_reason(explanation)
        )
        items.append(
            {
                "编号": _normalize_id(sequence),
                "Markdown行号": markdown_line,
                "答案说明": explanation,
                "答案说明有实质内容": non_substantive_reason is None,
                "需证明材料": non_substantive_reason is not None,
                "需证明材料原因": non_substantive_reason,
            }
        )

    if not found_header:
        raise FHX01ExtractionError("符合性评测表中未找到“序号”表头")
    if not items:
        raise FHX01ExtractionError("符合性评测表中未找到五段编号")
    return items


# 保留旧函数名，避免已有调用方中断；语义已改为提取评测表全部编号。
extract_actual_check_items = extract_evaluation_ids


def extract_proof_ids(
    markdown: str,
    source_name: str,
    *,
    known_ids: Iterable[str] | None = None,
) -> list[dict[str, object]]:
    normalized_known_ids = sorted(
        {_normalize_id(value) for value in known_ids or ()},
        key=len,
        reverse=True,
    )
    items: list[dict[str, object]] = []
    for markdown_line, line in enumerate(markdown.splitlines(), start=1):
        match = _PROOF_ID_RE.match(line)
        if match is None:
            continue
        candidate_id = _normalize_id(match.group("id"))
        item_id = next(
            (
                known_id
                for known_id in normalized_known_ids
                if candidate_id.startswith(known_id)
            ),
            candidate_id,
        )
        items.append(
            {
                "编号": item_id,
                "文件": source_name,
                "Markdown行号": markdown_line,
            }
        )
    return items


def extract_fhx_01_documents(evaluation_path: Path) -> dict[str, Any]:
    evaluation_path = Path(evaluation_path)
    if not evaluation_path.is_file():
        raise FileNotFoundError(f"输入 Markdown 不存在：{evaluation_path}")

    evaluation_items = extract_evaluation_ids(
        evaluation_path.read_text(encoding="utf-8-sig")
    )
    proof_required_items = [
        item for item in evaluation_items if item["需证明材料"] is True
    ]

    proof_paths = sorted(
        path
        for path in evaluation_path.parent.rglob("符合性评测表证明材料*.md")
        if path.is_file()
    )
    if proof_required_items and not proof_paths:
        raise RequiredDocumentNotFoundError(
            f"单位目录中没有符合性评测表证明材料 Markdown：{evaluation_path.parent}",
            requirement="符合性评测表证明材料",
        )
    proof_items: list[dict[str, object]] = []
    evaluation_ids = [str(item["编号"]) for item in evaluation_items]
    for proof_path in proof_paths:
        proof_items.extend(
            extract_proof_ids(
                proof_path.read_text(encoding="utf-8-sig"),
                str(proof_path.relative_to(evaluation_path.parent)),
                known_ids=evaluation_ids,
            )
        )

    return {
        "评测表全部编号": evaluation_items,
        "需证明材料编号": proof_required_items,
        "证明材料全部编号": proof_items,
        "证明材料文件": [
            str(path.relative_to(evaluation_path.parent)) for path in proof_paths
        ],
    }


@register_extractor("FHX-01", document_name_contains=("符合性评测表.md",))
def extract_fhx_01(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(source, Path):
        raise FHX01ExtractionError("FHX-01 需要一份符合性评测表作为主输入")
    return extract_fhx_01_documents(source)
