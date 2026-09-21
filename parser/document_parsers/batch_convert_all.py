#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Seven-category native-first, PDF-fallback batch converter.

Usage:
    python batch_convert_all.py 智能体案例 统一解析结果

The script does not import category modules into the same interpreter because
several historical parsers intentionally use the same local module names. Each
selected logical document is converted in an isolated subprocess instead.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PARSER_VERSION = "unified-native-first-1.0.0"

CATEGORIES = (
    "备案信息表",
    "定级报告",
    "资产清单",
    "符合性评测表证明材料",
    "符合性评测表",
    "三同步报告",
    "风险评估报告",
)

WORD_CATEGORIES = {
    "备案信息表",
    "定级报告",
    "符合性评测表证明材料",
    "三同步报告",
    "风险评估报告",
}
EXCEL_CATEGORIES = {"资产清单", "符合性评测表"}

CONVERTERS = {
    category: Path(__file__).resolve().parent / category / "convert.py"
    for category in CATEGORIES
}

SYSTEM_ORDER = {
    "4A系统安全管控平台": 1,
    "网管支撑采控系统": 2,
    "互联网内容分发网络系统": 3,
    "安全运营平台（O域）": 4,
    "漏洞管理平台": 5,
    "省内WLAN认证系统": 6,
    "省内光传送网骨干传送网": 7,
    "省内同步网": 8,
    "省内智能网系统": 9,
    "短消息网系统": 10,
}


@dataclass(frozen=True)
class Candidate:
    path: Path
    category: str
    system: str
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_temporary(path: Path) -> bool:
    return path.name.startswith((".~", "~$", ".")) or path.name.endswith(".tmp")


def classify(path: Path) -> str | None:
    name = path.name
    suffix = path.suffix.lower()
    if suffix not in {".doc", ".docx", ".xls", ".xlsx", ".pdf"}:
        return None
    if "风险评估报告" in name:
        return "风险评估报告"
    if "定级报告" in name:
        return "定级报告"
    if "备案信息表" in name or "备案表" in name:
        return "备案信息表"
    if "资产清单" in name and "三同步报告" not in name:
        return "资产清单"
    if "证明材料" in name or "符合性评测证明" in name:
        return "符合性评测表证明材料"
    if "符合性评测表" in name and "报告" not in name and "证明" not in name:
        return "符合性评测表"
    if suffix in {".doc", ".docx", ".pdf"} and (
        "三同步情况报告" in name or re.search(r"[-－]三同步(?:20\d+)?", name)
    ):
        return "三同步报告"
    return None


def _system_from_text(text: str) -> str | None:
    compact = re.sub(r"\s+", "", text).casefold()
    rules = (
        (("4a",), "4A系统安全管控平台"),
        (("采控",), "网管支撑采控系统"),
        (("内容分发", "cdn"), "互联网内容分发网络系统"),
        (("安全运营", "o域", "0域", "集中化安全管理"), "安全运营平台（O域）"),
        (("漏洞管理",), "漏洞管理平台"),
        (("wlan",), "省内WLAN认证系统"),
        (("光传送", "骨干传送"), "省内光传送网骨干传送网"),
        (("同步网",), "省内同步网"),
        (("智能网",), "省内智能网系统"),
        (("短消息",), "短消息网系统"),
    )
    for aliases, canonical in rules:
        if any(alias in compact for alias in aliases):
            return canonical
    return None


def system_alias(path: Path, input_root: Path) -> str:
    # Filename wins so the duplicated 同步网 PDF placed under 传送网 is still
    # grouped with the real 同步网 source.
    value = _system_from_text(path.name)
    if value:
        return value
    relative_parent = str(path.relative_to(input_root).parent)
    value = _system_from_text(relative_parent)
    if value:
        return value
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "_", path.stem).strip("_") or "未识别系统"


def _priority(category: str, suffix: str) -> int:
    suffix = suffix.lower()
    if category in WORD_CATEGORIES:
        return {".docx": 0, ".doc": 1, ".pdf": 2}.get(suffix, 99)
    return {".xlsx": 0, ".xls": 1, ".pdf": 2}.get(suffix, 99)


