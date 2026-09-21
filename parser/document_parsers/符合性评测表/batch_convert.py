from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from core.path_config import project_path

from convert import _image_count, convert


def find_sources(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file()
        and not path.name.startswith(".~")
        and "符合性评测表" in path.name
        and path.suffix.lower() in {".xls", ".xlsx"}
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="批量解析含图片的符合性评测表")
    parser.add_argument("source_root", nargs="?", default=str(project_path("sample_source_root", "智能体案例")))
    parser.add_argument("output_dir", nargs="?", default=str(Path(__file__).resolve().parent / "解析结果"))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summaries = []
    for source in find_sources(Path(args.source_root)):
        destination = output_dir / f"{source.stem}.json"
        payload = convert(source, destination)
        summaries.append(
            {
                "源文件": str(source),
                "输出文件": str(destination),
                "检查项目数": len(payload["检查项目"]),
                "图片引用数": _image_count(payload),
            }
        )
    print(json.dumps({"文件数": len(summaries), "结果": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
