#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate seven material categories and send five eligible ones to MinerU."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from core.path_config import mineru_url as configured_mineru_url

from convert_mineru_results import convert_mineru_results


WORKFLOW_VERSION = "mineru-folder-workflow-1.1.0"
DEFAULT_MINERU_URL = configured_mineru_url()
RESULTS_DIRECTORY = "解析结果"

CATEGORIES = (
    "定级报告",
    "备案信息表",
    "资产清单",
    "三同步报告",
    "符合性评测表",
    "符合性评测证明文件",
    "风险评估报告",
)

MINERU_CATEGORIES = (
    "定级报告",
    "三同步报告",
    "风险评估报告",
)

LOCAL_CATEGORIES = ("备案信息表", "符合性评测证明文件")
PROCESSING_CATEGORIES = (
    "定级报告",
    "备案信息表",
    "三同步报告",
    "符合性评测证明文件",
    "风险评估报告",
)

CHECK_ONLY_CATEGORIES = {"资产清单", "符合性评测表"}

ALLOWED_SUFFIXES = {
    "定级报告": {".pdf"},
    "备案信息表": {".doc", ".docx"},
    "资产清单": {".xls", ".xlsx"},
    "三同步报告": {".pdf", ".jpg", ".jpeg", ".png"},
    "符合性评测表": {".xls", ".xlsx"},
    "符合性评测证明文件": {".doc", ".docx"},
    "风险评估报告": {".pdf"},
}

