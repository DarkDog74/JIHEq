from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

from .errors import ExtractorNotApplicableError
from .registry import register_extractor


class FX03ExtractionError(ValueError):
    """FX-03 的风险评估报告标题无法可靠提取。"""


class FX03NoUsableReportError(FX03ExtractionError, ExtractorNotApplicableError):
    """FX-03 找到风险评估报告，但全部是显式解析失败存根。"""


_HEADING_RE = re.compile(r"^#{1,6}\s*(?P<title>.+?)\s*$")
_DEGRADED_HEADING_RE = re.compile(
    r"(?<!\d)\d+(?:\.\d+)*\.\s*"
    r"(?P<title>"
    r"业务识别(?:范围|方法|结果)?|"
    r"业务(?:重要性)?赋值(?:说明|调整|结果)?|"
    r"(?:系统)?资产识别(?:结果)?|"
    r"资产价值赋值|"
    r"威胁识别(?:概述|方法|结果)?|"
    r"威胁赋值(?:说明)?|"
    r"脆弱性识别(?:概述|方法|结果)?|"
    r"脆弱性赋值(?:说明)?|"
    r"已有安全措施(?:确认|有效性分析(?:概述|结果)?)?|"
    r"风险计算(?:与评价|方法|结果)?(?!模型)|"
    r"风险评价(?:方法|结果)?|"
    r"风险总结"
    r")"
)
_DOT_LEADER_RE = re.compile(r"\s*[.…·]{3,}")
_PARSE_ERROR_RE = re.compile(r"<!--\s*PARSE_ERROR\b", re.IGNORECASE)
_STANDALONE_TITLES = {
    "业务识别",
    "业务重要性赋值",
    "业务赋值调整",
    "业务赋值结果",
    "资产识别",
    "系统资产识别",
    "资产价值赋值",
    "威胁识别",
    "威胁赋值说明",
    "脆弱性识别",
    "脆弱性赋值说明",
    "已有安全措施确认",
    "已有安全措施有效性分析",
    "已有安全措施有效性分析结果",
    "风险计算",
    "风险计算与评价",
    "风险评价",
    "风险评价方法",
    "风险评价结果",
    "风险总结",
    "总体评价",
    "总体评级",
}
_REQUIRED_TITLES = (
    "业务识别和赋值",
    "资产识别和赋值",
    "威胁识别和赋值",
    "脆弱性识别和赋值",
    "已有安全措施确认",
    "风险计算",
    "风险总结",
)


Heading = dict[str, Any]
HeadingPredicate = Callable[[str], bool]


def _normalize_heading(title: str) -> str:
    title = title.replace("\\", "")
    title = re.sub(r"[*_`]", "", title)
    return re.sub(r"\s+", "", title).strip("#：:")


def _extract_headings(markdown: str) -> list[Heading]:
    headings: list[Heading] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        match = _HEADING_RE.fullmatch(line.strip())
        if match is not None:
            raw_title = match.group("title").strip()
            headings.append(
                {
                    "行号": line_number,
                    "标题": raw_title,
                    "归一化标题": _normalize_heading(raw_title),
                    "识别方式": "Markdown标题",
                }
            )
            continue

        stripped = line.strip()
        if not stripped:
            continue
        # 对 FX-03 这种“标题是否存在”的规则，目录中的带编号点线标题可作为
        # 文档结构证据。PDF 转 Markdown 时正文标题可能丢失，但目录文字仍保留。
        # 只接受标准编号 + 点线结构，普通正文中的同名短语仍不会计入。
        toc_leader = _DOT_LEADER_RE.search(stripped)
        if toc_leader is not None:
            toc_text = stripped[: toc_leader.start()].strip()
            toc_match = _DEGRADED_HEADING_RE.match(toc_text)
            if toc_match is not None:
                raw_title = toc_match.group("title").strip()
                headings.append(
                    {
                        "行号": line_number,
                        "标题": raw_title,
                        "归一化标题": _normalize_heading(raw_title),
                        "识别方式": "目录标题项",
                    }
                )
            continue
        if stripped.startswith("[") and "](" in stripped:
            continue
        is_table_row = stripped.startswith("|")
        normalized_line = _normalize_heading(stripped)
        if not is_table_row and normalized_line in _STANDALONE_TITLES:
            headings.append(
                {
                    "行号": line_number,
                    "标题": stripped,
                    "归一化标题": normalized_line,
                    "识别方式": "独占行标题",
                }
            )
            continue
        segments = stripped.split("|") if is_table_row else [stripped]
        for segment in segments:
            candidate_text = segment.strip()
            if not candidate_text:
                continue
            candidates = (
                _DEGRADED_HEADING_RE.finditer(candidate_text)
                if is_table_row
                else [_DEGRADED_HEADING_RE.match(candidate_text)]
            )
            for degraded_match in candidates:
                if degraded_match is None:
                    continue
                suffix = candidate_text[degraded_match.end() :]
                if _DOT_LEADER_RE.match(suffix):
                    continue
                raw_title = degraded_match.group("title").strip()
                headings.append(
                    {
                        "行号": line_number,
                        "标题": raw_title,
                        "归一化标题": _normalize_heading(raw_title),
                        "识别方式": "降级编号标题",
                    }
                )
    return headings


