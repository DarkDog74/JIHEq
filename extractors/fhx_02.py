from __future__ import annotations

from pathlib import Path
from typing import Any

from .dj_04 import _is_separator_row, _normalize_label, _split_markdown_row
from .registry import register_extractor


class FHX02ExtractionError(ValueError):
    """FHX-02 的符合性评测表答案列无法可靠提取。"""


def extract_fhx_02_text(markdown: str) -> dict[str, Any]:
    answer_index: int | None = None
    question_index: int | None = None
    sequence_index: int | None = None
    header_count = 0
    answer_rows: list[dict[str, object]] = []

    for markdown_line, line in enumerate(markdown.splitlines(), start=1):
        cells = _split_markdown_row(line)
        if cells is None:
            answer_index = None
            question_index = None
            sequence_index = None
            continue
        if _is_separator_row(cells):
            continue

        labels = [_normalize_label(cell) for cell in cells]
        if "检查点" in labels and "答案" in labels:
            answer_index = labels.index("答案")
            question_index = labels.index("检查点")
            sequence_index = labels.index("序号") if "序号" in labels else None
            header_count += 1
            continue
        if answer_index is None or question_index is None:
            continue

        question = cells[question_index].strip() if question_index < len(cells) else ""
        # 章节分组行没有检查点，不属于需要作答的题目。
        if not question:
            continue

        answer = cells[answer_index].strip() if answer_index < len(cells) else ""
        sequence = (
            cells[sequence_index].strip()
            if sequence_index is not None and sequence_index < len(cells)
            else ""
        )
        answer_rows.append(
            {
                "题目行号": len(answer_rows) + 1,
                "Markdown行号": markdown_line,
                "序号": sequence,
                "检查点": question,
                "答案": answer,
            }
        )

    if header_count == 0:
        raise FHX02ExtractionError("符合性评测表中未找到同时包含“检查点”和“答案”的表头")
    if not answer_rows:
        raise FHX02ExtractionError("符合性评测表答案列中没有可检查的题目")
    return {
        "答案记录": answer_rows,
    }


@register_extractor("FHX-02", document_name_contains=("符合性评测表.md",))
def extract_fhx_02(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入 Markdown 不存在：{path}")
    return extract_fhx_02_text(path.read_text(encoding="utf-8-sig"))