CANONICAL_UPLOAD_INDEX = {
    category: index for index, category in enumerate(MINERU_CATEGORIES, start=1)
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _is_temporary_or_hidden(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    if RESULTS_DIRECTORY in relative.parts:
        return True
    if any(part.startswith(".") for part in relative.parts):
        return True
    name = path.name
    return name.startswith(("~$", ".~")) or name.endswith((".tmp", ".temp"))


def classify_filename(path: str | Path) -> str | None:
    """Classify by filename only; content and parent directory are not consulted."""
    name = Path(path).name
    compact = re.sub(r"\s+", "", name).casefold()

    # Proof files must win before the assessment-table rule because their names
    # commonly contain "符合性评测表" as well.
    if "符合性" in compact and any(
        keyword in compact for keyword in ("证明文件", "证明材料")
    ):
        return "符合性评测证明文件"
    if "风险评估报告" in compact:
        return "风险评估报告"
    if "定级报告" in compact:
        return "定级报告"
    if "备案信息表" in compact or "备案表" in compact:
        return "备案信息表"
    if "资产清单" in compact:
        return "资产清单"
    if (
        ("符合性评测表" in compact or "符合性测评表" in compact)
        and "证明" not in compact
        and "报告" not in compact
    ):
        return "符合性评测表"
    if (
        "三同步报告" in compact
        or "三同步情况报告" in compact
        or re.search(r"[-－]三同步", compact)
    ):
        return "三同步报告"
    return None


def _file_record(path: Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "路径": str(path),
        "文件名": path.name,
        "格式": path.suffix.lower().lstrip("."),
    }
    try:
        stat = path.stat()
        record["文件大小"] = stat.st_size
        record["SHA256"] = _sha256(path)
        if stat.st_size <= 0:
            record["可读"] = False
            record["错误"] = "文件为空"
        else:
            record["可读"] = True
    except OSError as exc:
        record["可读"] = False
        record["错误"] = str(exc)
    return record


def inspect_materials(input_dir: str | Path) -> tuple[dict[str, Any], dict[str, Path]]:
    """Inspect all seven categories and return JSON-safe details plus selections."""
    root = Path(input_dir).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"输入目录不存在: {root}")

    matches: dict[str, list[Path]] = {category: [] for category in CATEGORIES}
    ignored_files = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _is_temporary_or_hidden(path, root):
            continue
        category = classify_filename(path)
        if category is None:
            ignored_files += 1
            continue
        matches[category].append(path.resolve())

    selected: dict[str, Path] = {}
    category_results: list[dict[str, Any]] = []
    for category in CATEGORIES:
        allowed = ALLOWED_SUFFIXES[category]
        records = [_file_record(path) for path in matches[category]]
        qualified = [
            (path, record)
            for path, record in zip(matches[category], records)
            if path.suffix.lower() in allowed and record.get("可读") is True
        ]
        invalid = [
            record
            for path, record in zip(matches[category], records)
            if path.suffix.lower() not in allowed or record.get("可读") is not True
        ]
        item: dict[str, Any] = {
            "类别": category,
            "允许格式": sorted(suffix.lstrip(".") for suffix in allowed),
            "处理方式": (
                "只检查，不进入MinerU"
                if category in CHECK_ONLY_CATEGORIES
                else (
                    (
                        "备案Schema本地解析，不进入MinerU"
                        if category == "备案信息表"
                        else "编号章节+内容+图片本地解析，不进入MinerU"
                    )
                    if category in LOCAL_CATEGORIES
                    else "进入MinerU"
                )
            ),
            "匹配文件": records,
            "格式不合格文件": invalid,
        }
        if not records:
            item["状态"] = "missing"
            item["错误"] = "未找到匹配关键词的文件"
        elif not qualified:
            item["状态"] = "invalid_format"
            item["错误"] = "已找到关键词，但没有合格格式的可读文件"
        else:
            path, record = qualified[0]
            selected[category] = path
            item["采用文件"] = record
            if len(qualified) > 1:
                item["合格候选"] = [candidate for _, candidate in qualified]
                item["未采用合格候选"] = [candidate for _, candidate in qualified[1:]]
                item["重复候选处理"] = "按完整路径排序采用第一个，其余不处理"
            if category in CHECK_ONLY_CATEGORIES:
                item["状态"] = "checked_only"
            elif category in LOCAL_CATEGORIES:
                item["状态"] = "ready_for_local_parser"
            else:
                item["状态"] = "ready_for_mineru"
        category_results.append(item)

    inspection = {
        "输入目录": str(root),
        "类别数": len(CATEGORIES),
        "未匹配其他文件数": ignored_files,
        "类别检查": category_results,
    }
    return inspection, selected


def _create_run_directory(input_root: Path, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    parent = input_root / RESULTS_DIRECTORY
    parent.mkdir(parents=True, exist_ok=True)
    base_name = f"run_{timestamp}"
    for index in range(1, 1000):
        name = base_name if index == 1 else f"{base_name}_{index:02d}"
        candidate = parent / name
        try:
            candidate.mkdir()
        except FileExistsError:
            continue
        return candidate
    raise RuntimeError("无法创建唯一的运行结果目录")


def _canonical_upload_name(category: str, source: Path) -> str:
    index = CANONICAL_UPLOAD_INDEX[category]
    return f"{index:02d}_{category}{source.suffix.lower()}"


def _call_mineru(
    selected: dict[str, Path],
    destination: Path,
    *,
    mineru_url: str,
    effort: str,
) -> dict[str, Any]:
    """Submit all selected documents in one curl request and publish a valid ZIP."""
    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError("未找到curl，无法调用MinerU HTTP接口")

    with tempfile.TemporaryDirectory(prefix="mineru-upload-") as temporary:
        staging = Path(temporary)
        staged: list[tuple[str, Path, Path]] = []
        for category in MINERU_CATEGORIES:
            source = selected.get(category)
            if source is None:
                continue
            staged_path = staging / _canonical_upload_name(category, source)
            shutil.copy2(source, staged_path)
            staged.append((category, source, staged_path))

        if not staged:
            raise RuntimeError("没有可提交给MinerU的合格文件")

        descriptor, temporary_output = tempfile.mkstemp(
            prefix=".mineru-response-", suffix=".tmp", dir=destination.parent
        )
        os.close(descriptor)
        response_path = Path(temporary_output)
        command = [
            curl,
            "--silent",
            "--show-error",
            "--fail-with-body",
            "--connect-timeout",
            "10",
            "--max-time",
            "3600",
            "--retry",
            "1",
            "--retry-all-errors",
            "-X",
            "POST",
            mineru_url,
        ]
        for _, _, staged_path in staged:
            command.extend(["-F", f"files=@{staged_path}"])
        for key, value in (
            ("backend", "hybrid-engine"),
            ("effort", effort),
            ("parse_method", "auto"),
            ("return_md", "true"),
            ("return_content_list", "true"),
            ("return_images", "true"),
            ("response_format_zip", "true"),
        ):
            command.extend(["-F", f"{key}={value}"])
        command.extend(["-o", str(response_path)])

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=7260,
                check=False,
            )
            if completed.returncode != 0:
                body = ""
                try:
                    body = response_path.read_text(encoding="utf-8", errors="replace")[:4000]
                except OSError:
                    pass
                message = (completed.stderr or completed.stdout or body or "MinerU请求失败").strip()
                if body and body not in message:
                    message = f"{message}; 响应: {body}"
                raise RuntimeError(message)
            if not zipfile.is_zipfile(response_path):
                body = response_path.read_text(encoding="utf-8", errors="replace")[:4000]
                raise RuntimeError(f"MinerU返回内容不是有效ZIP: {body}")
            with zipfile.ZipFile(response_path) as archive:
                content_lists = [
                    name for name in archive.namelist() if name.endswith("_content_list_v2.json")
                ]
            if not content_lists:
                raise RuntimeError("MinerU ZIP中没有 *_content_list_v2.json")
            os.replace(response_path, destination)
        finally:
            if response_path.exists():
                response_path.unlink()

    return {
        "状态": "ok",
        "接口": mineru_url,
        "参数": {
            "backend": "hybrid-engine",
            "effort": effort,
            "parse_method": "auto",
            "return_md": True,
            "return_content_list": True,
            "return_images": True,
            "response_format_zip": True,
        },
        "提交批次": 1,
        "最大请求尝试次数": 2,
        "提交文件数": len(staged),
        "提交文件": [
            {
                "类别": category,
                "原文件": str(source),
                "上传文件名": staged_path.name,
            }
            for category, source, staged_path in staged
        ],
        "原始ZIP": str(destination),
        "content_list_v2数": len(content_lists),
    }


def _selected_for_mineru(selected: dict[str, Path]) -> dict[str, Path]:
    return {
        category: selected[category]
        for category in MINERU_CATEGORIES
        if category in selected
    }


def _all_categories_valid(inspection: dict[str, Any]) -> bool:
    accepted = {"ready_for_mineru", "ready_for_local_parser", "checked_only"}
    return all(item.get("状态") in accepted for item in inspection["类别检查"])


def _conversion_documents_by_name(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item.get("文档名称", ""): item
        for item in summary.get("文档", [])
        if isinstance(item, dict)
    }


def _partial_confirmation_message(
    inspection: dict[str, Any], selected: dict[str, Path]
) -> str:
    category_checks = {
        item["类别"]: item for item in inspection.get("类别检查", [])
    }
    ready = [category for category in PROCESSING_CATEGORIES if category in selected]
    unavailable = [category for category in PROCESSING_CATEGORIES if category not in selected]
    lines = [
        "需要处理的5类材料未全部就绪（备案和证明材料本地解析，其余3类进入MinerU）。",
        f"当前合格：{len(ready)}/5（{'、'.join(ready) if ready else '无'}）",
        "未进入的类别：",
    ]
    for category in unavailable:
        item = category_checks.get(category, {})
        status = item.get("状态", "unknown")
        reason = item.get("错误", "未找到可用文件")
        lines.append(f"- {category}：{status}，{reason}")
    return "\n".join(lines)


def _terminal_confirmation(message: str) -> bool:
    print(message)
    try:
        answer = input("是否继续处理已合格的文件？输入 y 继续，其他输入取消：")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().casefold() == "y"


def _assume_yes_confirmation(message: str) -> bool:
    print(message)
    print("已通过 --yes 自动确认继续。")
    return True


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _convert_filing_form(source: Path, output_dir: Path) -> dict[str, Any]:
    """Run the existing fixed-Schema filing parser in an isolated process."""
    converter = Path(__file__).resolve().parent / "备案信息表" / "convert.py"
    destination = output_dir / "备案信息表.json"
    audit_path = output_dir / "备案信息表.audit.json"
    output_dir.mkdir(parents=True, exist_ok=True)
    if not converter.is_file():
        error = f"缺少备案信息表解析器: {converter}"
        _write_json_atomic(audit_path, {"status": "error", "错误": error})
        return {
            "文档名称": "备案信息表",
            "JSON": "",
            "审计": str(audit_path),
            "资源目录": "",
            "状态": "error",
            "错误": error,
            "解析方式": "备案固定Schema本地解析",
        }

    command = [sys.executable, str(converter), str(source), str(destination)]
    try:
        completed = subprocess.run(
            command,
            cwd=converter.parent,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
            env=os.environ.copy(),
        )
    except Exception as exc:
        completed = None
        error = f"备案解析进程异常: {exc}"
    else:
        error = "" if completed.returncode == 0 and destination.is_file() else (
            completed.stderr or completed.stdout or "备案解析器未生成主JSON"
        ).strip()

    audit = _read_json_object(audit_path)
    if error:
        if not audit:
            audit = {
                "status": "error",
                "source": {"path": str(source)},
                "issues": [{"code": "WORKFLOW_FILING_ERROR", "message": error}],
            }
            _write_json_atomic(audit_path, audit)
        return {
            "文档名称": "备案信息表",
            "JSON": "",
            "审计": str(audit_path),
            "资源目录": "",
            "状态": "error",
            "错误": error,
            "解析方式": "备案固定Schema本地解析",
        }

    payload = _read_json_object(destination)
    status = str(audit.get("status") or "ok")
    return {
        "文档名称": "备案信息表",
        "JSON": str(destination),
        "审计": str(audit_path),
        "资源目录": "",
        "状态": status,
        "统计": {"顶层字段数": len(payload)},
        "解析方式": "备案固定Schema本地解析",
        "输入格式": source.suffix.lower().lstrip("."),
    }


def _proof_content_statistics(payload: dict[str, Any]) -> dict[str, int]:
    counts = {"章节": 0, "段落": 0, "图片": 0, "表格": 0}

    def visit(items: Any) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = item.get("类型")
            if kind in counts:
                counts[kind] += 1
            if kind == "章节":
                visit(item.get("内容"))

    visit(payload.get("前置内容"))
    visit(payload.get("章节"))
    return counts


def _convert_proof_material(source: Path, output_dir: Path) -> dict[str, Any]:
    """Parse Word proof material into audit-code sections with text/images."""
    converter = Path(__file__).resolve().parent / "符合性评测表证明材料" / "convert.py"
    destination = output_dir / "符合性评测证明文件.json"
    audit_path = output_dir / "符合性评测证明文件.audit.json"
    assets_dir = output_dir / "符合性评测证明文件.assets"
    output_dir.mkdir(parents=True, exist_ok=True)
    if not converter.is_file():
        error = f"缺少符合性评测证明材料解析器: {converter}"
        _write_json_atomic(audit_path, {"status": "error", "错误": error})
        return {
            "文档名称": "符合性评测证明文件",
            "JSON": "",
            "审计": str(audit_path),
            "资源目录": "",
            "状态": "error",
            "错误": error,
            "解析方式": "Word编号章节+内容+图片本地解析",
        }

    try:
        completed = subprocess.run(
            [sys.executable, str(converter), str(source), str(destination)],
            cwd=converter.parent,
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
            env=os.environ.copy(),
        )
    except Exception as exc:
        completed = None
        error = f"证明材料解析进程异常: {exc}"
    else:
        error = "" if completed.returncode == 0 and destination.is_file() else (
            completed.stderr or completed.stdout or "证明材料解析器未生成主JSON"
        ).strip()

    if error:
        audit = _read_json_object(audit_path)
        if not audit:
            audit = {
                "status": "error",
                "source": {"path": str(source)},
                "issues": [{"code": "WORKFLOW_PROOF_ERROR", "message": error}],
            }
            _write_json_atomic(audit_path, audit)
        return {
            "文档名称": "符合性评测证明文件",
            "JSON": "",
            "审计": str(audit_path),
            "资源目录": str(assets_dir) if assets_dir.is_dir() else "",
            "状态": "error",
            "错误": error,
            "解析方式": "Word编号章节+内容+图片本地解析",
        }

    payload = _read_json_object(destination)
    statistics = _proof_content_statistics(payload)
    audit = {
        "status": "ok",
        "parser": "compliance-proof-word-audit-sections",
        "source": {"path": str(source), "format": source.suffix.lower().lstrip(".")},
        "statistics": statistics,
        "rules": {
            "section_heading": "XX-XX-XX-XX-XX编号或编号组合",
            "section_content": "编号后说明文字与按原文顺序提取的图片",
            "mineru": False,
        },
    }
    _write_json_atomic(audit_path, audit)
    return {
        "文档名称": "符合性评测证明文件",
        "JSON": str(destination),
        "审计": str(audit_path),
        "资源目录": str(assets_dir),
        "状态": "ok",
        "统计": statistics,
        "解析方式": "Word编号章节+内容+图片本地解析",
        "输入格式": source.suffix.lower().lstrip("."),
    }


def run_workflow(
    input_dir: str | Path,
    mineru_url: str = DEFAULT_MINERU_URL,
    effort: str = "medium",
    dry_run: bool = False,
    confirm_partial: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    input_root = Path(input_dir).expanduser().resolve()
    if not input_root.is_dir():
        raise ValueError(f"输入目录不存在: {input_root}")
    if effort not in {"low", "medium", "high"}:
        raise ValueError("effort只能是low、medium或high")

    inspection, selected = inspect_materials(input_root)
    run_dir = _create_run_directory(input_root)
    manifest_path = run_dir / "manifest.json"
    mineru_selected = _selected_for_mineru(selected)
    processing_selected = {
        category: selected[category]
        for category in PROCESSING_CATEGORIES
        if category in selected
    }
    manifest: dict[str, Any] = {
        "工作流版本": WORKFLOW_VERSION,
        "运行模式": "dry_run" if dry_run else "execute",
        "输入目录": str(input_root),
        "输出目录": str(run_dir),
        "总体状态": "error",
        "文件检查": inspection,
        "待处理类别": list(processing_selected),
        "待处理文件数": len(processing_selected),
        "MinerU待处理类别": list(mineru_selected),
        "MinerU待处理文件数": len(mineru_selected),
        "备案信息表解析": {"状态": "not_started"},
        "符合性评测证明文件解析": {"状态": "not_started"},
        "MinerU": {"状态": "not_started"},
        "通用解析": {"状态": "not_started"},
        "继续确认": {
            "需要确认": 0 < len(processing_selected) < len(PROCESSING_CATEGORIES),
            "当前合格数": len(processing_selected),
            "要求数": len(PROCESSING_CATEGORIES),
            "结果": "not_started",
        },
    }

    if dry_run:
        if _all_categories_valid(inspection):
            manifest["总体状态"] = "ok"
        elif processing_selected or any(category in selected for category in CHECK_ONLY_CATEGORIES):
            manifest["总体状态"] = "partial_success"
        manifest["备案信息表解析"]["状态"] = "skipped_dry_run"
        manifest["符合性评测证明文件解析"]["状态"] = "skipped_dry_run"
        manifest["MinerU"]["状态"] = "skipped_dry_run"
        manifest["通用解析"]["状态"] = "skipped_dry_run"
        manifest["继续确认"]["结果"] = "skipped_dry_run"
        _write_json_atomic(manifest_path, manifest)
        return manifest

    if not processing_selected:
        manifest["备案信息表解析"] = {
            "状态": "error",
            "错误": "没有可处理的备案Word文件",
        }
        manifest["符合性评测证明文件解析"] = {
            "状态": "error",
            "错误": "没有可处理的证明材料Word文件",
        }
        manifest["MinerU"] = {"状态": "error", "错误": "没有可提交的合格文件"}
        _write_json_atomic(manifest_path, manifest)
        return manifest

    if len(processing_selected) < len(PROCESSING_CATEGORIES):
        message = _partial_confirmation_message(inspection, processing_selected)
        manifest["继续确认"]["说明"] = message
        if confirm_partial is not None:
            manifest["继续确认"]["结果"] = "pending"
            _write_json_atomic(manifest_path, manifest)
            confirmed = bool(confirm_partial(message))
            manifest["继续确认"]["结果"] = "confirmed" if confirmed else "cancelled"
            if not confirmed:
                manifest["MinerU"] = {
                    "状态": "cancelled_by_user",
                    "说明": "当前合格材料不足5类，用户未输入y，已取消整次处理",
                }
                manifest["备案信息表解析"] = {
                    "状态": "cancelled_by_user",
                    "说明": "未开始备案本地解析",
                }
                manifest["符合性评测证明文件解析"] = {
                    "状态": "cancelled_by_user",
                    "说明": "未开始证明材料本地解析",
                }
                _write_json_atomic(manifest_path, manifest)
                return manifest
        else:
            # Programmatic callers keep the original API behaviour. The CLI
            # always supplies either an interactive or --yes callback.
            manifest["继续确认"]["结果"] = "not_requested_by_api"
    else:
        manifest["继续确认"]["结果"] = "not_required"

    output_dir = run_dir / "通用JSON"
    output_by_category: dict[str, dict[str, Any]] = {}
    filing_source = selected.get("备案信息表")
    if filing_source is not None:
        filing_result = _convert_filing_form(filing_source, output_dir)
        manifest["备案信息表解析"] = filing_result
        output_by_category["备案信息表"] = {
            "类别": "备案信息表",
            **filing_result,
        }
    else:
        manifest["备案信息表解析"] = {
            "状态": "skipped_no_eligible_word",
            "说明": "没有合格的.doc/.docx备案信息表",
        }

    proof_source = selected.get("符合性评测证明文件")
    if proof_source is not None:
        proof_result = _convert_proof_material(proof_source, output_dir)
        manifest["符合性评测证明文件解析"] = proof_result
        output_by_category["符合性评测证明文件"] = {
            "类别": "符合性评测证明文件",
            **proof_result,
        }
    else:
        manifest["符合性评测证明文件解析"] = {
            "状态": "skipped_no_eligible_word",
            "说明": "没有合格的.doc/.docx证明材料",
        }

    generic_conversion: dict[str, Any] = {"状态": "not_started", "文档": []}
    raw_zip = run_dir / "mineru_result.zip"
    if mineru_selected:
        try:
            manifest["MinerU"] = _call_mineru(
                mineru_selected,
                raw_zip,
                mineru_url=mineru_url,
                effort=effort,
            )
        except Exception as exc:
            manifest["MinerU"] = {
                "状态": "error",
                "接口": mineru_url,
                "错误": str(exc),
            }
            generic_conversion = {"状态": "error", "错误": str(exc), "文档": []}
        else:
            try:
                generic_conversion = convert_mineru_results(raw_zip, output_dir)
            except Exception as exc:
                generic_conversion = {"状态": "error", "错误": str(exc), "文档": []}
    else:
        manifest["MinerU"] = {
            "状态": "skipped_no_eligible_files",
            "说明": "没有合格的MinerU类别，仅执行已合格的Word本地解析",
        }
        generic_conversion = {"状态": "skipped", "文档": []}

    by_name = _conversion_documents_by_name(generic_conversion)
    for category in MINERU_CATEGORIES:
        if category not in mineru_selected:
            continue
        converted = by_name.get(category)
        if converted is None:
            output_by_category[category] = {
                "类别": category,
                "状态": "error",
                "错误": "MinerU返回中缺少该文档的content_list_v2或转换记录",
            }
        else:
            output_by_category[category] = {"类别": category, **converted}

    category_outputs = [
        output_by_category[category]
        for category in PROCESSING_CATEGORIES
        if category in output_by_category
    ]
    generated_json_count = sum(
        bool(item.get("JSON") and Path(item["JSON"]).is_file())
        for item in category_outputs
    )
    processed_ok = (
        len(category_outputs) == len(processing_selected)
        and generated_json_count == len(processing_selected)
        and all(item.get("状态") == "ok" for item in category_outputs)
    )
    aggregate_status = "ok" if processed_ok else ("needs_review" if generated_json_count else "error")
    aggregate_summary = {
        "状态": aggregate_status,
        "文档数量": len(category_outputs),
        "成功文档数": generated_json_count,
        "失败文档数": len(category_outputs) - generated_json_count,
        "说明": "备案信息表和符合性评测证明文件使用Word本地解析，定级、三同步和风险报告使用MinerU通用解析",
        "文档": category_outputs,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "转换汇总.json"
    _write_json_atomic(summary_path, aggregate_summary)
    manifest["通用解析"] = {
        "状态": aggregate_status,
        "输出目录": str(output_dir),
        "生成JSON数": generated_json_count,
        "期望JSON数": len(processing_selected),
        "类别结果": category_outputs,
        "转换汇总": str(summary_path),
    }

    if _all_categories_valid(inspection) and processed_ok:
        manifest["总体状态"] = "ok"
    elif generated_json_count > 0:
        manifest["总体状态"] = "partial_success"
    _write_json_atomic(manifest_path, manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="检查七类材料：备案和证明材料本地解析，定级、三同步和风险报告送入MinerU"
    )
    parser.add_argument("input", help="最低级业务文件夹")
    parser.add_argument("--mineru-url", default=DEFAULT_MINERU_URL, help="MinerU HTTP接口")
    parser.add_argument(
        "--effort", choices=("low", "medium", "high"), default="medium", help="MinerU effort"
    )
    parser.add_argument("--dry-run", action="store_true", help="只检查和选择文件，不调用MinerU")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="不足5类时仍自动继续，用于无人值守运行",
    )
    args = parser.parse_args(argv)
    try:
        manifest = run_workflow(
            args.input,
            mineru_url=args.mineru_url,
            effort=args.effort,
            dry_run=args.dry_run,
            confirm_partial=(
                _assume_yes_confirmation if args.yes else _terminal_confirmation
            ),
        )
    except Exception as exc:
        print(f"工作流失败: {exc}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "总体状态": manifest["总体状态"],
                "输出目录": manifest["输出目录"],
                "待处理文件数": manifest["待处理文件数"],
                "MinerU待处理文件数": manifest["MinerU待处理文件数"],
            },
            ensure_ascii=False,
        )
    )
    if manifest["总体状态"] == "ok":
        return 0
    if manifest["总体状态"] == "partial_success":
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
