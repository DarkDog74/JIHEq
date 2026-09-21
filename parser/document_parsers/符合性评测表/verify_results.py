from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from core.path_config import project_path
from typing import Any, Iterable

from batch_convert import find_sources
from business_parser import OUTPUT_CHECK_FIELDS


def _all_image_refs(payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    yield from payload.get("其他图片", [])
    for record in payload.get("检查项目", []):
        yield from record.get("图片", [])
    for record in payload.get("备份信息", []):
        yield from record.get("图片", [])
    for records in payload.get("其他附表", {}).values():
        for record in records:
            yield from record.get("图片", [])


def main() -> None:
    parser = argparse.ArgumentParser(description="核验含图片符合性评测表结果")
    parser.add_argument("source_root", nargs="?", default=str(project_path("sample_source_root", "智能体案例")))
    parser.add_argument("output_dir", nargs="?", default=str(Path(__file__).resolve().parent / "解析结果"))
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    errors: list[str] = []
    summaries: list[dict[str, Any]] = []
    for source in find_sources(Path(args.source_root)):
        result_path = output_dir / f"{source.stem}.json"
        if not result_path.is_file():
            errors.append(f"缺少结果文件：{result_path}")
            continue
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        for index, record in enumerate(payload.get("检查项目", []), start=1):
            if tuple(record) != OUTPUT_CHECK_FIELDS:
                errors.append(f"{result_path.name} 第 {index} 条检查项目字段不完整")

        references = list(_all_image_refs(payload))
        referenced_files: set[Path] = set()
        for image in references:
            relative = Path(image.get("地址", ""))
            if not relative.parts or relative.is_absolute() or ".." in relative.parts:
                errors.append(f"{result_path.name} 存在非法图片地址：{relative}")
                continue
            target = (output_dir / relative).resolve()
            try:
                target.relative_to(output_dir)
            except ValueError:
                errors.append(f"{result_path.name} 图片地址越出结果目录：{relative}")
                continue
            if not target.is_file():
                errors.append(f"{result_path.name} 图片不存在：{relative}")
            else:
                referenced_files.add(target)

        assets_dir = output_dir / f"{result_path.stem}.assets"
        actual_assets = set(assets_dir.iterdir()) if assets_dir.is_dir() else set()
        orphaned = actual_assets - referenced_files
        if orphaned:
            errors.append(f"{result_path.name} 存在 {len(orphaned)} 个未引用图片文件")

        source_counts = Counter(image.get("来源", "") for image in references)
        summaries.append(
            {
                "文件": result_path.name,
                "检查项目数": len(payload.get("检查项目", [])),
                "图片引用数": len(references),
                "图片文件数": len(actual_assets),
                "图片来源": dict(source_counts),
            }
        )

    payload = {"状态": "通过" if not errors else "失败", "文件数": len(summaries), "结果": summaries, "错误": errors}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
