from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .dj_04 import _is_separator_row, _normalize_label, _split_markdown_row
from .errors import ExtractorNotApplicableError
from .registry import register_extractor


class FHX04ExtractionError(ExtractorNotApplicableError):
    """FHX-04 的符合性评测表无法可靠提取。"""


_PROOF_FILE_PATTERN = "符合性评测表证明材料*.md"
_DISPIMG_RE = re.compile(r"^=?\s*DISPIMG\s*\(.*\)\s*$", re.IGNORECASE)
_REFERENCE_ONLY_RE = re.compile(
    r"^(?:详见|见|参见)(?:独立)?(?:符合性评测表)?(?:证明材料|附件(?:截图)?|机房文档)[。.!！]?$"
)
_PLACEHOLDER_VALUES = frozenset(
    {
        "占位",
        "占位符",
        "待定",
        "待补充",
        "待完善",
        "n/a",
        "na",
        "-",
        "—",
        "/",
    }
)


def _non_substantive_reason(value: str) -> str | None:
    # FHX-01 仍使用实质内容判断；FHX-04 只判断空白，不调用此函数。
    compact = value.strip()
    if not compact:
        return "答案说明为空"
    if _DISPIMG_RE.fullmatch(compact):
        return "答案说明仅为DISPIMG图片占位符"
    if compact.casefold() in _PLACEHOLDER_VALUES:
        return "答案说明仅为占位符"
    if _REFERENCE_ONLY_RE.fullmatch(compact):
        return "答案说明仅引用外部材料"
    return None


def extract_fhx_04_text(
    markdown: str,
    *,
    proof_material_exists: bool,
    proof_material_path: str | None = None,
) -> dict[str, Any]:
    indexes: dict[str, int] | None = None
    table_count = 0
    item_count = 0
    nonempty_count = 0
    proof_required_items: list[dict[str, object]] = []

    for markdown_line, line in enumerate(markdown.splitlines(), start=1):
        cells = _split_markdown_row(line)
        if cells is None:
            indexes = None
            continue
        if _is_separator_row(cells):
            continue

        labels = [_normalize_label(cell) for cell in cells]
        required_headers = ("检查点", "答案", "答案说明")
        if all(header in labels for header in required_headers):
            indexes = {
                header: labels.index(header)
                for header in ("序号", *required_headers)
                if header in labels
            }
            table_count += 1
            continue
        if indexes is None:
            continue

        def value(field: str) -> str:
            index = indexes.get(field)
            return cells[index].strip() if index is not None and index < len(cells) else ""

        check_point = value("检查点")
        answer = value("答案")
        if not check_point and not answer:
            continue

        item_count += 1
        explanation = value("答案说明")
        if explanation:
            nonempty_count += 1
            continue
        proof_required_items.append(
            {
                "评测项行号": item_count,
                "Markdown行号": markdown_line,
                "序号": value("序号"),
                "检查点": check_point,
                "答案": answer,
                "答案说明": explanation,
                "需证明材料原因": "答案说明为空",
            }
        )

    if table_count == 0:
        raise FHX04ExtractionError(
            "符合性评测表中未找到包含“检查点、答案、答案说明”的数据表"
        )
    if item_count == 0:
        raise FHX04ExtractionError("符合性评测表中没有可检查的评测项")

    return {
        "评测项总数": item_count,
        "答案说明非空项数": nonempty_count,
        "答案说明为空项": proof_required_items,
        "证明材料存在": proof_material_exists,
        "证明材料路径": proof_material_path,
    }


@register_extractor("FHX-04", document_name_contains=("符合性评测表.md",))
def extract_fhx_04(path: Path) -> dict[str, Any]:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"输入 Markdown 不存在：{path}")
    # 只看当前系统目录内已分类的证明文件是否存在，不读取内容或匹配编号。
    proof_path = next(
        (candidate for candidate in sorted(path.parent.glob(_PROOF_FILE_PATTERN))
         if candidate.is_file()),
        None,
    )
    return extract_fhx_04_text(
        path.read_text(encoding="utf-8-sig"),
        proof_material_exists=proof_path is not None,
        proof_material_path=str(proof_path) if proof_path is not None else None,
    )
