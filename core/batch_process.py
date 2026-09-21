from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

from audit_rules import get_rule_spec
from extractors import (
    ExtractorNotApplicableError,
    MultipleRequiredDocumentsError,
    RequiredDocumentNotFoundError,
    RevisionHistoryNotFoundError,
    TargetSectionNotFoundError,
    get_extractor_spec,
    select_input_documents,
)
from core.pipeline import ensure_item_pair, write_json
from extractors.common import audit_artifact_scope, portable_artifact_fields


JsonObject = dict[str, Any]
AUDIT_CLASSIFICATIONS = ("passed", "failed", "not_applicable")


def _classify_success(
    fields: JsonObject, rule_result: JsonObject
) -> tuple[str, str]:
    """将成功执行的规则统一为对外三分类。"""
    if rule_result.get("applicable") is False:
        return "not_applicable", "not_applicable"
    if rule_result.get("review_required") is True or fields.get("需人工复核") is True:
        return "failed", "needs_review"
    if bool(rule_result.get("passed")):
        return "passed", "passed"
    reason_code = str(rule_result.get("reason_code") or "rule_failed")
    return "failed", reason_code


def _classification_counts(runs: list[JsonObject]) -> dict[str, int]:
    counts = Counter(str(record["classification"]) for record in runs)
    return {name: counts.get(name, 0) for name in AUDIT_CLASSIFICATIONS}


def _aggregate_classification(counts: dict[str, int], total: int) -> str:
    if counts["failed"]:
        return "failed"
    if total and counts["not_applicable"] == total:
        return "not_applicable"
    return "passed"


def discover_unit_directories(dataset_dir: Path, unit_depth: int = 2) -> list[Path]:
    """按相对测试集根目录的层级发现最小处理单位。"""
    dataset_dir = Path(dataset_dir)
    if not dataset_dir.is_dir():
        raise FileNotFoundError(f"测试集目录不存在：{dataset_dir}")
    if unit_depth < 0:
        raise ValueError("unit_depth 必须大于等于 0")
    if unit_depth == 0:
        return [dataset_dir]

    units = sorted(
        (
            path
            for path in dataset_dir.rglob("*")
            if path.is_dir()
            and len(path.relative_to(dataset_dir).parts) == unit_depth
        ),
        key=lambda path: str(path.relative_to(dataset_dir)),
    )
    if not units:
        raise FileNotFoundError(
            f"在 {dataset_dir} 下没有找到深度为 {unit_depth} 的单位目录"
        )
    return units


