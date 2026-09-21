from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
GROUND_TRUTH_FILENAME = "ground_truth.v1.json"
DEFAULT_LABEL_FILE = PROJECT_ROOT / "reference_data" / GROUND_TRUTH_FILENAME
GROUND_TRUTH_SCHEMA_VERSION = 1
GROUND_TRUTH_KIND = "audit_ground_truth"

LABEL_PASSED = "通过"
LABEL_FAILED = "不通过"
LABEL_NOT_APPLICABLE = "不适用"
LABELS = (LABEL_PASSED, LABEL_FAILED, LABEL_NOT_APPLICABLE)
CLASSIFICATION_LABELS = {
    "passed": LABEL_PASSED,
    "failed": LABEL_FAILED,
    "not_applicable": LABEL_NOT_APPLICABLE,
}

_ITEM_ID_FULL_PATTERN = re.compile(r"[A-Z][A-Z0-9]*-\d{2}")

COVERAGE_SCORED = "双方存在"
COVERAGE_TRUTH_ONLY = "仅真实标签"
COVERAGE_PREDICTION_ONLY = "仅程序结果"


@dataclass(frozen=True)
class TruthLabel:
    system: str
    item_id: str
    item_name: str
    category: str
    classification: str
    source_location: str

    @property
    def label(self) -> str:
        """仅用于中文报告展示，评分使用 classification 原值。"""
        return CLASSIFICATION_LABELS[self.classification]


@dataclass(frozen=True)
class GroundTruthTable:
    systems: tuple[str, ...]
    item_ids: tuple[str, ...]
    labels: dict[str, dict[str, TruthLabel]]
    scope: str
    source_name: str


@dataclass(frozen=True)
class Prediction:
    item_id: str
    classification: str
    status: str
    prediction_source: str
    summary_file: str
    diagnostic: str

    @property
    def label(self) -> str:
        """仅用于中文报告展示，评分使用 classification 原值。"""
        return CLASSIFICATION_LABELS[self.classification]


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"结构化标签 JSON 包含重复键：{key}")
        result[key] = value
    return result


def _require_exact_keys(
    value: dict[str, Any], required: set[str], *, location: str
) -> None:
    actual = set(value)
    missing = sorted(required - actual)
    extra = sorted(actual - required)
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("缺少=" + "、".join(missing))
        if extra:
            details.append("多余=" + "、".join(extra))
        raise ValueError(f"{location} 字段不符合 JSON v1：" + "；".join(details))