def discover(input_root: str | Path) -> list[Candidate]:
    root = Path(input_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"输入目录不存在: {root}")
    result: list[Candidate] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or _is_temporary(path):
            continue
        category = classify(path)
        if category is None:
            continue
        suffix = path.suffix.lower()
        if category in WORD_CATEGORIES and suffix not in {".doc", ".docx", ".pdf"}:
            continue
        if category in EXCEL_CATEGORIES and suffix not in {".xls", ".xlsx", ".pdf"}:
            continue
        result.append(Candidate(path, category, system_alias(path, root), _sha256(path)))
    return result


def group_candidates(candidates: Iterable[Candidate]) -> dict[tuple[str, str], list[Candidate]]:
    grouped: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
    for item in candidates:
        grouped[(item.category, item.system)].append(item)
    for items in grouped.values():
        items.sort(
            key=lambda item: (
                _priority(item.category, item.path.suffix),
                0 if _system_from_text(str(item.path.parent)) == item.system else 1,
                str(item.path),
            )
        )
    return dict(grouped)


def _version_label(path: Path) -> str:
    values = re.findall(r"(?i)(?:20\d{2}(?:0[1-9]|1[0-2])?(?:0[1-9]|[12]\d|3[01])?|v\d+(?:\.\d+)*)", path.stem)
    return "+".join(dict.fromkeys(value.lower() for value in values))


def _logical_key(category: str, system: str, selected: Candidate) -> str:
    version = _version_label(selected.path)
    return f"{category}:{system}" + (f":{version}" if version else "")


def _safe_stem(value: str) -> str:
    return re.sub(r"[\\/:*?\"<>|]+", "_", value).strip(" .") or "result"


