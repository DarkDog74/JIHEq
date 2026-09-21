from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from parser.emergency_evidence import (
    DateEvidence,
    extract_drill_date_evidence,
    extract_revision_date_evidence,
)

from .errors import (
    ExtractorNotApplicableError,
    RequiredDocumentNotFoundError,
    RevisionHistoryNotFoundError,
    TargetSectionNotFoundError,
)
from .registry import register_extractor


class YLBG06ExtractionError(ExtractorNotApplicableError):
    """YLBG-06 应急材料清单或解析产物无法使用。"""


def _read_manifest(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise YLBG06ExtractionError(f"应急材料清单 JSON 格式错误：{error}") from error
    if not isinstance(payload, Mapping):
        raise YLBG06ExtractionError("应急材料清单顶层必须是对象")
    files = payload.get("files")
    if not isinstance(files, list):
        raise YLBG06ExtractionError("应急材料清单缺少 files 数组")
    return payload


def _records_by_purpose(
    manifest: Mapping[str, Any], purpose: str
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    matched = [
        record
        for record in manifest.get("files", [])
        if isinstance(record, Mapping) and record.get("用途") == purpose
    ]
    successful = [record for record in matched if record.get("解析状态") == "success"]
    return matched, successful


def _require_successful_records(
    manifest: Mapping[str, Any], purpose: str, requirement: str
) -> list[Mapping[str, Any]]:
    matched, successful = _records_by_purpose(manifest, purpose)
    if successful:
        return successful
    if not matched:
        raise RequiredDocumentNotFoundError(
            f"该系统没有{requirement}原件",
            requirement=requirement,
        )
    failures = "；".join(
        f"{record.get('源文件') or '未知文件'}："
        f"{record.get('错误原因') or '解析失败'}"
        for record in matched
    )
    raise YLBG06ExtractionError(f"{requirement}原件存在但未成功解析：{failures}")


def _read_standard_markdown(
    manifest_dir: Path, record: Mapping[str, Any], purpose: str
) -> tuple[Path, str]:
    relative = record.get("标准Markdown")
    if not isinstance(relative, str) or not relative.strip():
        raise YLBG06ExtractionError(
            f"{record.get('源文件') or purpose}缺少标准Markdown路径"
        )
    path = manifest_dir / relative
    if not path.is_file():
        raise YLBG06ExtractionError(f"标准Markdown不存在：{path}")
    return path, path.read_text(encoding="utf-8-sig")


def _with_source(
    evidence: DateEvidence,
    record: Mapping[str, Any],
    markdown_path: Path,
) -> dict[str, Any]:
    return {
        **evidence.to_dict(),
        "source_file": str(record.get("源文件") or ""),
        "markdown_file": markdown_path.name,
    }


def _record_date_key(record: Mapping[str, Any]) -> tuple[date, date, str]:
    return (
        date.fromisoformat(str(record["start_date"])),
        date.fromisoformat(str(record["end_date"])),
        str(record.get("source_file") or ""),
    )


def extract_ylbg_06_data(
    manifest: Mapping[str, Any], manifest_dir: Path
) -> dict[str, Any]:
    """提取所有客观日期证据，再选出最新修订记录和最新年份报告。"""

    plan_records = _require_successful_records(
        manifest, "应急预案", "应急预案（.doc/.docx）"
    )
    report_records = _require_successful_records(
        manifest, "应急演练报告", "应急演练报告（.doc/.docx）"
    )

    revision_records: list[dict[str, Any]] = []
    plans_without_revision: list[str] = []
    for record in plan_records:
        markdown_path, markdown = _read_standard_markdown(
            manifest_dir, record, "应急预案"
        )
        extracted = extract_revision_date_evidence(markdown)
        if not extracted:
            plans_without_revision.append(
                str(record.get("源文件") or markdown_path.name)
            )
            continue
        revision_records.extend(
            _with_source(evidence, record, markdown_path) for evidence in extracted
        )
    if plans_without_revision:
        sources = "、".join(plans_without_revision)
        raise RevisionHistoryNotFoundError(
            "应急预案的“预案管理名录/修订记录”表中未提取到修订日期："
            f"{sources}"
        )

    drill_records: list[dict[str, Any]] = []
    reports_without_date: list[str] = []
    for record in report_records:
        markdown_path, markdown = _read_standard_markdown(
            manifest_dir, record, "应急演练报告"
        )
        evidence = extract_drill_date_evidence(markdown)
        if evidence is None:
            reports_without_date.append(str(record.get("源文件") or markdown_path.name))
            continue
        drill_records.append(_with_source(evidence, record, markdown_path))
    if reports_without_date:
        sources = "、".join(reports_without_date)
        raise TargetSectionNotFoundError(
            "应急演练报告的“演练时间/时间、地点、人员”小节中未提取到演练日期："
            f"{sources}"
        )

    latest_revision = max(revision_records, key=_record_date_key)
    latest_year = max(date.fromisoformat(record["start_date"]).year for record in drill_records)
    latest_year_records = [
        record
        for record in drill_records
        if date.fromisoformat(record["start_date"]).year == latest_year
    ]
    latest_drill = max(latest_year_records, key=_record_date_key)
    return {
        "应急预案源文件": [str(record.get("源文件") or "") for record in plan_records],
        "应急演练报告源文件": [
            str(record.get("源文件") or "") for record in report_records
        ],
        "修订日期记录": revision_records,
        "演练日期记录": drill_records,
        "最新修订记录": latest_revision,
        "最新修订月份": str(latest_revision["start_date"])[:7],
        "最新演练年份": latest_year,
        "最新年度演练记录": latest_drill,
        "最新演练月份": str(latest_drill["start_date"])[:7],
        "配对依据": (
            "修订日期取全部预案修订记录中最新值；"
            "演练报告先取最新演练年份，同年多份时取演练日期最新值；"
            "稽核字段将两者归一化到 YYYY-MM 月份。"
        ),
        "告警": [],
    }


@register_extractor(
    "YLBG-06",
    document_name_contains=("应急材料分析",),
    file_suffixes=(".json",),
)
def extract_ylbg_06(source: Path) -> dict[str, Any]:
    manifest_path = Path(source)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"应急材料分析清单不存在：{manifest_path}")
    manifest = _read_manifest(manifest_path)
    return extract_ylbg_06_data(manifest, manifest_path.parent)