def _require_nonempty_text(value: Any, *, location: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{location} 必须是非空字符串")
    if value != value.strip():
        raise ValueError(f"{location} 首尾不允许有空白字符")
    return value


def load_ground_truth_json(label_file: Path) -> GroundTruthTable:
    """读取严格的 ``audit_ground_truth`` JSON v1，不做文本推断。"""
    requested = Path(label_file).expanduser()
    if requested.is_symlink():
        raise ValueError(f"结构化标签文件不允许使用符号链接：{requested}")
    path = requested.resolve()
    if path.suffix.casefold() != ".json":
        raise ValueError(f"真实标签必须是 JSON v1，不再支持 Markdown：{path}")
    if not path.is_file():
        raise FileNotFoundError(f"结构化真实标签不存在：{path}")
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"结构化标签 JSON 语法错误：{path}：{error}") from error
    if not isinstance(payload, dict):
        raise ValueError("结构化标签 JSON 顶层必须是对象")
    _require_exact_keys(
        payload,
        {"schema_version", "kind", "source_name", "scope", "items", "systems"},
        location="JSON 顶层",
    )
    version = payload["schema_version"]
    if type(version) is not int or version != GROUND_TRUTH_SCHEMA_VERSION:
        raise ValueError(
            f"schema_version 必须是整数 {GROUND_TRUTH_SCHEMA_VERSION}"
        )
    if payload["kind"] != GROUND_TRUTH_KIND:
        raise ValueError(f"kind 必须是 {GROUND_TRUTH_KIND!r}")
    source_name = _require_nonempty_text(
        payload["source_name"], location="source_name"
    )
    scope = _require_nonempty_text(payload["scope"], location="scope")

    raw_items = payload["items"]
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("items 必须是非空数组")
    item_ids: list[str] = []
    item_metadata: dict[str, tuple[str, str]] = {}
    for index, raw_item in enumerate(raw_items):
        location = f"items[{index}]"
        if not isinstance(raw_item, dict):
            raise ValueError(f"{location} 必须是对象")
        _require_exact_keys(
            raw_item, {"item_id", "name", "category"}, location=location
        )
        item_id = _require_nonempty_text(
            raw_item["item_id"], location=f"{location}.item_id"
        )
        if not _ITEM_ID_FULL_PATTERN.fullmatch(item_id):
            raise ValueError(f"{location}.item_id 格式无效：{item_id!r}")
        if item_id in item_metadata:
            raise ValueError(f"items 中稽核项重复：{item_id}")
        item_name = _require_nonempty_text(
            raw_item["name"], location=f"{location}.name"
        )
        category = _require_nonempty_text(
            raw_item["category"], location=f"{location}.category"
        )
        item_ids.append(item_id)
        item_metadata[item_id] = (item_name, category)

    raw_systems = payload["systems"]
    if not isinstance(raw_systems, dict) or not raw_systems:
        raise ValueError("systems 必须是非空对象")
    systems: list[str] = []
    canonical_systems: dict[str, str] = {}
    labels: dict[str, dict[str, TruthLabel]] = {}
    expected_item_ids = set(item_ids)
    for raw_system, raw_labels in raw_systems.items():
        system = _require_nonempty_text(raw_system, location="systems 系统名")
        canonical = unicodedata.normalize("NFKC", system)
        if canonical in canonical_systems:
            raise ValueError(
                "systems 系统名归一化后重复："
                f"{canonical_systems[canonical]} / {system}"
            )
        canonical_systems[canonical] = system
        if not isinstance(raw_labels, dict):
            raise ValueError(f"systems[{system!r}] 必须是稽核项标签对象")
        actual_item_ids = set(raw_labels)
        missing = sorted(expected_item_ids - actual_item_ids)
        extra = sorted(actual_item_ids - expected_item_ids)
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append("缺少=" + "、".join(missing))
            if extra:
                details.append("多余=" + "、".join(extra))
            raise ValueError(
                f"系统 {system} 的标签矩阵不完整：" + "；".join(details)
            )
        system_labels: dict[str, TruthLabel] = {}
        for item_id in item_ids:
            classification = raw_labels[item_id]
            if (
                not isinstance(classification, str)
                or classification not in CLASSIFICATION_LABELS
            ):
                raise ValueError(
                    f"系统 {system} / {item_id} 的 classification 无效："
                    f"{classification!r}"
                )
            item_name, category = item_metadata[item_id]
            system_labels[item_id] = TruthLabel(
                system=system,
                item_id=item_id,
                item_name=item_name,
                category=category,
                classification=classification,
                source_location=f"systems[{system!r}].{item_id}",
            )
        systems.append(system)
        labels[system] = system_labels

    return GroundTruthTable(
        systems=tuple(systems),
        item_ids=tuple(item_ids),
        labels=labels,
        scope=scope,
        source_name=source_name,
    )


def ground_truth_path_for_source(source_dir: Path) -> Path:
    """返回某原始材料目录对应的固定 JSON v1 标签路径。"""
    source = Path(source_dir).expanduser().resolve()
    return source.with_name(f"{source.name}label") / GROUND_TRUTH_FILENAME


def match_system_names(
    truth_systems: Iterable[str], result_units: Iterable[str]
) -> dict[str, dict[str, str]]:
    """系统名必须与 ``batch-all-source`` 产出的 unit 完全一致。"""
    truth_list = list(truth_systems)
    unit_list = list(result_units)
    if not all(isinstance(name, str) and name for name in truth_list + unit_list):
        raise ValueError("真实标签和稽核结果的系统名都必须是非空字符串")
    if len(set(truth_list)) != len(truth_list):
        raise ValueError("真实标签中存在重复系统名")
    if len(set(unit_list)) != len(unit_list):
        raise ValueError("稽核结果中存在重复系统 unit")
    missing = sorted(set(truth_list) - set(unit_list))
    extra = sorted(set(unit_list) - set(truth_list))
    if missing or extra:
        details: list[str] = []
        if missing:
            details.append("结果缺失=" + "、".join(missing))
        if extra:
            details.append("标签缺失=" + "、".join(extra))
        raise ValueError("真实标签与稽核结果的系统 unit 不一致：" + "；".join(details))
    return {
        system: {
            "truth_system": system,
            "result_unit": system,
            "match_method": "exact",
        }
        for system in truth_list
    }


