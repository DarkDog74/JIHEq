from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from business_parser import ParsedBusiness, parse_business
from image_extractor import RawImageOccurrence, extract_image_occurrences

SHARED_ROOT = Path(__file__).resolve().parents[1]
if str(SHARED_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_ROOT))
from _shared.audit import pdf_audit
from _shared.pdf_backend import backend_for


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _replace_assets(staged_assets: Path, final_assets: Path) -> None:
    if not final_assets.name.endswith(".assets") or final_assets.name == ".assets":
        raise ValueError(f"非法资源目录：{final_assets}")
    if final_assets.is_symlink():
        raise ValueError(f"资源输出位置不能是符号链接：{final_assets}")
    if final_assets.exists():
        if not final_assets.is_dir():
            raise ValueError(f"资源输出位置不是普通目录：{final_assets}")
        shutil.rmtree(final_assets)
    if staged_assets.is_dir() and any(staged_assets.iterdir()):
        shutil.copytree(staged_assets, final_assets)


def _image_field(parsed: ParsedBusiness, occurrence: RawImageOccurrence) -> str:
    return parsed.fields_by_position.get(
        (occurrence.sheet_index, occurrence.row, occurrence.column),
        "佐证材料" if occurrence.column else "",
    )


def _attach_images(
    parsed: ParsedBusiness,
    occurrences: list[RawImageOccurrence],
    staged_assets: Path,
    assets_name: str,
) -> None:
    asset_by_digest: dict[str, tuple[str, str]] = {}
    seen_occurrences: set[tuple[int, int, int, str, str]] = set()

    for occurrence in occurrences:
        digest = hashlib.sha256(occurrence.payload).hexdigest()
        occurrence_key = (
            occurrence.sheet_index,
            occurrence.row,
            occurrence.column,
            occurrence.source,
            digest,
        )
        if occurrence_key in seen_occurrences:
            continue
        seen_occurrences.add(occurrence_key)

        if digest not in asset_by_digest:
            image_number = len(asset_by_digest) + 1
            image_id = f"IMG{image_number:03d}"
            extension = occurrence.extension or ".bin"
            file_name = f"{image_id}{extension}"
            staged_assets.mkdir(parents=True, exist_ok=True)
            (staged_assets / file_name).write_bytes(occurrence.payload)
            asset_by_digest[digest] = (image_id, file_name)
        image_id, file_name = asset_by_digest[digest]

        field = _image_field(parsed, occurrence)
        image: dict[str, Any] = {
            "图片编号": image_id,
            "地址": f"{assets_name}/{file_name}",
            "来源": occurrence.source,
        }
        if field:
            image["所在字段"] = field
        if occurrence.alt_text:
            image["替代文字"] = occurrence.alt_text

        record = parsed.row_records.get((occurrence.sheet_index, occurrence.row))
        if record is not None:
            record.setdefault("图片", []).append(image)
            if field in record and isinstance(record[field], str) and not record[field]:
                record[field] = "有图"
            continue

        other = dict(image)
        if 0 <= occurrence.sheet_index < len(parsed.sheets):
            other["所在工作表"] = parsed.sheets[occurrence.sheet_index].title
        if occurrence.row:
            other["所在行"] = occurrence.row
        if occurrence.column:
            other["所在列"] = occurrence.column
        parsed.result["其他图片"].append(other)


def convert(input_path: str | Path, output_json: str | Path) -> dict[str, Any]:
    source = Path(input_path).expanduser().resolve()
    destination = Path(output_json).expanduser().resolve()
    assets_name = f"{destination.stem}.assets"
    final_assets = destination.parent / assets_name

    with tempfile.TemporaryDirectory(prefix="compliance-images-parser-") as work_dir_name:
        work_dir = Path(work_dir_name)
        pdf_document = None
        pdf_warnings: list[str] = []
        pdf_backend = "light"
        if source.suffix.lower() == ".pdf":
            from pdf_parser import parse_mineru_assessment, parse_pdf_assessment

            pdf_backend = backend_for("符合性评测表")
            pdf_result = (
                parse_mineru_assessment(source, work_dir / "source")
                if pdf_backend == "mineru"
                else parse_pdf_assessment(source, work_dir / "source")
            )
            parsed = pdf_result.business
            occurrences = pdf_result.images
            pdf_document = pdf_result.document
            pdf_warnings = pdf_result.warnings
        else:
            parsed = parse_business(source)
            occurrences = extract_image_occurrences(source, parsed.sheets, work_dir / "source")
        staged_assets = work_dir / "assets"
        _attach_images(parsed, occurrences, staged_assets, assets_name)
        _replace_assets(staged_assets, final_assets)
        _write_json_atomic(destination, parsed.result)
        if pdf_document is not None:
            _write_json_atomic(
                destination.with_suffix(".audit.json"),
                pdf_audit(
                    pdf_document,
                    "compliance-assessment-pdf-1.0.0",
                    pdf_warnings,
                    engine="MinerU pipeline(本地)" if pdf_backend == "mineru" else "pdfplumber+OpenCV+RapidOCR(按需)",
                ),
            )
        return parsed.result


def _image_count(value: dict[str, Any]) -> int:
    count = len(value.get("其他图片", []))
    count += sum(len(item.get("图片", [])) for item in value.get("检查项目", []))
    count += sum(len(item.get("图片", [])) for item in value.get("备份信息", []))
    for records in value.get("其他附表", {}).values():
        count += sum(len(item.get("图片", [])) for item in records)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="解析符合性评测表，并提取、定位全部图片")
    parser.add_argument("input", help="输入 .xls/.xlsx/.pdf 文件")
    parser.add_argument("output", nargs="?", help="输出 JSON；默认与输入文件同名")
    args = parser.parse_args()
    source = Path(args.input)
    destination = Path(args.output) if args.output else source.with_suffix(".json")
    try:
        payload = convert(source, destination)
    except Exception as exc:
        if source.suffix.lower() == ".pdf":
            _write_json_atomic(
                destination.expanduser().resolve().with_suffix(".audit.json"),
                {"status": "error", "解析来源": "pdf", "源文件": str(source.expanduser().resolve()), "错误": str(exc)},
            )
        raise
    print(
        json.dumps(
            {
                "输出文件": str(destination),
                "检查项目数": len(payload["检查项目"]),
                "图片引用数": _image_count(payload),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
