from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from .errors import ExtractorNotApplicableError
from .registry import register_extractor


class FX01ExtractionError(ValueError):
    """FX-01 检验结论栏无法可靠提取。"""


class FX01SectionNotFoundError(FX01ExtractionError, ExtractorNotApplicableError):
    """全部风险评估报告均无法定位检验结论栏。"""


_HEADING_RE = re.compile(r"^#{1,6}\s+.+$")
_COUNT_RE = re.compile(
    r"(?<![不未])(?:存在?|发现)\s*[1-9]\d*\s*个\s*(?:脆弱点|(?:安全)?风险(?:项|问题)?)"
    r"|(?:很高|很低|高|中|低)(?:等级)?风险\s*[：:]?\s*[1-9]\d*\s*个"
)
_ISSUE_RE = re.compile(r"(?<!不)存在如下(?:安全)?问题\s*[：:]")
_INSPECTION_START_RE = re.compile(r"本\s*次\s*检\s*验")
_SIGNATURE_RE = re.compile(
    r"签\s*发\s*日\s*期|(?:批\s*准|审\s*核|编\s*写)\s*[：:]"
)


def _clean(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]*>", "", text)
    return html.unescape(text).replace("**", "").replace("__", "").strip()


def _label(line: str) -> str:
    return re.sub(r"\s+", "", _clean(re.sub(r"^#{1,6}\s*", "", line.strip()))).strip("：:")


def _cells(line: str) -> list[str] | None:
    if not line.strip().startswith("|"):
        return None
    return [_clean(c) for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def _flattened_conclusions(cells: list[str]) -> list[str]:
    """仅在检验信息表的标签列及其相邻值单元格内恢复结论。"""
    conclusions = []
    for column, cell in enumerate(cells[:-1]):
        # 顺序标签限定目标表，正文提及“检验结论”不构成定位依据。
        label = _label(cell)
        labels_match = re.search(r"系统名称.*检验依据.*检验结论", label)
        if labels_match is None:
            continue
        suffix = label[labels_match.end():].lstrip("：:")
        if label.count("检验结论") != 1 or (
            suffix and re.match(r"(?:签发日期|批准|审核|编写)(?:[：:]|$)", suffix) is None
        ):
            raise FX01ExtractionError("压扁表格中检验结论不是最后一个业务字段，边界不明确")
        value = cells[column + 1]
        starts = list(_INSPECTION_START_RE.finditer(value))
        if len(starts) > 1:
            raise FX01ExtractionError("压扁检验信息表存在多个“本次检验”候选，无法唯一定位结论")
        if not starts:
            continue
        start = starts[0].start()
        signature = _SIGNATURE_RE.search(value)
        if signature is not None and signature.start() < start:
            raise FX01ExtractionError("压扁表格的结论起点位于签署字段之后，边界不明确")
        end = signature.start() if signature is not None else len(value)
        # 定位不依赖风险数字；零风险结论也必须被提取并交给规则判断。
        conclusions.append(value[start:end].strip())
    return conclusions


def _extract_conclusion(markdown: str) -> tuple[str, int, int] | None:
    lines = markdown.splitlines()
    rows: list[tuple[str, int, int]] = []
    for index, line in enumerate(lines):
        cells = _cells(line)
        if cells is None:
            continue
        for column, cell in enumerate(cells):
            if _label(cell) == "检验结论" and any(c.strip() for c in cells[column + 1:]):
                # 只取标签紧邻的值，不把同一行后面的其他字段拼进来。
                rows.append((cells[column + 1], index + 1, index + 1))
    if len(rows) > 1:
        raise FX01ExtractionError("发现多个检验结论值单元格，无法唯一定位")
    if rows:
        return rows[0]

    # 标准值单元格优先，只有正常表格未命中时才兼容压扁表格。
    for index, line in enumerate(lines):
        cells = _cells(line)
        if cells is not None:
            rows.extend((text, index + 1, index + 1) for text in _flattened_conclusions(cells))
    if len(rows) > 1:
        raise FX01ExtractionError("发现多个压扁检验结论值单元格，无法唯一定位")
    if rows:
        return rows[0]

    # 正文型模板：从独占一行的检验结论到下一个标题/目录。
    starts = [i for i, line in enumerate(lines) if _label(line) == "检验结论"]
    if not starts:
        return None
    if len(starts) > 1:
        raise FX01ExtractionError("发现多个检验结论标题，无法唯一定位")
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines))
                if _HEADING_RE.match(lines[i].strip())
                or _label(lines[i]) in {"目录", "总体评价", "总体评级"}), len(lines))
    body = lines[start + 1:end]
    # 表格存在但结论单元格无法分离时，不以整张检验信息表代替结论栏。
    if any(_cells(line) is not None for line in body):
        return None
    return "\n".join(body), start + 1, end


def extract_fx_01_text(markdown: str, *, source_name: str) -> dict[str, Any]:
    section = _extract_conclusion(markdown)
    text, start, end = section if section else ("", None, None)
    normalized = re.sub(r"\s+", "", _clean(text))
    count = _COUNT_RE.search(normalized)
    issue = _ISSUE_RE.search(normalized)
    # “存在如下问题：无/未发现/无风险”不作为正向风险证据。
    if issue and (not normalized[issue.end():].strip("。；;")
                  or re.match(r"(?:无|没有|未发现|不存在)", normalized[issue.end():])):
        issue = None
    marker = count or issue
    return {
        "文件": source_name,
        "检验结论范围存在": section is not None,
        "匹配章节": "检验结论" if section else None,
        "检验结论起始行": start,
        "检验结论结束行": end,
        "检验结论原文": text,
        "问题关键词存在": marker is not None,
        "匹配关键词": marker.group(0) if marker else None,
        "问题内容摘录": normalized[max(0, marker.start()-80):marker.end()+400] if marker else "",
    }


def extract_fx_01_documents(primary_path: Path) -> dict[str, Any]:
    primary_path = Path(primary_path)
    if not primary_path.is_file():
        raise FileNotFoundError(f"输入风险评估报告 Markdown 不存在：{primary_path}")
    paths = [primary_path]
    results = [extract_fx_01_text(p.read_text(encoding="utf-8-sig"),
               source_name=str(p.relative_to(primary_path.parent))) for p in paths]
    valid = sum(item["检验结论范围存在"] for item in results)
    if not valid:
        raise FX01SectionNotFoundError(
            "风险评估报告均未找到可可靠定位的“检验结论”栏：" + "、".join(item["文件"] for item in results)
        )
    return {
        "风险评估报告文件数": len(results),
        "可定位检验结论文件数": valid,
        "包含问题关键词文件数": sum(item["问题关键词存在"] for item in results),
        "报告检查结果": results,
    }


@register_extractor("FX-01", document_name_contains=("风险评估报告.md",))
def extract_fx_01(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(source, Path):
        raise FX01ExtractionError("FX-01 需要一份标准风险评估报告作为主输入")
    return extract_fx_01_documents(source)
