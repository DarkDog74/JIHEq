from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from audit_rules import get_rule_spec
from core.audit_catalog import audit_item_enabled
from extractors import get_extractor_spec, normalize_item_id, select_input_documents
from extractors.common import audit_artifact_scope, portable_artifact_fields


JsonObject = dict[str, Any]


def write_json(path: Path, payload: Mapping[str, Any], *, overwrite: bool = False) -> None:
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"输出文件已存在：{path}；如需覆盖请使用 --overwrite")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def ensure_item_pair(item_id: str) -> str:
    normalized = normalize_item_id(item_id)
    get_extractor_spec(normalized)
    get_rule_spec(normalized)
    if not audit_item_enabled(normalized):
        raise ValueError(f"稽核项已停用：{normalized}")
    return normalized


def extract_fields_from_unit(
    *, item_id: str, input_unit_dir: Path, artifact_dir: Path | None = None
) -> tuple[str, Path | tuple[Path, ...], JsonObject]:
    normalized = ensure_item_pair(item_id)
    extractor_spec = get_extractor_spec(normalized)
    source_documents = select_input_documents(extractor_spec, Path(input_unit_dir))
    extractor_input: Path | tuple[Path, ...] = (
        source_documents[0] if len(source_documents) == 1 else source_documents
    )
    if artifact_dir is None:
        fields = extractor_spec.extractor(extractor_input)
    else:
        with audit_artifact_scope(artifact_dir):
            fields = extractor_spec.extractor(extractor_input)
    return normalized, extractor_input, fields


def _serialize_sources(source: Path | tuple[Path, ...]) -> str | list[str]:
    if isinstance(source, Path):
        return str(source)
    return [str(path) for path in source]


def run_extract(
    *,
    item_id: str,
    input_unit_dir: Path,
    output_path: Path,
    overwrite: bool = False,
) -> JsonObject:
    _, _, fields = extract_fields_from_unit(
        item_id=item_id,
        input_unit_dir=input_unit_dir,
        artifact_dir=Path(output_path).parent,
    )
    write_json(Path(output_path), portable_artifact_fields(fields), overwrite=overwrite)
    return fields


def run_audit(
    *,
    item_id: str,
    extracted_fields: Mapping[str, Any],
    output_path: Path,
    overwrite: bool = False,
) -> JsonObject:
    normalized = ensure_item_pair(item_id)
    result = get_rule_spec(normalized).rule(extracted_fields)
    write_json(Path(output_path), result, overwrite=overwrite)
    return result


def run_pipeline(
    *,
    item_id: str,
    input_unit_dir: Path,
    extracted_output_path: Path,
    audit_output_path: Path,
    overwrite: bool = False,
) -> JsonObject:
    normalized, source_document, fields = extract_fields_from_unit(
        item_id=item_id,
        input_unit_dir=input_unit_dir,
        artifact_dir=Path(extracted_output_path).parent,
    )
    write_json(
        Path(extracted_output_path),
        portable_artifact_fields(fields),
        overwrite=overwrite,
    )
    audit = run_audit(
        item_id=normalized,
        extracted_fields=fields,
        output_path=audit_output_path,
        overwrite=overwrite,
    )
    return {
        "item_id": normalized,
        "source_document": _serialize_sources(source_document),
        "fields": fields,
        "audit": audit,
    }
