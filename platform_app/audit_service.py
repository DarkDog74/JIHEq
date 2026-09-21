from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Callable

from core.audit_catalog import DISABLED_ITEMS, audit_item_name
from audit_rules import get_rule_spec, list_rule_items
from extractors import (
    ExtractorNotApplicableError,
    MultipleRequiredDocumentsError,
    RequiredDocumentNotFoundError,
    RevisionHistoryNotFoundError,
    TargetSectionNotFoundError,
    get_extractor_spec,
    list_extractor_items,
    select_input_documents,
)
from extractors.common import audit_artifact_scope, portable_artifact_fields
from core.pipeline import write_json


EventCallback = Callable[[dict[str, object]], None]
ItemReadyCallback = Callable[[dict[str, object]], dict[str, object] | None]
PUBLIC_AUDIT_STATUSES = {"passed", "failed", "not_applicable"}


def normalize_audit_summary(summary: dict[str, object]) -> dict[str, object]:
    """Expose exactly three audit outcomes while preserving the detailed reason."""
    payload = deepcopy(summary)
    audit = payload.get("audit")
    items = audit.get("items", []) if isinstance(audit, dict) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        current = str(item.get("status") or "execution_error")
        if current not in PUBLIC_AUDIT_STATUSES:
            item.setdefault("reason_code", current)
            item["status"] = "failed"
    if isinstance(audit, dict):
        audit["counts"] = dict(
            sorted(
                Counter(
                    str(item.get("status"))
                    for item in items
                    if isinstance(item, dict)
                ).items()
            )
        )
    return payload


def _preview_value(value: object) -> str:
    if value is None or value == "":
        return "未提取"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float, str)):
        text = str(value).replace("\n", " ").strip()
        return text if len(text) <= 96 else f"{text[:93]}…"
    if isinstance(value, list):
        if not value:
            return "0 条"
        if len(value) <= 3 and all(isinstance(item, (str, int, float, bool)) for item in value):
            text = "、".join(str(item) for item in value)
            return text if len(text) <= 96 else f"{text[:93]}…"
        return f"{len(value)} 条记录（完整内容见详情）"
    if isinstance(value, dict):
        simple = [
            f"{key}={nested}"
            for key, nested in value.items()
            if isinstance(nested, (str, int, float, bool)) and nested not in ("", None)
        ]
        if simple:
            text = "；".join(simple)
            return text if len(text) <= 96 else f"{text[:93]}…"
        return f"{len(value)} 个字段（完整内容见详情）"
    return str(value)


def _field_summary(fields: dict[str, object]) -> list[dict[str, str]]:
    """Create a small, predictable list-column preview without duplicating large tables."""
    return [
        {"field": str(name), "value": _preview_value(value)}
        for name, value in fields.items()
    ]


def paired_item_ids() -> list[str]:
    return sorted(
        (set(list_extractor_items()) & set(list_rule_items())) - set(DISABLED_ITEMS)
    )


def _portable_path(path: Path, run_root: Path) -> str:
    """Store result-local paths without leaking the disposable staging directory."""
    try:
        return str(path.resolve().relative_to(run_root.resolve()))
    except ValueError:
        return str(path)


def _serialize_sources(paths: tuple[Path, ...], run_root: Path) -> list[str]:
    return [_portable_path(path, run_root) for path in paths]


def _portable_message(message: str, run_root: Path) -> str:
    root = str(run_root.resolve())
    return message.replace(f"{root}/", "").replace(root, ".")


def _message(rule_result: dict[str, object], fallback: str) -> str:
    return str(rule_result.get("message") or rule_result.get("conclusion") or fallback)