def _read_json_object(path: Path, description: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"无法读取{description} {path}：{error}") from error
    if not isinstance(value, dict):
        raise ValueError(f"{description}必须是 JSON 对象：{path}")
    return value


def _is_within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def classify_prediction_item(item: dict[str, Any]) -> Prediction:
    """只接受新汇总中的显式三分类字段，不从其他字段猜测。"""
    item_id = item.get("item_id")
    if not isinstance(item_id, str) or not _ITEM_ID_FULL_PATTERN.fullmatch(item_id):
        raise ValueError(f"程序结果中稽核项编号无效：{item_id!r}")

    classification = item.get("classification")
    if classification not in CLASSIFICATION_LABELS:
        if classification is None:
            raise ValueError(
                f"稽核项 {item_id} 缺少 classification 三分类字段；"
                "这是旧版汇总，请重新运行 batch-all-source"
            )
        raise ValueError(
            f"稽核项 {item_id} 的 classification 值无效：{classification!r}"
        )
    return Prediction(
        item_id=item_id,
        classification=str(classification),
        status=str(item.get("status", "")),
        prediction_source="classification",
        summary_file=str(item.get("summary_file") or ""),
        diagnostic="",
    )


def _resolve_system_summary(
    result_root: Path, system_record: dict[str, Any]
) -> Path:
    unit = system_record.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError("全部系统汇总中存在空系统名")
    unit_path = Path(unit)
    if unit_path.is_absolute() or ".." in unit_path.parts:
        raise ValueError(f"系统相对路径不安全：{unit}")

    expected = (
        result_root / "稽核结果" / unit_path / "系统稽核汇总.json"
    ).resolve(strict=False)
    if not _is_within(expected, result_root):
        raise ValueError(
            f"系统“{unit}”的汇总路径越出全流程结果根目录：{expected}"
        )
    raw_summary = system_record.get("summary_file")
    if isinstance(raw_summary, str) and raw_summary.strip():
        referenced = Path(raw_summary).expanduser()
        if not referenced.is_absolute():
            referenced = result_root / referenced
        resolved_reference = referenced.resolve(strict=False)
        if not _is_within(resolved_reference, result_root):
            raise ValueError(
                f"系统“{unit}”的 summary_file 指向结果目录之外："
                f"{resolved_reference}"
            )
        if resolved_reference != expected:
            raise ValueError(
                f"系统“{unit}”的 summary_file 与按系统目录重建的路径不一致："
                f"{resolved_reference} != {expected}"
            )
    if not expected.is_file():
        raise FileNotFoundError(
            f"找不到系统“{unit}”的系统稽核汇总.json：{expected}"
        )
    return expected


def _load_system_predictions(
    result_root: Path, source_root: Path, system_record: dict[str, Any]
) -> tuple[dict[str, Prediction], Path]:
    summary_path = _resolve_system_summary(result_root, system_record)
    summary = _read_json_object(summary_path, "系统稽核汇总")
    unit = str(system_record["unit"])
    if summary.get("unit") != unit:
        raise ValueError(
            f"系统稽核汇总内部 unit 与所在结果目录不一致："
            f"{summary.get('unit')!r} != {unit!r}"
        )
    recorded_source = summary.get("source_directory")
    if not isinstance(recorded_source, str) or not recorded_source.strip():
        raise ValueError(f"系统稽核汇总缺少 source_directory：{summary_path}")
    expected_source = (source_root / Path(unit)).resolve(strict=False)
    if Path(recorded_source).expanduser().resolve(strict=False) != expected_source:
        raise ValueError(
            f"系统“{unit}”的 source_directory 与原始系统目录不一致："
            f"{recorded_source} != {expected_source}"
        )
    audit = summary.get("audit")
    if not isinstance(audit, dict):
        if summary.get("audit_error"):
            return {}, summary_path
        raise ValueError(f"系统稽核汇总缺少 audit 对象：{summary_path}")
    items = audit.get("items")
    if not isinstance(items, list):
        raise ValueError(f"系统稽核汇总缺少 audit.items 数组：{summary_path}")

    predictions: dict[str, Prediction] = {}
    for item in items:
        if not isinstance(item, dict):
            raise ValueError(f"系统稽核汇总 audit.items 中存在非对象：{summary_path}")
        prediction = classify_prediction_item(item)
        if prediction.item_id in predictions:
            raise ValueError(
                f"系统稽核汇总中稽核项重复：{prediction.item_id} | {summary_path}"
            )
        predictions[prediction.item_id] = prediction
    return predictions, summary_path


