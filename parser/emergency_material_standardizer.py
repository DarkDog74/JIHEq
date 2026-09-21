"""定位应急预案、演练方案和演练报告原件并生成规范 Markdown。"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from .emergency_document_adapter import (
        EmergencyDocumentParseError,
        parse_emergency_drill,
        parse_emergency_plan,
    )
    from .emergency_evidence import (
        extract_drill_date_evidence,
        extract_revision_date_evidence,
    )
except ImportError:
    from emergency_document_adapter import (
        EmergencyDocumentParseError,
        parse_emergency_drill,
        parse_emergency_plan,
    )
    from emergency_evidence import (
        extract_drill_date_evidence,
        extract_revision_date_evidence,
    )


MANIFEST_NAME = "应急材料分析.json"
SUPPORTED_SUFFIXES = frozenset({".doc", ".docx"})
_GENERATED_MARKDOWN_RE = re.compile(
    r"^(?:应急预案|上一份应急预案|演练方案|上一份演练方案|"
    r"应急演练报告|上一份应急演练报告|"
    r"历史应急预案_(?:20\d{2}|\d{4}-\d{2}-\d{2}|未知日期)(?:_[2-9]\d*)?|"
    r"历史演练方案_(?:20\d{2}|\d{4}-\d{2}-\d{2}|未知日期)(?:_[2-9]\d*)?|"
    r"历史应急演练报告_(?:20\d{2}|\d{4}-\d{2}-\d{2}|未知日期)(?:_[2-9]\d*)?|"
    r"应急预案_[2-9]\d*|演练方案_[2-9]\d*|"
    r"应急演练报告_(?:20\d{2}|未知年份)(?:_[2-9]\d*)?)\.md$"
)
_GENERATED_ASSET_RE = re.compile(
    _GENERATED_MARKDOWN_RE.pattern.removesuffix(r"\.md$") + "$"
)


@dataclass(frozen=True)
class EmergencyStandardizationResult:
    manifest_path: Path
    matched_count: int
    success_count: int
    failed_count: int
    records: list[dict[str, Any]]


def _normalized_name(path: Path) -> str:
    name = unicodedata.normalize("NFKC", path.stem).casefold()
    return re.sub(r"[\s\-_\u2014－+()\uff08\uff09\[\]【】]+", "", name)


def classify_emergency_source(path: Path) -> str | None:
    """按受控文件名条件匹配，不把系统名中的“应急”当材料用途。"""

    if (
        not path.is_file()
        or path.suffix.casefold() not in SUPPORTED_SUFFIXES
        or path.name.startswith(("~$", ".~", "."))
    ):
        return None
    name = _normalized_name(path)
    if any(value in name for value in ("风险评估", "符合性", "应急响应报告")):
        return None
    if any(
        value in name
        for value in (
            "演练总结报告",
            "应急演练总结",
            "应急演练报告",
            "应急预案总结",
        )
    ):
        return "应急演练报告"
    if "演练" in name and "方案" in name and "总结" not in name and "报告" not in name:
        return "演练方案"
    if "预案" in name and "总结" not in name and "报告" not in name:
        return "应急预案"
    return None


def discover_emergency_sources(source_dir: Path) -> list[tuple[Path, str]]:
    source_root = Path(source_dir)
    matches: list[tuple[Path, str]] = []
    for path in sorted(source_root.rglob("*"), key=lambda item: str(item)):
        purpose = classify_emergency_source(path)
        if purpose is not None:
            matches.append((path, purpose))
    return matches


def _rank_and_name_records(records: list[dict[str, Any]]) -> dict[int, str]:
    """两类材料各自按业务日期降序排列，保留全部解析结果。"""

    result: dict[int, str] = {}
    for purpose, latest_stem, previous_stem, history_prefix in (
        ("应急预案", "应急预案", "上一份应急预案", "历史应急预案"),
        ("演练方案", "演练方案", "上一份演练方案", "历史演练方案"),
        (
            "应急演练报告",
            "应急演练报告",
            "上一份应急演练报告",
            "历史应急演练报告",
        ),
    ):
        candidates = [
            record
            for record in records
            if record["用途"] == purpose and record["解析状态"] == "success"
        ]
        # 先按源路径固定同日顺序，再按可提取的业务日期倒序。
        candidates.sort(key=lambda record: str(record.get("源文件") or ""))
        candidates.sort(
            key=lambda record: (
                record.get("_sort_start") is not None,
                record.get("_sort_start") or "",
                record.get("_sort_end") or "",
            ),
            reverse=True,
        )
        history_counts: dict[str, int] = {}
        for rank, record in enumerate(candidates, start=1):
            record["排序序号"] = rank
            record["标准角色"] = "最新" if rank == 1 else ("上一份" if rank == 2 else "历史")
            if record.get("_sort_start") is None and len(candidates) > 1:
                record["告警"].append(
                    "未提取到用于排序的业务日期，无法完全确认其新旧次序。"
                )
            if rank == 1:
                stem = latest_stem
            elif rank == 2:
                stem = previous_stem
            else:
                date_label = str(record.get("_sort_start") or "未知日期")
                base = f"{history_prefix}_{date_label}"
                history_counts[base] = history_counts.get(base, 0) + 1
                stem = (
                    base
                    if history_counts[base] == 1
                    else f"{base}_{history_counts[base]}"
                )
            result[id(record)] = stem
    return result


def _replace_asset_prefix(value: str, old_stem: str, new_stem: str) -> str:
    return value.replace(
        (Path("附件") / old_stem).as_posix(),
        (Path("附件") / new_stem).as_posix(),
    )


def _generated_paths(output_dir: Path) -> list[Path]:
    paths = [output_dir / MANIFEST_NAME]
    if output_dir.is_dir():
        paths.extend(
            path
            for path in output_dir.iterdir()
            if path.is_file() and _GENERATED_MARKDOWN_RE.fullmatch(path.name)
        )
    asset_root = output_dir / "附件"
    if asset_root.is_dir() and not asset_root.is_symlink():
        paths.extend(
            path
            for path in asset_root.iterdir()
            if path.is_dir() and _GENERATED_ASSET_RE.fullmatch(path.name)
        )
    return [path for path in paths if path.exists() or path.is_symlink()]


def _commit_bundle(bundle: Path, output_dir: Path, *, overwrite: bool) -> None:
    old_paths = _generated_paths(output_dir)
    if old_paths and not overwrite:
        names = "、".join(path.name for path in old_paths)
        raise FileExistsError(f"应急标准化产物已存在：{names}")
    if output_dir.is_symlink():
        raise ValueError(f"拒绝写入符号链接输出目录：{output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    backup = bundle.parent / "previous-emergency-output"
    moved_new: list[Path] = []
    try:
        for path in old_paths:
            if path.is_symlink():
                raise ValueError(f"拒绝覆盖符号链接产物：{path}")
            relative = path.relative_to(output_dir)
            target = backup / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)

        for source in sorted(bundle.iterdir(), key=lambda item: item.name):
            if source.name == "附件":
                for asset_dir in sorted(source.iterdir(), key=lambda item: item.name):
                    target = output_dir / "附件" / asset_dir.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    asset_dir.rename(target)
                    moved_new.append(target)
                continue
            target = output_dir / source.name
            source.rename(target)
            moved_new.append(target)
    except Exception:
        for path in reversed(moved_new):
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
        if backup.is_dir():
            for path in sorted(backup.rglob("*"), key=lambda item: len(item.parts)):
                if not path.is_file() and not path.is_symlink():
                    continue
                relative = path.relative_to(backup)
                target = output_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                path.rename(target)
        raise


def standardize_emergency_materials(
    source_dir: str | Path,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
    sources: Iterable[tuple[Path, str]] | None = None,
) -> EmergencyStandardizationResult:
    """生成应急预案、演练方案和演练报告规范 Markdown 及唯一清单。"""

    source_root = Path(source_dir).resolve()
    destination = Path(output_dir).resolve()
    matched = list(sources) if sources is not None else discover_emergency_sources(source_root)
    destination.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(
        prefix=".emergency-standardize-",
        dir=destination.parent,
    ) as temporary_name:
        temporary_root = Path(temporary_name)
        raw_root = temporary_root / "raw"
        bundle = temporary_root / "bundle"
        bundle.mkdir()
        records: list[dict[str, Any]] = []

        for index, (source, purpose) in enumerate(matched, start=1):
            raw_stem = f"source-{index}"
            raw_output = raw_root / raw_stem
            record: dict[str, Any] = {
                "源文件": source.relative_to(source_root).as_posix(),
                "用途": purpose,
                "扩展名": source.suffix.casefold(),
                "解析状态": "pending",
                "演练年份": None,
                "材料排序日期": None,
                "排序序号": None,
                "标准角色": None,
                "标准Markdown": None,
                "附件目录": None,
                "附件数量": 0,
                "图片": [],
                "告警": [],
                "错误类型": None,
                "错误原因": None,
                "处理建议": None,
                "技术错误": None,
            }
            try:
                parser = parse_emergency_plan if purpose == "应急预案" else parse_emergency_drill
                result = parser(source, raw_output, stem=raw_stem)
                record["_result"] = result
                record["_raw_stem"] = raw_stem
                if purpose in {"演练方案", "应急演练报告"}:
                    evidence = extract_drill_date_evidence(result.markdown)
                    if evidence is not None:
                        record["演练年份"] = evidence.start_date.year
                        record["演练日期证据"] = evidence.to_dict()
                        record["材料排序日期"] = evidence.start_date.isoformat()
                        record["_sort_start"] = evidence.start_date.isoformat()
                        record["_sort_end"] = evidence.end_date.isoformat()
                    else:
                        record["告警"].append(
                            f"未从{purpose}的演练时间/时间、地点、人员小节提取到演练日期。"
                        )
                else:
                    revisions = extract_revision_date_evidence(result.markdown)
                    if revisions:
                        evidence = max(
                            revisions,
                            key=lambda item: (item.start_date, item.end_date),
                        )
                        record["修订日期证据"] = evidence.to_dict()
                        record["材料排序日期"] = evidence.start_date.isoformat()
                        record["_sort_start"] = evidence.start_date.isoformat()
                        record["_sort_end"] = evidence.end_date.isoformat()
                    else:
                        record["告警"].append(
                            "未从预案管理名录/修订记录表提取到修订日期。"
                        )
                record["解析状态"] = "success"
                record["附件数量"] = result.asset_count
                record["告警"].extend(result.warnings)
            except EmergencyDocumentParseError as error:
                record["解析状态"] = "failed"
                record["错误类型"] = error.failure_type
                record["错误原因"] = error.reason
                record["处理建议"] = error.suggestion
                record["技术错误"] = error.technical_detail
            except Exception as error:
                record["解析状态"] = "failed"
                record["错误类型"] = "unexpected_error"
                record["错误原因"] = f"{type(error).__name__}: {error}"
                record["处理建议"] = "请检查原文件是否能正常打开。"
                record["技术错误"] = record["错误原因"]
            records.append(record)

        successful = [record for record in records if record["解析状态"] == "success"]
        assigned_stems = _rank_and_name_records(records)
        for record in successful:
            result = record.pop("_result")
            raw_stem = str(record.pop("_raw_stem"))
            record.pop("_sort_start", None)
            record.pop("_sort_end", None)
            final_stem = assigned_stems[id(record)]
            markdown = _replace_asset_prefix(result.markdown, raw_stem, final_stem)
            markdown_path = bundle / f"{final_stem}.md"
            markdown_path.write_text(markdown, encoding="utf-8")

            raw_asset_dir = raw_root / raw_stem / "附件" / raw_stem
            final_asset_dir = bundle / "附件" / final_stem
            if raw_asset_dir.is_dir():
                final_asset_dir.parent.mkdir(parents=True, exist_ok=True)
                raw_asset_dir.rename(final_asset_dir)
                record["附件目录"] = (Path("附件") / final_stem).as_posix()

            images: list[dict[str, Any]] = []
            for image in result.images:
                updated = dict(image)
                if isinstance(updated.get("file"), str):
                    updated["file"] = _replace_asset_prefix(
                        str(updated["file"]), raw_stem, final_stem
                    )
                images.append(updated)
            record["图片"] = images
            record["标准Markdown"] = markdown_path.relative_to(bundle).as_posix()

        manifest = {
            "schema_version": "1.0",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "source_directory": str(source_root),
            "supported_formats": [".doc", ".docx"],
            "matched_count": len(records),
            "success_count": len(successful),
            "failed_count": sum(record["解析状态"] == "failed" for record in records),
            "files": records,
        }
        (bundle / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _commit_bundle(bundle, destination, overwrite=overwrite)

    return EmergencyStandardizationResult(
        manifest_path=destination / MANIFEST_NAME,
        matched_count=len(records),
        success_count=len(successful),
        failed_count=sum(record["解析状态"] == "failed" for record in records),
        records=records,
    )