def _normalize_success(
    *,
    item_id: str,
    fields: dict[str, object],
    rule_result: dict[str, object],
    sources: tuple[Path, ...],
    fields_file: Path,
    result_file: Path,
    output_root: Path,
    run_root: Path,
) -> dict[str, object]:
    if rule_result.get("review_required") is True or fields.get("需人工复核") is True:
        status = "failed"
        reason_code = "needs_review"
    elif rule_result.get("applicable") is False:
        status = "not_applicable"
        reason_code = "not_applicable"
    elif bool(rule_result.get("passed")):
        status = "passed"
        reason_code = "passed"
    else:
        status = "failed"
        reason_code = "rule_failed"
    issues = rule_result.get("issues", [])
    if not isinstance(issues, list):
        issues = [str(issues)]
    return {
        "item_id": item_id,
        "item_name": audit_item_name(item_id),
        "status": status,
        "reason_code": reason_code,
        "message": _message(
            rule_result,
            "通过"
            if status == "passed"
            else "不适用"
            if status == "not_applicable"
            else "未通过",
        ),
        "source_documents": _serialize_sources(sources, run_root),
        "issues": issues,
        "extracted_summary": _field_summary(fields),
        "fields_file": str(fields_file.relative_to(output_root)),
        "result_file": str(result_file.relative_to(output_root)),
    }


def _load_failed_parse_records(parsed_dir: Path) -> list[dict[str, object]]:
    path = parsed_dir / "文件分析.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    files = payload.get("files", []) if isinstance(payload, dict) else []
    return [
        item for item in files
        if isinstance(item, dict) and item.get("status") == "failed"
    ]


def _is_blocked(requirement: str, failed_records: list[dict[str, object]]) -> bool:
    wanted = requirement.replace(".md", "").replace(".json", "").casefold()
    for record in failed_records:
        category = str(record.get("category_name") or "").casefold()
        source = str(record.get("source") or "").casefold()
        if category and (category in wanted or wanted in category):
            return True
        if wanted and wanted in source:
            return True
    return False