def _write_json_atomic(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
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


def _native_engine(path: Path) -> str:
    return {
        ".docx": "OOXML",
        ".doc": "LibreOffice转DOCX+OOXML",
        ".xlsx": "openpyxl+OOXML图片关系",
        ".xls": "xlrd+CFB图片关系",
        ".pdf": "本地轻量PDF后端",
    }.get(path.suffix.lower(), "未知")


def _read_audit(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _candidate_record(item: Candidate, reason: str) -> dict:
    return {
        "路径": str(item.path),
        "格式": item.path.suffix.lower().lstrip("."),
        "SHA256": item.sha256,
        "原因": reason,
    }


def run_batch(input_root: str | Path, output_root: str | Path, *, dry_run: bool = False) -> dict:
    input_path = Path(input_root).expanduser().resolve()
    output_path = Path(output_root).expanduser().resolve()
    candidates = discover(input_path)
    grouped = group_candidates(candidates)
    entries: list[dict] = []
    category_counters: Counter[str] = Counter()
    ordered_groups = sorted(
        grouped.items(),
        key=lambda pair: (
            CATEGORIES.index(pair[0][0]),
            SYSTEM_ORDER.get(pair[0][1], 999),
            pair[0][1],
        ),
    )
    for (category, system), items in ordered_groups:
        selected = items[0]
        category_counters[category] += 1
        number = category_counters[category]
        output_name = f"{number:02d}_{_safe_stem(selected.path.stem)}.json"
        destination = output_path / category / output_name
        audit_path = destination.with_suffix(".audit.json")
        logical_key = _logical_key(category, system, selected)
        same_hash_paths = [str(item.path) for item in items if item.sha256 == selected.sha256]
        skipped = []
        for item in items[1:]:
            if item.sha256 == selected.sha256:
                reason = "与采用文件内容完全相同，按SHA256去重"
            elif _priority(category, item.path.suffix) > _priority(category, selected.path.suffix):
                reason = "存在优先级更高的Word/Excel原件"
            else:
                reason = "同一逻辑文档已有排序更优的候选"
            skipped.append(_candidate_record(item, reason))
        entry = {
            "逻辑文档键": logical_key,
            "类别": category,
            "系统别名": system,
            "采用源文件": str(selected.path),
            "采用源文件全部重复路径": same_hash_paths,
            "未采用候选": skipped,
            "文件SHA256": selected.sha256,
            "解析来源": selected.path.suffix.lower().lstrip("."),
            "解析引擎": _native_engine(selected.path),
            "主JSON": str(destination),
            "资源目录": str(destination.parent / f"{destination.stem}.assets"),
            "审计文件": str(audit_path),
            "状态": "planned" if dry_run else "error",
        }
        if dry_run:
            entries.append(entry)
            continue
        converter = CONVERTERS[category]
        if not converter.is_file():
            error = f"缺少类别转换器: {converter}"
            audit = {
                "status": "error",
                "解析器版本": PARSER_VERSION,
                "逻辑文档键": logical_key,
                "类别": category,
                "源文件": str(selected.path),
                "错误": error,
            }
            _write_json_atomic(audit_path, audit)
            entry["错误"] = error
            entries.append(entry)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, str(converter), str(selected.path), str(destination)]
        try:
            completed = subprocess.run(
                command,
                cwd=converter.parent,
                capture_output=True,
                text=True,
                timeout=900,
                env=os.environ.copy(),
            )
        except Exception as exc:
            completed = None
            error = f"转换进程异常: {exc}"
        else:
            error = "" if completed.returncode == 0 and destination.is_file() else (
                (completed.stderr or completed.stdout or "转换器未生成主JSON").strip()
            )
        if error:
            # A failed conversion must never leave a partial main JSON.
            if destination.is_file():
                destination.unlink()
            audit = {
                "status": "error",
                "解析器版本": PARSER_VERSION,
                "逻辑文档键": logical_key,
                "类别": category,
                "源文件": str(selected.path),
                "文件SHA256": selected.sha256,
                "解析引擎": _native_engine(selected.path),
                "错误": error,
            }
            _write_json_atomic(audit_path, audit)
            entry["错误"] = error
            entries.append(entry)
            continue
        audit = _read_audit(audit_path)
        audit.update(
            {
                "status": audit.get("status", "ok"),
                "统一解析器版本": PARSER_VERSION,
                "逻辑文档键": logical_key,
                "类别": category,
                "采用源文件": str(selected.path),
                "采用源文件全部重复路径": same_hash_paths,
                "未采用候选": skipped,
                "文件SHA256": selected.sha256,
                "解析来源": selected.path.suffix.lower().lstrip("."),
                "解析引擎": audit.get("解析引擎", _native_engine(selected.path)),
            }
        )
        _write_json_atomic(audit_path, audit)
        entry["状态"] = audit["status"]
        entries.append(entry)

    counts = Counter(entry["类别"] for entry in entries)
    statuses = Counter(entry["状态"] for entry in entries)
    manifest = {
        "解析器版本": PARSER_VERSION,
        "输入目录": str(input_path),
        "输出目录": str(output_path),
        "逻辑文档总数": len(entries),
        "分类统计": {category: counts.get(category, 0) for category in CATEGORIES},
        "状态统计": dict(statuses),
        "候选文件总数": len(candidates),
        "规则": {
            "Word类优先级": ["docx", "doc", "pdf"],
            "Excel类优先级": ["xlsx", "xls", "pdf"],
            "PDF去重": "SHA256相同的PDF仅解析一次，所有路径保留在记录中",
        },
        "文档": entries,
    }
    output_path.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(output_path / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="七类材料原件优先、PDF兜底统一解析器")
    parser.add_argument("input", help="智能体案例根目录")
    parser.add_argument("output", help="统一解析结果目录")
    parser.add_argument("--dry-run", action="store_true", help="只生成选源清单，不执行解析")
    args = parser.parse_args(argv)
    try:
        manifest = run_batch(args.input, args.output, dry_run=args.dry_run)
    except Exception as exc:
        print(f"批处理失败: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "输出目录": manifest["输出目录"],
                "逻辑文档总数": manifest["逻辑文档总数"],
                "分类统计": manifest["分类统计"],
                "状态统计": manifest["状态统计"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if not manifest["状态统计"].get("error") else 2


if __name__ == "__main__":
    raise SystemExit(main())