def _stats_for_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    materialized = list(rows)
    scored = [row for row in materialized if row["score"] is not None]
    correct = sum(row["score"] == 1 for row in scored)
    incorrect = len(scored) - correct
    truth_counts = Counter(
        row["truth_label"] for row in materialized if row["truth_label"]
    )
    prediction_counts = Counter(
        row["prediction_label"] for row in materialized if row["prediction_label"]
    )
    confusion = {
        truth_label: {prediction_label: 0 for prediction_label in LABELS}
        for truth_label in LABELS
    }
    for row in scored:
        confusion[row["truth_label"]][row["prediction_label"]] += 1
    return {
        "truth_items": sum(bool(row["truth_label"]) for row in materialized),
        "prediction_items": sum(bool(row["prediction_label"]) for row in materialized),
        "scored_items": len(scored),
        "correct_items": correct,
        "incorrect_items": incorrect,
        "score": correct,
        "max_score": len(scored),
        "accuracy": correct / len(scored) if scored else None,
        "truth_only_items": sum(
            row["coverage"] == COVERAGE_TRUTH_ONLY for row in materialized
        ),
        "prediction_only_items": sum(
            row["coverage"] == COVERAGE_PREDICTION_ONLY for row in materialized
        ),
        "truth_label_counts": {label: truth_counts.get(label, 0) for label in LABELS},
        "prediction_label_counts": {
            label: prediction_counts.get(label, 0) for label in LABELS
        },
        "confusion_matrix": confusion,
    }


def _percentage(value: float | None) -> str:
    return "-" if value is None else f"{value:.2%}"