def run_unit_audits(
    parsed_dir: Path,
    output_dir: Path,
    on_event: EventCallback | None = None,
    on_item_ready: ItemReadyCallback | None = None,
) -> dict[str, object]:
    parsed = Path(parsed_dir).resolve()
    output = Path(output_dir).resolve()
    if not parsed.is_dir():
        raise FileNotFoundError(f"单系统解析结果不存在：{parsed}")
    output.mkdir(parents=True, exist_ok=True)
    items = paired_item_ids()
    failed_records = _load_failed_parse_records(parsed)
    results: list[dict[str, object]] = []

    for index, item_id in enumerate(items, start=1):
        item_name = audit_item_name(item_id)
        if on_event:
            on_event(
                {
                    "type": "audit_started",
                    "stage": "auditing",
                    "item_id": item_id,
                    "item_name": item_name,
                    "current": index,
                    "total": len(items),
                    "message": f"正在执行 {item_id}",
                }
            )
        item_dir = output / item_id
        fields_file = item_dir / f"{item_id}.json"
        result_file = item_dir / "稽核结果.json"
        persisted_fields: dict[str, object] = {}
        rule_result: dict[str, object] = {}
        try:
            extractor_spec = get_extractor_spec(item_id)
            sources = select_input_documents(extractor_spec, parsed)
            extractor_input: Path | tuple[Path, ...] = sources[0] if len(sources) == 1 else sources
            with audit_artifact_scope(item_dir):
                fields = extractor_spec.extractor(extractor_input)
                rule_result = get_rule_spec(item_id).rule(fields)
            persisted_fields = portable_artifact_fields(fields)
            write_json(fields_file, persisted_fields, overwrite=True)
            write_json(
                result_file,
                {
                    "item_id": item_id,
                    "item_name": item_name,
                    "source_document": _serialize_sources(sources, parsed.parent),
                    "passed": bool(rule_result.get("passed")),
                    "result": rule_result,
                },
                overwrite=True,
            )
            normalized = _normalize_success(
                item_id=item_id,
                fields=fields,
                rule_result=rule_result,
                sources=sources,
                fields_file=fields_file,
                result_file=result_file,
                output_root=output.parent,
                run_root=parsed.parent,
            )
        except RequiredDocumentNotFoundError as error:
            reason_code = (
                "blocked_by_parse_error"
                if _is_blocked(error.requirement, failed_records)
                else "missing_document"
            )
            message = _portable_message(str(error), parsed.parent)
            normalized = {
                "item_id": item_id,
                "item_name": item_name,
                "status": "failed",
                "reason_code": reason_code,
                "message": message,
                "source_documents": [],
                "issues": [message],
                "extracted_summary": [],
                "fields_file": None,
                "result_file": None,
            }
        except MultipleRequiredDocumentsError as error:
            normalized = _exception_result(item_id, "multiple_documents", error, parsed.parent)
        except RevisionHistoryNotFoundError as error:
            normalized = _exception_result(item_id, "missing_revision_history", error, parsed.parent)
        except TargetSectionNotFoundError as error:
            normalized = _exception_result(item_id, "missing_target_section", error, parsed.parent)
        except ExtractorNotApplicableError as error:
            normalized = _exception_result(item_id, "extraction_error", error, parsed.parent)
        except Exception as error:
            normalized = _exception_result(item_id, "execution_error", error, parsed.parent)

        # A completed item is not published until its LLM step has returned. The
        # callback is deliberately outside the rule/extractor try block so even
        # missing-document and extraction-error outcomes go through the model.
        narration: dict[str, object] | None = None
        if on_item_ready is not None:
            if not rule_result:
                rule_result = {
                    "passed": False,
                    "message": normalized["message"],
                    "issues": deepcopy(normalized.get("issues", [])),
                    "reason_code": normalized.get("reason_code"),
                }
            try:
                narration = on_item_ready(
                    {
                        "item_id": item_id,
                        "item_name": normalized["item_name"],
                        "current": index,
                        "total": len(items),
                        "fields": deepcopy(persisted_fields),
                        "rule_result": deepcopy(rule_result),
                        "normalized": deepcopy(normalized),
                        "item_dir": item_dir,
                        "fields_file": fields_file if fields_file.is_file() else None,
                        "result_file": result_file if result_file.is_file() else None,
                    }
                )
                if narration is not None and not isinstance(narration, dict):
                    raise TypeError("AI回调必须返回字典或None")
            except Exception:
                narration = {
                    "status": "failed",
                    "text": "AI说明生成失败：AI回调内部异常",
                    "error_code": "callback_error",
                    "error_message": "AI回调内部异常",
                    "generated_at": datetime.now().astimezone().isoformat(
                        timespec="seconds"
                    ),
                    "cached": False,
                    "usage": {},
                }
                try:
                    write_json(item_dir / "AI复述.json", narration, overwrite=True)
                except Exception:
                    pass
            if isinstance(narration, dict):
                # AI metadata is an attachment only; fixed rule status/reason stay unchanged.
                normalized["ai_narration"] = deepcopy(narration)

        results.append(normalized)
        if on_event:
            on_event(
                {
                    "type": "audit_complete",
                    "stage": "auditing",
                    "item_id": item_id,
                    "item_name": normalized["item_name"],
                    "status": normalized["status"],
                    "reason_code": normalized.get("reason_code"),
                    "ai_narration": deepcopy(narration),
                    "current": index,
                    "total": len(items),
                    "message": normalized["message"],
                }
            )

    counts = dict(sorted(Counter(str(item["status"]) for item in results).items()))
    return {"item_count": len(items), "counts": counts, "items": results}


def _exception_result(
    item_id: str,
    reason_code: str,
    error: Exception,
    run_root: Path,
) -> dict[str, object]:
    message = _portable_message(str(error), run_root)
    return {
        "item_id": item_id,
        "item_name": audit_item_name(item_id),
        "status": "failed",
        "reason_code": reason_code,
        "message": message,
        "source_documents": [],
        "issues": [f"{type(error).__name__}: {message}"],
        "extracted_summary": [],
        "fields_file": None,
        "result_file": None,
    }