def run_batch(
    *,
    dataset_dir: Path,
    item_id: str,
    output_dir: Path,
    unit_depth: int = 2,
    overwrite: bool = False,
    flatten_single_unit_output: bool = False,
) -> JsonObject:
    """逐单位定位目标文档，并分别执行同一稽核项的提取与规则。"""
    dataset_dir = Path(dataset_dir)
    output_dir = Path(output_dir)
    if output_dir.exists() and not overwrite:
        raise FileExistsError(f"批量输出目录已存在：{output_dir}；如需覆盖请使用 --overwrite")

    normalized_item_id = ensure_item_pair(item_id)
    extractor_spec = get_extractor_spec(normalized_item_id)
    rule_spec = get_rule_spec(normalized_item_id)
    unit_directories = discover_unit_directories(dataset_dir, unit_depth)
    if flatten_single_unit_output and unit_depth != 0:
        raise ValueError("flatten_single_unit_output 仅支持 unit_depth=0")
    output_dir.mkdir(parents=True, exist_ok=True)

    runs: list[JsonObject] = []
    for unit_dir in unit_directories:
        relative_unit = unit_dir.relative_to(dataset_dir)
        run_record: JsonObject = {
            "item_id": normalized_item_id,
            "unit": str(relative_unit),
            "unit_directory": str(unit_dir),
        }
        try:
            unit_output_dir = (
                output_dir
                if flatten_single_unit_output
                else output_dir / relative_unit / normalized_item_id
            )
            source_documents = select_input_documents(extractor_spec, unit_dir)
            extractor_input = (
                source_documents[0] if len(source_documents) == 1 else source_documents
            )
            with audit_artifact_scope(unit_output_dir):
                fields = extractor_spec.extractor(extractor_input)
                rule_result = rule_spec.rule(fields)
            classification, reason_code = _classify_success(fields, rule_result)
            persisted_fields = portable_artifact_fields(fields)
            serialized_sources: str | list[str] = (
                str(source_documents[0])
                if len(source_documents) == 1
                else [str(path) for path in source_documents]
            )
            extracted_output = unit_output_dir / f"{normalized_item_id}.json"
            audit_output = unit_output_dir / "稽核结果.json"
            audit_result: JsonObject = {
                "item_id": normalized_item_id,
                "unit": str(relative_unit),
                "source_document": serialized_sources,
                "passed": bool(rule_result.get("passed")),
                "classification": classification,
                "reason_code": reason_code,
                "result": rule_result,
            }
            # 部分提取器会在本次 artifact scope 内先落同名字段文件；
            # output_dir 已在函数入口做过覆盖权限校验，这里只覆盖本次内部产物。
            write_json(extracted_output, persisted_fields, overwrite=True)
            write_json(audit_output, audit_result, overwrite=overwrite)
            calculation = rule_result.get("calculation", {})
            summary_fields = fields
            if normalized_item_id == "DJ-09":
                summary_fields = {
                    "全量资产清单条数": len(fields.get("全量资产清单", [])),
                    "定级报告末尾资产条数": len(
                        fields.get("定级报告末尾资产", [])
                    ),
                }
            elif normalized_item_id == "BEAN-02":
                summary_fields = {
                    "备案IP条数": fields.get("备案IP条数"),
                    "资产清单外网资产IP条数": fields.get(
                        "资产清单外网资产IP条数"
                    ),
                }
            elif normalized_item_id == "FX-07":
                summary_fields = {
                    "风险评估报告资产数量": rule_result.get("risk_asset_count"),
                    "定级报告资产数量": rule_result.get(
                        "grading_report_asset_count"
                    ),
                    "数量一致": rule_result.get("quantity_match"),
                    "IP一致": rule_result.get("ip_match"),
                    "类型一致": rule_result.get("type_match"),
                    "型号一致": rule_result.get("model_match"),
                    "字段不一致数量": rule_result.get("field_mismatch_counts"),
                }
            elif normalized_item_id == "FX-05":
                summary_fields = {
                    "风险评估报告资产数量": rule_result.get("risk_asset_count"),
                    "资产清单记录数": rule_result.get("asset_inventory_count"),
                }
            elif normalized_item_id == "FX-10":
                summary_fields = {
                    "赋值记录数": rule_result.get("checked_rows"),
                    "完整赋值行数": rule_result.get("complete_row_count"),
                    "问题赋值行数": rule_result.get("incomplete_row_count"),
                }
            runs.append(
                {
                    **run_record,
                    "status": "success",
                    "source_document": serialized_sources,
                    "fields": summary_fields,
                    "audit_passed": audit_result["passed"],
                    "classification": classification,
                    "reason_code": reason_code,
                    "applicable": rule_result.get("applicable"),
                    "calculated_k": calculation.get("calculated_k"),
                    "declared_k": rule_result.get("declared_k"),
                    "k_matches": rule_result.get("k_matches"),
                    "level_matches": rule_result.get("level_matches"),
                    "calculated_level": calculation.get("calculated_level"),
                    "declared_level": rule_result.get("declared_level"),
                    "checked_rows": rule_result.get("checked_rows"),
                    "placeholder_count": rule_result.get("placeholder_count"),
                    "invalid_asset_type_count": rule_result.get("invalid_asset_type_count"),
                    "incomplete_row_count": rule_result.get("incomplete_row_count"),
                    "complete_row_count": rule_result.get("complete_row_count"),
                    "invalid_value_count": rule_result.get("invalid_value_count"),
                    "problem_field_counts": rule_result.get("problem_field_counts"),
                    "missing_cell_count": rule_result.get("missing_cell_count"),
                    "risk_report_count": rule_result.get("risk_report_count"),
                    "maximum_years": rule_result.get("maximum_years"),
                    "interval_days": rule_result.get("interval_days"),
                    "delta_months": rule_result.get("delta_months"),
                    "image_count": rule_result.get("image_count"),
                    "checked_image_count": rule_result.get("checked_image_count"),
                    "watermarked_image_count": rule_result.get(
                        "watermarked_image_count"
                    ),
                    "missing_watermark_count": rule_result.get(
                        "missing_watermark_count"
                    ),
                    "uncheckable_image_count": rule_result.get(
                        "uncheckable_image_count"
                    ),
                    "risk_report_record_count": rule_result.get("risk_report_record_count"),
                    "network_type": rule_result.get("network_type"),
                    "filing_network_type": rule_result.get("filing_network_type"),
                    "catalog_match": rule_result.get("catalog_match"),
                    "filing_match": rule_result.get("filing_match"),
                    "match_relation": rule_result.get("match_relation"),
                    "invalid_answer_count": rule_result.get("invalid_answer_count"),
                    "invalid_reason_counts": rule_result.get("invalid_reason_counts"),
                    "required_count": rule_result.get("required_count"),
                    "missing_count": rule_result.get("missing_count"),
                    "checked_report_count": rule_result.get("checked_report_count"),
                    "valid_section_count": rule_result.get("valid_section_count"),
                    "problem_report_count": rule_result.get("problem_report_count"),
                    "complete_report_count": rule_result.get("complete_report_count"),
                    "best_report": rule_result.get("best_report"),
                    "matched_title_count": rule_result.get("matched_title_count"),
                    "missing_title_count": rule_result.get("missing_title_count"),
                    "topology_text_count": rule_result.get("topology_text_count"),
                    "topology_image_count": rule_result.get("topology_image_count"),
                    "parse_error_count": (
                        rule_result.get("parse_error_count")
                        if rule_result.get("parse_error_count") is not None
                        else fields.get("解析失败报告数")
                    ),
                    "risk_asset_count": rule_result.get("risk_asset_count"),
                    "asset_inventory_count": rule_result.get("asset_inventory_count"),
                    "grading_report_asset_count": rule_result.get(
                        "grading_report_asset_count"
                    ),
                    "quantity_difference": rule_result.get("quantity_difference"),
                    "quantity_match": rule_result.get("quantity_match"),
                    "ip_match": rule_result.get("ip_match"),
                    "type_match": rule_result.get("type_match"),
                    "model_match": rule_result.get("model_match"),
                    "field_mismatch_counts": rule_result.get(
                        "field_mismatch_counts"
                    ),
                    "incomplete_record_counts": rule_result.get(
                        "incomplete_record_counts"
                    ),
                    "missing_columns": rule_result.get("missing_columns"),
                    "missing_in_report_count": rule_result.get("missing_in_report_count"),
                    "extra_in_report_count": rule_result.get("extra_in_report_count"),
                    "keyword_presence": rule_result.get("keyword_presence"),
                    "column_presence": rule_result.get("column_presence"),
                    "matched_methods": rule_result.get("matched_methods"),
                    "chapter_presence": rule_result.get("chapter_presence"),
                    "matched_chapter_count": rule_result.get("matched_chapter_count"),
                    "missing_chapters": rule_result.get("missing_chapters"),
                    "scan_archive_count": rule_result.get("scan_archive_count"),
                    "readable_html_archive_count": rule_result.get(
                        "readable_html_archive_count"
                    ),
                    "material_type_presence": rule_result.get("type_presence"),
                    "matched_material_types": rule_result.get("matched_types"),
                    "abnormal_archive_count": rule_result.get(
                        "abnormal_archive_count"
                    ),
                    "keyword_evidence_count": rule_result.get(
                        "keyword_evidence_count"
                    ),
                    "matched_measure_count": rule_result.get(
                        "matched_measure_count"
                    ),
                    "used_report": fields.get("使用报告"),
                    "audit_message": rule_result.get("message"),
                    "audit_issues": rule_result.get("issues", []),
                    "extracted_output": str(extracted_output.relative_to(output_dir)),
                    "audit_output": str(audit_output.relative_to(output_dir)),
                }
            )
        except RequiredDocumentNotFoundError as error:
            runs.append(
                {
                    **run_record,
                    "status": "missing_document",
                    "classification": "failed",
                    "reason_code": "missing_document",
                    "missing_requirement": error.requirement,
                    "message": str(error),
                }
            )
        except MultipleRequiredDocumentsError as error:
            runs.append(
                {
                    **run_record,
                    "status": "multiple_documents",
                    "classification": "failed",
                    "reason_code": "multiple_documents",
                    "message": str(error),
                }
            )
        except RevisionHistoryNotFoundError as error:
            runs.append(
                {
                    **run_record,
                    "status": "missing_revision_history",
                    "classification": "failed",
                    "reason_code": "missing_revision_history",
                    "message": str(error),
                }
            )
        except TargetSectionNotFoundError as error:
            runs.append(
                {
                    **run_record,
                    "status": "missing_target_section",
                    "classification": "failed",
                    "reason_code": "missing_target_section",
                    "message": str(error),
                }
            )
        except ExtractorNotApplicableError as error:
            runs.append(
                {
                    **run_record,
                    "status": "extraction_error",
                    "classification": "failed",
                    "reason_code": "extraction_error",
                    "error": f"{type(error).__name__}: {error}",
                }
            )
        except Exception as error:  # 单个系统失败不应中断整个测试集
            runs.append(
                {
                    **run_record,
                    "status": "execution_error",
                    "classification": "failed",
                    "reason_code": "execution_error",
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    status_counts = Counter(record["status"] for record in runs)
    audit_counts = _classification_counts(runs)
    classification = _aggregate_classification(audit_counts, len(unit_directories))
    # 兼容旧调用方：不适用不是失败，但三分类应以 classification 为准。
    batch_passed = classification != "failed"
    summary: JsonObject = {
        "dataset_directory": str(dataset_dir),
        "item_id": normalized_item_id,
        "document_name_contains": list(extractor_spec.document_name_contains),
        "document_requirements": [
            list(group) for group in extractor_spec.document_requirements
        ],
        "unit_depth": unit_depth,
        "total_units": len(unit_directories),
        "status_counts": dict(sorted(status_counts.items())),
        "audit_counts": audit_counts,
        "classification": classification,
        "batch_passed": batch_passed,
        "runs": runs,
    }
    write_json(output_dir / "批量处理汇总.json", summary, overwrite=overwrite)
    return summary


def format_batch_summary(summary: JsonObject, output_dir: Path) -> str:
    status_counts = summary.get("status_counts", {})
    audit_counts = summary.get("audit_counts", {})
    lines = [
        "=" * 72,
        f"稽核项：{summary.get('item_id', '')}（逐系统单位结果）",
        "=" * 72,
    ]
    item_tag = f"[{summary.get('item_id', '')}]"

    status_labels = {
        "missing_document": "缺少目标文档",
        "multiple_documents": "匹配到多份文档",
        "missing_revision_history": "不存在修订历史",
        "missing_target_section": "不存在目标章节",
        "extraction_error": "提取失败",
        "execution_error": "程序错误",
    }
    for record in summary.get("runs", []):
        unit = record.get("unit", "")
        if record.get("status") == "success":
            calculated_k = record.get("calculated_k")
            calculated_level = record.get("calculated_level")
            declared_level = record.get("declared_level")
            if record.get("classification") == "not_applicable":
                result_label = "⏭️ 不适用"
            elif record.get("classification") == "passed":
                result_label = "✅ 通过"
            else:
                result_label = "❌ 不通过"
            if summary.get("item_id") == "DJ-02":
                details = (
                    f"k={calculated_k} | 声明k={record.get('declared_k')} | "
                    f"k值一致={record.get('k_matches')} | 计算等级=第{calculated_level}级 | "
                    f"声明等级=第{declared_level}级"
                )
            elif summary.get("item_id") == "FX-01":
                details = (
                    f"风险报告={record.get('checked_report_count')}份 | "
                    f"有效评价/结论={record.get('valid_section_count')}份 | "
                    f"包含问题={record.get('problem_report_count')}份"
                )
            elif summary.get("item_id") == "FX-03":
                details = (
                    f"标题={record.get('matched_title_count')}/7 | "
                    f"报告={record.get('best_report')}"
                )
            elif summary.get("item_id") == "FHX-02":
                details = (
                    f"题目={record.get('checked_rows')} | "
                    f"非法答案={record.get('invalid_answer_count')}"
                )
            elif summary.get("item_id") == "ZIC-01":
                details = (
                    f"资产清单行数={record.get('checked_rows')} | "
                    f"模糊占位符={record.get('placeholder_count')}"
                )
            elif summary.get("item_id") == "ZIC-02":
                details = (
                    f"资产类型行数={record.get('checked_rows')} | "
                    f"非标准类型={record.get('invalid_asset_type_count')}"
                )
            elif summary.get("item_id") == "ZIC-03":
                details = (
                    f"资产记录={record.get('checked_rows')} | "
                    f"不完整记录={record.get('incomplete_row_count')} | "
                    f"缺失单元格={record.get('missing_cell_count')}"
                )
            elif summary.get("item_id") == "FX-02":
                if record.get("applicable") is False:
                    details = f"声明等级=第{declared_level}级 | 本项不适用"
                else:
                    details = (
                        f"声明等级=第{declared_level}级 | "
                        f"当前={record.get('fields', {}).get('当前风险评估检验结束日期')} | "
                        f"上一份={record.get('fields', {}).get('上一份风险评估检验结束日期')} | "
                        f"间隔={record.get('interval_days')}天 | "
                        f"上限={record.get('maximum_years')}年"
                    )
            elif summary.get("item_id") == "FX-04":
                details = (
                    f"拓扑图文字={record.get('topology_text_count')} | "
                    f"关联IMAGE={record.get('topology_image_count')} | "
                    f"解析异常副本={record.get('parse_error_count')}"
                )
            elif summary.get("item_id") == "YLBG-05":
                details = (
                    f"图片={record.get('image_count')}张 | "
                    f"有水印={record.get('watermarked_image_count')}张 | "
                    f"缺水印={record.get('missing_watermark_count')}张 | "
                    f"无法检查={record.get('uncheckable_image_count')}张"
                )
            elif summary.get("item_id") == "YLBG-06":
                details = (
                    f"修订月份={record.get('fields', {}).get('最新修订月份')} | "
                    f"演练月份={record.get('fields', {}).get('最新演练月份')} | "
                    f"间隔={record.get('delta_months')}个月"
                )
            elif summary.get("item_id") == "YLP-01":
                details = (
                    f"计划行={record.get('checked_rows')} | "
                    f"已勾选={record.get('complete_row_count')} | "
                    f"未勾选={record.get('incomplete_row_count')}"
                )
            elif summary.get("item_id") == "FX-05":
                details = (
                    f"风险报告数量={record.get('risk_asset_count')} | "
                    f"资产清单记录数={record.get('asset_inventory_count')} | "
                    f"差值={record.get('quantity_difference')}"
                )
            elif summary.get("item_id") == "FX-08":
                column_presence = record.get("column_presence") or {}
                column_details = " | ".join(
                    f"{column}列={'是' if column_presence.get(column) else '否'}"
                    for column in ("保密性", "完整性", "可用性")
                )
                details = (
                    f"{column_details} | 赋值行={record.get('checked_rows')} | "
                    f"完整={record.get('complete_row_count')} | "
                    f"问题行={record.get('incomplete_row_count')}"
                )
            elif summary.get("item_id") == "FX-09":
                column_presence = record.get("column_presence") or {}
                details = " | ".join(
                    f"{column}={'是' if column_presence.get(column) else '否'}"
                    for column in ("威胁来源", "威胁种类", "威胁行为")
                )
            elif summary.get("item_id") == "FX-15":
                matched_methods = record.get("matched_methods") or []
                details = (
                    f"风险计算方法={'、'.join(matched_methods)}"
                    if matched_methods
                    else "风险计算方法=未命中"
                )
            elif summary.get("item_id") == "FX-10":
                details = (
                    f"赋值行={record.get('checked_rows')} | "
                    f"完整={record.get('complete_row_count')} | "
                    f"问题行={record.get('incomplete_row_count')} | "
                    f"非数值={record.get('invalid_value_count')}"
                )
            elif summary.get("item_id") == "FX-12":
                type_presence = record.get("material_type_presence") or {}
                details = (
                    f"ZIP={record.get('scan_archive_count')} | "
                    f"基线={'是' if type_presence.get('基线') else '否'} | "
                    f"漏扫={'是' if type_presence.get('漏扫') else '否'} | "
                    f"渗透测试={'是' if type_presence.get('渗透测试') else '否'}"
                )
            elif summary.get("item_id") == "FHX-05":
                details = f"声明等级=第{declared_level}级"
                if record.get("applicable") is not False:
                    details += (
                        f" | 风险评估报告记录={record.get('risk_report_record_count')}份"
                        f" | 可读取={record.get('risk_report_count')}份"
                    )
            elif summary.get("item_id") == "FHX-03":
                filing_match = record.get("filing_match")
                details = (
                    f"网络类型={record.get('network_type')} | "
                    f"备案类型={record.get('filing_network_type')} | "
                    f"标准匹配={'是' if record.get('catalog_match') else '否'} | "
                    f"备案一致={'是' if filing_match else ('否' if filing_match is False else '未比较')} | "
                    f"匹配方式={record.get('match_relation') or '无'}"
                )
            elif summary.get("item_id") in {"FX-13", "FX-14"}:
                column_presence = record.get("column_presence") or {}
                keyword_presence = record.get("keyword_presence") or {}
                target_keyword = (
                    "保护性安全措施"
                    if summary.get("item_id") == "FX-13"
                    else "预防性安全措施"
                )
                details = (
                    f"类型列={'是' if column_presence.get('类型') else '否'} | "
                    f"{target_keyword}="
                    f"{'是' if keyword_presence.get(target_keyword) else '否'} | "
                    f"命中证据={record.get('keyword_evidence_count')} | "
                    f"报告={record.get('used_report')} | "
                    f"解析异常副本={record.get('parse_error_count')}"
                )
            elif summary.get("item_id") == "FHX-04":
                details = str(record.get("audit_message") or "")
            elif record.get("checked_rows") is not None:
                details = f"修订历史行数={record.get('checked_rows')}"
            elif summary.get("item_id") == "BEAN-01":
                details = (
                    f"必填项={record.get('required_count')} | "
                    f"缺失={record.get('missing_count')}"
                )
            elif summary.get("item_id") == "BEAN-02":
                if record.get("applicable") is False:
                    details = str(record.get("audit_message") or "不适用")
                else:
                    details = (
                        f"备案IP={record.get('fields', {}).get('备案IP条数')}个 | "
                        "资产清单外网资产IP="
                        f"{record.get('fields', {}).get('资产清单外网资产IP条数')}个"
                    )
            elif summary.get("item_id") == "FX-07":
                mismatch_counts = record.get("field_mismatch_counts") or {}
                details = (
                    f"风险报告数量={record.get('risk_asset_count')} | "
                    f"定级报告数量={record.get('grading_report_asset_count')} | "
                    f"数量={'一致' if record.get('quantity_match') else '不一致'} | "
                    f"IP={'一致' if record.get('ip_match') else '不一致'} | "
                    f"类型={'一致' if record.get('type_match') else '不一致'} | "
                    f"型号={'一致' if record.get('model_match') else '不一致'} | "
                    "不一致数(IP/类型/型号)="
                    f"{mismatch_counts.get('IP', 0)}/"
                    f"{mismatch_counts.get('类型', 0)}/"
                    f"{mismatch_counts.get('型号', 0)}"
                )
            elif summary.get("item_id") == "DJ-03" and not record.get(
                "audit_passed", False
            ):
                details = ""
            else:
                details = str(record.get("audit_message") or "稽核完成")
            if record.get("classification") == "failed":
                if summary.get("item_id") == "FHX-02":
                    reason_counts = record.get("invalid_reason_counts") or {}
                    issue_preview = "、".join(
                        f"{reason}×{count}"
                        for reason, count in reason_counts.items()
                    )
                    issues = (
                        f"{issue_preview}（逐题明细见FHX-02.json）"
                        if issue_preview
                        else "稽核规则未通过"
                    )
                elif summary.get("item_id") == "ZIC-02":
                    invalid_types = record.get("fields", {}).get(
                        "不在资产类型划分标准表的类型", []
                    )
                    type_counts = Counter(
                        str(item.get("资产类型") or "（空）")
                        for item in invalid_types
                        if isinstance(item, dict)
                    )
                    issue_preview = "、".join(
                        f"{asset_type}×{count}"
                        for asset_type, count in type_counts.most_common(5)
                    )
                    issues = (
                        f"非标准类型：{issue_preview}（逐行明细见ZIC-02.json）"
                        if issue_preview
                        else "稽核规则未通过"
                    )
                elif summary.get("item_id") == "ZIC-03":
                    incomplete_rows = record.get("fields", {}).get(
                        "不完整资产记录", []
                    )
                    field_counts = Counter(
                        str(field)
                        for item in incomplete_rows
                        if isinstance(item, dict)
                        for field in item.get("缺失字段", [])
                    )
                    issue_preview = "、".join(
                        f"{field}×{count}"
                        for field, count in field_counts.most_common(5)
                    )
                    issues = (
                        f"缺失字段：{issue_preview}（逐行明细见ZIC-03.json）"
                        if issue_preview
                        else "稽核规则未通过"
                    )
                elif summary.get("item_id") == "FX-10":
                    field_counts = record.get("problem_field_counts") or {}
                    issue_preview = "、".join(
                        f"{field}×{count}"
                        for field, count in field_counts.items()
                    )
                    issues = (
                        f"问题字段：{issue_preview}（逐行明细见FX-10.json）"
                        if issue_preview
                        else "；".join(record.get("audit_issues", []))
                        or "稽核规则未通过"
                    )
                else:
                    issues = "；".join(record.get("audit_issues", [])) or "稽核规则未通过"
                details = f"{details} | 原因：{issues}" if details else f"原因：{issues}"
            lines.append(f"{item_tag} {result_label} | {unit} | {details}")
        else:
            label = status_labels.get(record.get("status"), "处理失败")
            error_detail = str(record.get("error") or "").strip()
            missing_requirement = str(
                record.get("missing_requirement") or ""
            ).strip()
            if record.get("status") == "missing_document" and missing_requirement:
                reason = f"缺少目标文档：{missing_requirement}"
            elif error_detail:
                error_type, separator, error_message = error_detail.partition(": ")
                reason = (
                    f"{error_message}（{error_type}）"
                    if separator and error_message
                    else error_detail
                )
            else:
                reason = label
            lines.append(f"{item_tag} ❌ 不通过 | {unit} | 原因：{reason}")

    lines.extend(
        [
            "-" * 72,
            f"单位总数：{summary.get('total_units', 0)}",
            f"通过：{audit_counts.get('passed', 0)}",
            f"不通过（含缺材料/异常）：{audit_counts.get('failed', 0)}",
            f"不适用：{audit_counts.get('not_applicable', 0)}",
            f"缺少目标文档：{status_counts.get('missing_document', 0)}",
            f"匹配到多份文档：{status_counts.get('multiple_documents', 0)}",
            f"不存在修订历史：{status_counts.get('missing_revision_history', 0)}",
            f"不存在目标章节：{status_counts.get('missing_target_section', 0)}",
            f"提取失败：{status_counts.get('extraction_error', 0)}",
            f"程序错误：{status_counts.get('execution_error', 0)}",
            f"汇总文件：{Path(output_dir) / '批量处理汇总.json'}",
            "=" * 72,
        ]
    )
    return "\n".join(lines)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="逐系统单位定位指定名称的 Markdown，并执行配对的提取和稽核规则"
    )
    parser.add_argument("--dataset-dir", required=True, type=Path, help="完整测试集根目录")
    parser.add_argument("--item", required=True, help="稽核项编号，例如 DJ-02")
    parser.add_argument("--output-dir", required=True, type=Path, help="批量输出目录")
    parser.add_argument(
        "--unit-depth",
        default=2,
        type=int,
        help="系统单位相对测试集根目录的层级，默认：2（部门/系统）",
    )
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有输出")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    try:
        result = run_batch(
            dataset_dir=args.dataset_dir,
            item_id=args.item,
            output_dir=args.output_dir,
            unit_depth=args.unit_depth,
            overwrite=args.overwrite,
        )
        print(format_batch_summary(result, args.output_dir))
        if result["status_counts"].get("execution_error", 0):
            return 2
        return 0 if result["batch_passed"] else 1
    except (FileExistsError, FileNotFoundError, KeyError, OSError, ValueError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