def _markdown_cell(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def _build_markdown_report(summary: dict[str, Any]) -> str:
    totals = summary["totals"]
    lines = [
        "# 稽核结果三分类评估",
        "",
        f"- 原始材料：`{summary['source_directory']}`",
        f"- 稽核结果：`{summary['result_directory']}`",
        f"- 真实标签：`{summary['label_file']}`",
        f"- 标签范围：{summary['label_scope']}",
        (
            f"- 总得分：**{totals['score']} / {totals['max_score']}**，"
            f"准确率 **{_percentage(totals['accuracy'])}**"
        ),
        "",
        "真实标签直接读取 JSON v1 中的三分类枚举；"
        "程序结果只接受 `classification` 三分类字段，不兼容旧二分类汇总。"
        "只有双方都存在的稽核项参与评分。",
        "",
        "## 分系统得分",
        "",
        "| 系统 | 得分 | 满分 | 准确率 | 不一致 | 真值缺程序结果 | 程序结果缺真值 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summary["systems"]:
        stats = item["stats"]
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_cell(item["truth_system"]),
                    str(stats["score"]),
                    str(stats["max_score"]),
                    _percentage(stats["accuracy"]),
                    str(stats["incorrect_items"]),
                    str(stats["truth_only_items"]),
                    str(stats["prediction_only_items"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## 三分类混淆矩阵",
            "",
            "| 真实 \\ 程序 | 通过 | 不通过 | 不适用 |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for truth_label in LABELS:
        matrix = totals["confusion_matrix"][truth_label]
        lines.append(
            f"| {truth_label} | {matrix[LABEL_PASSED]} | {matrix[LABEL_FAILED]} | "
            f"{matrix[LABEL_NOT_APPLICABLE]} |"
        )

    coverage = summary["coverage"]
    lines.extend(
        [
            "",
            "## 覆盖缺口",
            "",
            f"- 真实标签有、程序结果缺失：{len(coverage['truth_only'])} 项次。",
            f"- 程序结果有、真实标签缺失：{len(coverage['prediction_only'])} 项次。",
            "- 以上覆盖缺口均不参与得分。",
            "",
            "## 不一致项",
            "",
        ]
    )
    if not summary["mismatches"]:
        lines.append("无。")
    else:
        lines.extend(
            [
                "| 系统 | 稽核项 | 真实标签 | 程序标签 |",
                "| --- | --- | --- | --- |",
            ]
        )
        for row in summary["mismatches"]:
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(row[key])
                    for key in (
                        "system",
                        "item_id",
                        "truth_label",
                        "prediction_label",
                    )
                )
                + " |"
            )

    diagnostics = summary["diagnostics"]
    lines.extend(["", "## 诊断信息", ""])
    if diagnostics:
        for diagnostic in diagnostics:
            lines.append(
                f"- {_markdown_cell(diagnostic['system'])} / "
                f"{diagnostic['item_id']}：{_markdown_cell(diagnostic['message'])}"
            )
    else:
        lines.append("无。")
    lines.append("")
    return "\n".join(lines)


_CSV_FIELD_MAP = (
    ("system", "系统"),
    ("result_unit", "结果系统目录"),
    ("item_id", "稽核项"),
    ("item_name", "稽核项名称"),
    ("category", "分类"),
    ("coverage", "覆盖状态"),
    ("truth_label", "真实标签"),
    ("prediction_label", "程序标签"),
    ("score", "得分"),
    ("consistent", "是否一致"),
    ("truth_location", "结构化标签位置"),
    ("prediction_status", "程序运行状态"),
    ("prediction_source", "程序标签依据"),
    ("prediction_summary_file", "程序明细汇总"),
    ("diagnostic", "诊断"),
)


def _csv_text(rows: Iterable[dict[str, Any]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=[title for _, title in _CSV_FIELD_MAP])
    writer.writeheader()
    for row in rows:
        serialized: dict[str, Any] = {}
        for key, title in _CSV_FIELD_MAP:
            value = row.get(key)
            if key == "consistent":
                value = "" if value is None else ("是" if value else "否")
            elif value is None:
                value = ""
            serialized[title] = value
        writer.writerow(serialized)
    return "\ufeff" + stream.getvalue()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(text)
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def evaluate_batch_results(
    *,
    source_dir: Path,
    result_dir: Path | None = None,
    label_file: Path | None = None,
) -> dict[str, Any]:
    source = Path(source_dir).expanduser().resolve()
    if not source.is_dir():
        raise FileNotFoundError(f"原始材料目录不存在：{source}")
    requested_labels_path = (
        ground_truth_path_for_source(source)
        if label_file is None
        else Path(label_file).expanduser()
    )
    truth = load_ground_truth_json(requested_labels_path)
    labels_path = requested_labels_path.resolve()
    if truth.source_name != source.name:
        raise ValueError(
            "结构化标签 source_name 与原始材料目录名不一致："
            f"{truth.source_name!r} != {source.name!r}"
        )

    if result_dir is None:
        result_root = (PROJECT_ROOT / "output" / f"{source.name}_全流程结果").resolve(
            strict=False
        )
    else:
        result_root = Path(result_dir).expanduser().resolve()
    if not result_root.is_dir():
        raise FileNotFoundError(f"全流程结果目录不存在：{result_root}")
    if (
        result_root == source
        or source in result_root.parents
        or result_root in source.parents
    ):
        raise ValueError("原始材料目录和评估结果目录不能相同或互相包含")

    flow_summary_path = result_root / "全流程汇总.json"
    flow = _read_json_object(flow_summary_path, "全流程汇总")
    recorded_source = flow.get("source_directory")
    if not isinstance(recorded_source, str) or not recorded_source.strip():
        raise ValueError("全流程汇总缺少 source_directory")
    if Path(recorded_source).expanduser().resolve(strict=False) != source:
        raise ValueError(
            "全流程汇总的原始材料目录与评估输入不一致："
            f"{recorded_source} != {source}"
        )

    audit = flow.get("audit")
    if not isinstance(audit, dict) or not isinstance(audit.get("systems"), list):
        raise ValueError("全流程汇总缺少 audit.systems 数组")
    system_records = audit["systems"]
    if not all(isinstance(record, dict) for record in system_records):
        raise ValueError("全流程汇总 audit.systems 中存在非对象")
    result_units = [record.get("unit") for record in system_records]
    if not all(isinstance(unit, str) for unit in result_units):
        raise ValueError("全流程汇总中存在无效系统名")
    mapping = match_system_names(truth.systems, result_units)
    records_by_unit = {str(record["unit"]): record for record in system_records}

    comparisons: list[dict[str, Any]] = []
    system_summaries: list[dict[str, Any]] = []
    diagnostics: list[dict[str, str]] = []
    for truth_system in truth.systems:
        mapping_record = mapping[truth_system]
        result_unit = mapping_record["result_unit"]
        predictions, system_summary_path = _load_system_predictions(
            result_root, source, records_by_unit[result_unit]
        )
        system_rows: list[dict[str, Any]] = []
        truth_items = truth.labels[truth_system]
        for item_id in truth.item_ids:
            truth_item = truth_items[item_id]
            prediction = predictions.get(item_id)
            if prediction is None:
                row = {
                    "system": truth_system,
                    "result_unit": result_unit,
                    "item_id": item_id,
                    "item_name": truth_item.item_name,
                    "category": truth_item.category,
                    "coverage": COVERAGE_TRUTH_ONLY,
                    "truth_label": truth_item.label,
                    "prediction_label": "",
                    "score": None,
                    "consistent": None,
                    "truth_location": truth_item.source_location,
                    "prediction_status": "",
                    "prediction_source": "",
                    "prediction_summary_file": "",
                    "diagnostic": "程序结果中不存在该稽核项，不评分",
                }
            else:
                consistent = (
                    truth_item.classification == prediction.classification
                )
                row = {
                    "system": truth_system,
                    "result_unit": result_unit,
                    "item_id": item_id,
                    "item_name": truth_item.item_name,
                    "category": truth_item.category,
                    "coverage": COVERAGE_SCORED,
                    "truth_label": truth_item.label,
                    "prediction_label": prediction.label,
                    "score": 1 if consistent else 0,
                    "consistent": consistent,
                    "truth_location": truth_item.source_location,
                    "prediction_status": prediction.status,
                    "prediction_source": prediction.prediction_source,
                    "prediction_summary_file": prediction.summary_file,
                    "diagnostic": prediction.diagnostic,
                }
                if prediction.diagnostic:
                    diagnostics.append(
                        {
                            "system": truth_system,
                            "item_id": item_id,
                            "message": prediction.diagnostic,
                        }
                    )
            system_rows.append(row)

        for item_id in sorted(set(predictions) - set(truth.item_ids)):
            prediction = predictions[item_id]
            row = {
                "system": truth_system,
                "result_unit": result_unit,
                "item_id": item_id,
                "item_name": "",
                "category": "",
                "coverage": COVERAGE_PREDICTION_ONLY,
                "truth_label": "",
                "prediction_label": prediction.label,
                "score": None,
                "consistent": None,
                "truth_location": "",
                "prediction_status": prediction.status,
                "prediction_source": prediction.prediction_source,
                "prediction_summary_file": prediction.summary_file,
                "diagnostic": prediction.diagnostic or "本地真实标签中不存在该稽核项，不评分",
            }
            if prediction.diagnostic:
                diagnostics.append(
                    {
                        "system": truth_system,
                        "item_id": item_id,
                        "message": prediction.diagnostic,
                    }
                )
            system_rows.append(row)

        comparisons.extend(system_rows)
        system_summaries.append(
            {
                **mapping_record,
                "system_summary_file": str(system_summary_path),
                "stats": _stats_for_rows(system_rows),
            }
        )

    totals = _stats_for_rows(comparisons)
    mismatches = [row for row in comparisons if row["score"] == 0]
    truth_only = [
        {"system": row["system"], "item_id": row["item_id"]}
        for row in comparisons
        if row["coverage"] == COVERAGE_TRUTH_ONLY
    ]
    prediction_only = [
        {"system": row["system"], "item_id": row["item_id"]}
        for row in comparisons
        if row["coverage"] == COVERAGE_PREDICTION_ONLY
    ]
    summary: dict[str, Any] = {
        "schema_version": 1,
        "classification": {
            "truth": {
                "format": GROUND_TRUTH_KIND,
                "accepted_values": list(CLASSIFICATION_LABELS),
                "rule": "直接读取 systems.<unit>.<item_id> 枚举值",
            },
            "prediction": {
                "不适用": "classification is not_applicable",
                "通过": "classification is passed",
                "不通过": "classification is failed",
            },
            "scoring": "双方存在且三分类标签相同得 1 分，否则得 0 分",
        },
        "source_directory": str(source),
        "result_directory": str(result_root),
        "flow_summary_file": str(flow_summary_path),
        "label_file": str(labels_path),
        "label_format": f"{GROUND_TRUTH_KIND}/v{GROUND_TRUTH_SCHEMA_VERSION}",
        "label_sha256": hashlib.sha256(labels_path.read_bytes()).hexdigest(),
        "label_scope": truth.scope,
        "execution_mode": flow.get("execution_mode"),
        "excluded_model_items": audit.get("excluded_model_items", []),
        "label_system_count": len(truth.systems),
        "label_item_count_per_system": len(truth.item_ids),
        "systems": system_summaries,
        "totals": totals,
        "coverage": {
            "truth_only": truth_only,
            "prediction_only": prediction_only,
        },
        "mismatches": mismatches,
        "diagnostics": diagnostics,
    }

    requested_evaluation_dir = result_root / "评估结果"
    if requested_evaluation_dir.is_symlink():
        raise ValueError(f"拒绝写入符号链接评估目录：{requested_evaluation_dir}")
    evaluation_dir = requested_evaluation_dir.resolve(strict=False)
    if not _is_within(evaluation_dir, result_root):
        raise ValueError(f"评估输出目录越出全流程结果根目录：{evaluation_dir}")
    mismatch_rows = [row for row in comparisons if row["score"] == 0]
    summary["output_files"] = {
        "summary_json": str(evaluation_dir / "评分汇总.json"),
        "summary_markdown": str(evaluation_dir / "评分汇总.md"),
        "comparisons_csv": str(evaluation_dir / "逐项对比.csv"),
        "mismatches_csv": str(evaluation_dir / "不一致项.csv"),
    }
    _atomic_write_text(
        evaluation_dir / "评分汇总.json",
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write_text(
        evaluation_dir / "评分汇总.md", _build_markdown_report(summary)
    )
    _atomic_write_text(evaluation_dir / "逐项对比.csv", _csv_text(comparisons))
    _atomic_write_text(evaluation_dir / "不一致项.csv", _csv_text(mismatch_rows))
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用固定 JSON v1 真实标签评估 batch-all-source 的逐系统结果"
    )
    parser.add_argument(
        "--source-dir",
        required=True,
        type=Path,
        help="与 batch-all-source 相同的原始材料根目录",
    )
    parser.add_argument(
        "--result-dir",
        type=Path,
        help="可选：全流程结果目录；默认为 output/<原始目录名>_全流程结果",
    )
    parser.add_argument(
        "--label-file",
        type=Path,
        help=(
            "可选：结构化标签 JSON v1；默认使用同级 "
            f"<原始目录名>label/{GROUND_TRUTH_FILENAME}"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = evaluate_batch_results(
            source_dir=args.source_dir,
            result_dir=args.result_dir,
            label_file=args.label_file,
        )
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2

    totals = result["totals"]
    print("三分类评估完成")
    print(f"得分：{totals['score']} / {totals['max_score']}")
    print(f"准确率：{_percentage(totals['accuracy'])}")
    print(f"不一致项：{totals['incorrect_items']}")
    print(f"真值有、程序缺失：{totals['truth_only_items']}")
    print(f"程序有、真值缺失：{totals['prediction_only_items']}")
    print(f"评估结果：{Path(result['result_directory']) / '评估结果'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
