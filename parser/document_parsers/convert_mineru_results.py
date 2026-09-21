#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Batch-convert MinerU ZIP/directory output to ordered generic JSON."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path

from _shared.mineru_generic import convert_content_list


def _write_json_atomic(path: Path, value) -> None:
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


def _fallback_document_name(content_path: Path) -> str:
    name = re.sub(r"_content_list_v2$", "", content_path.stem)
    return re.sub(r"^\d+[_\-]", "", name) or "未命名文档"


def _safe_extract(archive: Path, target: Path) -> None:
    root = target.resolve()
    with zipfile.ZipFile(archive) as handle:
        for member in handle.infolist():
            destination = (target / member.filename).resolve()
            if destination != root and root not in destination.parents:
                raise ValueError(f"ZIP包含不安全路径: {member.filename}")
        handle.extractall(target)


def _convert_root(source_root: Path, output_dir: Path) -> dict:
    paths = sorted(source_root.rglob("*_content_list_v2.json"))
    if not paths:
        raise FileNotFoundError(f"没有找到 *_content_list_v2.json: {source_root}")
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = []
    for path in paths:
        try:
            report, audit = convert_content_list(path, output_dir)
            name = report["文档名称"]
            json_path = output_dir / f"{name}.json"
            audit_path = output_dir / f"{name}.audit.json"
            _write_json_atomic(json_path, report)
            _write_json_atomic(audit_path, audit)
            documents.append(
                {
                    "文档名称": name,
                    "JSON": str(json_path),
                    "审计": str(audit_path),
                    "资源目录": str(output_dir / f"{name}.assets"),
                    "状态": audit["status"],
                    "统计": audit["statistics"],
                    "表格类型": audit["table_types"],
                }
            )
        except Exception as exc:
            name = _fallback_document_name(path)
            audit_path = output_dir / f"{name}.audit.json"
            error_audit = {
                "status": "error",
                "文档名称": name,
                "源content_list_v2": str(path),
                "错误": str(exc),
            }
            _write_json_atomic(audit_path, error_audit)
            documents.append(
                {
                    "文档名称": name,
                    "JSON": "",
                    "审计": str(audit_path),
                    "资源目录": "",
                    "状态": "error",
                    "错误": str(exc),
                }
            )
    successful = sum(bool(item.get("JSON")) for item in documents)
    if not successful:
        status = "error"
    elif any(item["状态"] != "ok" for item in documents):
        status = "needs_review"
    else:
        status = "ok"
    summary = {
        "状态": status,
        "文档数量": len(documents),
        "成功文档数": successful,
        "失败文档数": len(documents) - successful,
        "文档": documents,
    }
    _write_json_atomic(output_dir / "转换汇总.json", summary)
    return summary


def convert_mineru_results(source: str | Path, output_dir: str | Path) -> dict:
    source_path = Path(source).expanduser().resolve()
    target = Path(output_dir).expanduser().resolve()
    if source_path.is_dir():
        return _convert_root(source_path, target)
    if source_path.suffix.casefold() != ".zip":
        raise ValueError("输入必须是 MinerU ZIP 或解压后的目录")
    with tempfile.TemporaryDirectory(prefix="mineru-generic-") as temporary:
        root = Path(temporary)
        _safe_extract(source_path, root)
        return _convert_root(root, target)


def main() -> None:
    parser = argparse.ArgumentParser(description="将MinerU结果转为标题/段落/表格/图片有序JSON")
    parser.add_argument("source", help="MinerU ZIP 或解压目录")
    parser.add_argument("output_dir", help="JSON输出目录")
    args = parser.parse_args()
    summary = convert_mineru_results(args.source, args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