def _contains(*fragments: str) -> HeadingPredicate:
    return lambda title: all(fragment in title for fragment in fragments)


def _risk_calculation(title: str) -> bool:
    return "风险计算" in title and "风险计算模型" not in title


def _existing_security_measures(title: str) -> bool:
    return any(
        label in title
        for label in (
            "已有安全措施确认",
            "已有安全措施有效性分析结果",
            "已有安全措施有效性分析",
        )
    )


def _risk_summary(title: str) -> bool:
    return any(
        label in title
        for label in ("风险总结", "风险评价", "总体评价", "总体评级")
    )


_TITLE_REQUIREMENTS: dict[str, tuple[HeadingPredicate, ...]] = {
    "业务识别和赋值": (_contains("业务识别"), _contains("业务", "赋值")),
    "资产识别和赋值": (_contains("资产识别"), _contains("资产", "赋值")),
    "威胁识别和赋值": (_contains("威胁识别"), _contains("威胁", "赋值")),
    "脆弱性识别和赋值": (
        _contains("脆弱性识别"),
        _contains("脆弱性", "赋值"),
    ),
    "已有安全措施确认": (_existing_security_measures,),
    "风险计算": (_risk_calculation,),
    "风险总结": (_risk_summary,),
}


def _match_requirement(
    headings: list[Heading], predicates: tuple[HeadingPredicate, ...]
) -> list[dict[str, Any]]:
    matched: dict[tuple[int, str], dict[str, Any]] = {}
    for predicate in predicates:
        candidate = next(
            (
                heading
                for heading in headings
                if predicate(str(heading["归一化标题"]))
            ),
            None,
        )
        if candidate is None:
            return []
        key = (int(candidate["行号"]), str(candidate["标题"]))
        matched[key] = {
            "行号": candidate["行号"],
            "标题": candidate["标题"],
            "识别方式": candidate["识别方式"],
        }
    return [matched[key] for key in sorted(matched)]


def extract_fx_03_text(markdown: str, *, source_name: str) -> dict[str, Any]:
    headings = _extract_headings(markdown)
    checks: list[dict[str, Any]] = []
    for required_title in _REQUIRED_TITLES:
        evidence = _match_requirement(
            headings, _TITLE_REQUIREMENTS[required_title]
        )
        checks.append(
            {
                "目标标题": required_title,
                "存在": bool(evidence),
                "匹配标题": evidence,
            }
        )

    missing_titles = [item["目标标题"] for item in checks if not item["存在"]]
    return {
        "文件": source_name,
        "Markdown标题数": len(headings),
        "已找到标题数": len(_REQUIRED_TITLES) - len(missing_titles),
        "完整": not missing_titles,
        "缺失标题": missing_titles,
        "标题检查": checks,
    }


def extract_fx_03_documents(primary_path: Path) -> dict[str, Any]:
    primary_path = Path(primary_path)
    if not primary_path.is_file():
        raise FileNotFoundError(f"输入风险评估报告 Markdown 不存在：{primary_path}")

    report_paths = [primary_path]
    if not report_paths:
        raise FX03ExtractionError("未找到可检查的风险评估报告 Markdown")

    usable_reports: list[tuple[Path, str]] = []
    parse_error_count = 0
    for path in report_paths:
        markdown = path.read_text(encoding="utf-8-sig")
        if _PARSE_ERROR_RE.search(markdown):
            parse_error_count += 1
            continue
        usable_reports.append((path, markdown))
    if not usable_reports:
        names = "、".join(path.name for path in report_paths)
        raise FX03NoUsableReportError(
            f"风险评估报告全部为显式解析失败存根：{names}"
        )

    results = [
        extract_fx_03_text(
            markdown,
            source_name=str(path.relative_to(primary_path.parent)),
        )
        for path, markdown in usable_reports
    ]
    best = min(
        results,
        key=lambda item: (-int(item["已找到标题数"]), str(item["文件"])),
    )
    return {
        "风险评估报告文件数": len(results),
        "解析异常报告数": parse_error_count,
        "完整标题报告数": sum(bool(item["完整"]) for item in results),
        "最佳匹配报告": best["文件"],
        "已找到标题数": best["已找到标题数"],
        "缺失标题": best["缺失标题"],
        "报告检查结果": results,
    }


@register_extractor("FX-03", document_name_contains=("风险评估报告.md",))
def extract_fx_03(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(source, Path):
        raise FX03ExtractionError("FX-03 需要一份标准风险评估报告作为主输入")
    return extract_fx_03_documents(source)
