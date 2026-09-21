#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Validate the 25 native/PDF pairs without changing unified business JSON."""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from batch_convert_all import CONVERTERS, _write_json_atomic


PAIR_CATEGORIES = {"定级报告", "符合性评测表证明材料", "符合性评测表", "风险评估报告"}


def _norm(value: str) -> str:
    return re.sub(r"[\s\u3000]+", "", value or "").replace("－", "-")


def _nested_stats(document: dict) -> dict:
    titles: list[str] = []
    text: list[str] = []
    table_count = 0
    nonempty_cells: list[str] = []

    def scalars(value, key: str = ""):
        nonlocal table_count
        if isinstance(value, dict):
            if value.get("类型") == "表格":
                table_count += 1
                for item_key, item_value in value.items():
                    if item_key not in {"类型", "表格编号", "图片", "地址", "图片编号"}:
                        scalars(item_value, item_key)
                return
            for item_key, item_value in value.items():
                if item_key not in {"文档名称", "类型", "图片编号", "地址", "替代文字", "标题级别"}:
                    scalars(item_value, item_key)
        elif isinstance(value, list):
            for item in value:
                scalars(item, key)
        elif isinstance(value, str) and _norm(value):
            if key == "标题":
                titles.append(_norm(value))
            elif key == "文本":
                text.append(_norm(value))
            else:
                nonempty_cells.append(_norm(value))

    scalars(document)
    return {
        "titles": titles,
        "text": "".join(text),
        "table_count": table_count,
        "cells": nonempty_cells,
    }


def _nested_compare(native: dict, pdf: dict) -> dict:
    left = _nested_stats(native)
    right = _nested_stats(pdf)
    right_titles = Counter(right["titles"])
    matched_titles = 0
    for title in left["titles"]:
        if right_titles[title]:
            matched_titles += 1
            right_titles[title] -= 1
    title_accuracy = matched_titles / len(left["titles"]) if left["titles"] else 1.0
    text_accuracy = difflib.SequenceMatcher(None, left["text"], right["text"], autojunk=False).ratio()
    right_cells = Counter(right["cells"])
    matched_cells = 0
    for cell in left["cells"]:
        if right_cells[cell]:
            matched_cells += 1
            right_cells[cell] -= 1
    cell_accuracy = matched_cells / len(left["cells"]) if left["cells"] else 1.0
    passed = (
        title_accuracy >= 0.98
        and text_accuracy >= 0.98
        and left["table_count"] == right["table_count"]
        and cell_accuracy >= 0.98
    )
    return {
        "通过": passed,
        "章节标题准确率": round(title_accuracy, 6),
        "正文规范化相似度": round(text_accuracy, 6),
        "原件表格数": left["table_count"],
        "PDF表格数": right["table_count"],
        "非空表格字段准确率": round(cell_accuracy, 6),
    }


def _assessment_compare(native: dict, pdf: dict) -> dict:
    left = native.get("检查项目", [])
    right = pdf.get("检查项目", [])
    fields = ("序号", "检查点", "答案")
    exact = {}
    for field in fields:
        exact[field] = (
            len(left) == len(right)
            and all(_norm(a.get(field, "")) == _norm(b.get(field, "")) for a, b in zip(left, right))
        )
    total = matched = 0
    for a, b in zip(left, right):
        for field, value in a.items():
            if field in {"图片", "佐证材料"} or not isinstance(value, str) or not _norm(value):
                continue
            total += 1
            matched += _norm(value) == _norm(b.get(field, ""))
    other_accuracy = matched / total if total else 1.0
    passed = len(left) == len(right) and all(exact.values()) and other_accuracy >= 0.99
    return {
        "通过": passed,
        "原件检查项目数": len(left),
        "PDF检查项目数": len(right),
        "序号完全一致": exact["序号"],
        "检查点完全一致": exact["检查点"],
        "答案完全一致": exact["答案"],
        "其他非空字段准确率": round(other_accuracy, 6),
    }


def validate(manifest_path: str | Path, output_report: str | Path) -> dict:
    manifest_file = Path(manifest_path).expanduser().resolve()
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    pairs = []
    for entry in manifest["文档"]:
        if entry["类别"] not in PAIR_CATEGORIES or entry["解析来源"] == "pdf":
            continue
        pdf_candidates = [item for item in entry["未采用候选"] if item["格式"] == "pdf"]
        if not pdf_candidates:
            continue
        pairs.append((entry, Path(pdf_candidates[0]["路径"])))
    details = []
    with tempfile.TemporaryDirectory(prefix="pdf-pair-validation-") as work_name:
        work = Path(work_name)
        for index, (entry, pdf_path) in enumerate(pairs, start=1):
            pdf_json = work / f"{index:02d}.json"
            converter = CONVERTERS[entry["类别"]]
            completed = subprocess.run(
                [sys.executable, str(converter), str(pdf_path), str(pdf_json)],
                cwd=converter.parent,
                capture_output=True,
                text=True,
                timeout=900,
                env=os.environ.copy(),
            )
            detail = {
                "逻辑文档键": entry["逻辑文档键"],
                "类别": entry["类别"],
                "原件": entry["采用源文件"],
                "PDF": str(pdf_path),
            }
            if completed.returncode != 0 or not pdf_json.is_file():
                detail.update({"通过": False, "错误": (completed.stderr or completed.stdout).strip()})
            else:
                native = json.loads(Path(entry["主JSON"]).read_text(encoding="utf-8"))
                pdf = json.loads(pdf_json.read_text(encoding="utf-8"))
                metric = (
                    _assessment_compare(native, pdf)
                    if entry["类别"] == "符合性评测表"
                    else _nested_compare(native, pdf)
                )
                detail.update(metric)
            details.append(detail)
            print(f"[{index}/{len(pairs)}] {entry['逻辑文档键']}: {'PASS' if detail.get('通过') else 'FAIL'}", flush=True)
    category_status = {}
    for category in sorted(PAIR_CATEGORIES):
        values = [item for item in details if item["类别"] == category]
        category_status[category] = {
            "样本数": len(values),
            "通过数": sum(bool(item.get("通过")) for item in values),
            "轻量后端达标": bool(values) and all(item.get("通过") for item in values),
        }
    report = {
        "配对样本总数": len(details),
        "通过样本数": sum(bool(item.get("通过")) for item in details),
        "类别结论": category_status,
        "MinerU策略": "仅对轻量后端未达标的类别启用；当前环境未安装时不得生成不完整主JSON",
        "明细": details,
    }
    _write_json_atomic(Path(output_report).expanduser().resolve(), report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="25份原件/PDF配对验收")
    parser.add_argument("manifest")
    parser.add_argument("output")
    args = parser.parse_args(argv)
    report = validate(args.manifest, args.output)
    print(json.dumps({key: report[key] for key in ("配对样本总数", "通过样本数", "类别结论")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
