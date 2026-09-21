# -*- coding: utf-8 -*-
"""Batch-convert every risk assessment report in 智能体案例."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from core.path_config import project_path

from convert import convert
from risk_structure import PARSER_VERSION, content_statistics


DEFAULT_SOURCE = project_path("sample_source_root", "智能体案例")
DEFAULT_OUTPUT = Path(__file__).resolve().parent / "解析结果_嵌套版"


def discover_reports(source_dir: str | Path) -> list[Path]:
    root = Path(source_dir).expanduser().resolve()
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.suffix.lower() in {".doc", ".docx"}
        and "风险评估报告" in path.name
        and not path.name.startswith(("~$", ".~"))
    )


def _safe_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]", "_", value)
    return re.sub(r"\s+", " ", value).strip(" ._")


def _write_manifest(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def batch_convert(source_dir: str | Path, output_dir: str | Path) -> dict:
    source_root = Path(source_dir).expanduser().resolve()
    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    source_files = discover_reports(source_root)
    records: list[dict] = []
    failures: list[dict] = []

    for index, source in enumerate(source_files, start=1):
        output_name = f"{index:02d}_{_safe_name(source.stem)}.json"
        output_path = output_root / output_name
        try:
            result = convert(source, output_path)
            records.append(
                {
                    "序号": index,
                    "源文件": str(source),
                    "输出文件": output_path.name,
                    **content_statistics(result),
                }
            )
        except Exception as exc:
            failures.append({"序号": index, "源文件": str(source), "错误": str(exc)})

    manifest = {
        "解析器版本": PARSER_VERSION,
        "源目录": str(source_root),
        "输出目录": str(output_root),
        "发现文件数": len(source_files),
        "成功生成JSON数": len(records),
        "失败数": len(failures),
        "文档": records,
        "失败": failures,
    }
    _write_manifest(output_root / "manifest.json", manifest)
    return manifest


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="批量解析智能体案例中的全部风险评估报告")
    parser.add_argument("source", nargs="?", default=str(DEFAULT_SOURCE), help="源目录")
    parser.add_argument("output", nargs="?", default=str(DEFAULT_OUTPUT), help="输出目录")
    arguments = parser.parse_args(argv)
    result = batch_convert(arguments.source, arguments.output)
    print(
        f"发现 {result['发现文件数']} 份，"
        f"成功 {result['成功生成JSON数']} 份，失败 {result['失败数']} 份"
    )
    for failure in result["失败"]:
        print(f"失败: {failure['源文件']}: {failure['错误']}")
    print(f"输出目录: {result['输出目录']}")
    return 1 if result["失败数"] else 0


if __name__ == "__main__":
    raise SystemExit(_main())
