from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from core.path_config import project_path

from batch_convert import find_sources
from convert import ASSET_FIELDS, parse


def main() -> None:
    parser = argparse.ArgumentParser(description="核验资产清单 JSON 的字段与记录完整性")
    parser.add_argument("source_root", nargs="?", default=str(project_path("sample_source_root", "智能体案例")))
    parser.add_argument("output_dir", nargs="?", default=str(Path(__file__).resolve().parent / "解析结果"))
    args = parser.parse_args()

    errors: list[str] = []
    summaries: list[dict[str, object]] = []
    for source in find_sources(Path(args.source_root)):
        destination = Path(args.output_dir) / f"{source.stem}.json"
        if not destination.exists():
            errors.append(f"缺少结果文件：{destination}")
            continue
        actual = json.loads(destination.read_text(encoding="utf-8"))
        expected = parse(source)
        if actual != expected:
            errors.append(f"结果与当前解析逻辑不一致：{destination}")
        for index, record in enumerate(actual.get("资产清单", []), start=1):
            missing = [field for field in ASSET_FIELDS if field not in record]
            if missing:
                errors.append(f"{destination.name} 第 {index} 条缺少字段：{missing}")
            if not any(record.values()):
                errors.append(f"{destination.name} 第 {index} 条为全空记录")
        summaries.append({"文件": destination.name, "资产数": len(actual.get("资产清单", []))})

    payload = {"状态": "通过" if not errors else "失败", "文件数": len(summaries), "结果": summaries, "错误": errors}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
