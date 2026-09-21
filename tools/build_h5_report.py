"""从评估集跑批的「稽核结果/<系统>」目录生成 H5 汇总报告。

评估集跑批（main.py）与平台单系统跑批（platform_app/worker.py）落盘结构不同：
前者只有 <item_id>/稽核结果.json + <item_id>/<item_id>.json，没有 summary.json。
本脚本按后者 report.py 期望的 payload 形状做一次适配，再复用同一套模板，
保证两个入口产出的 HTML 完全一致。

用法：
    python tools/build_h5_report.py <系统稽核目录> [<系统稽核目录> ...]

报告写在系统稽核目录下（report.py 需要以该目录为根解析 fields_file 相对路径）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.audit_catalog import audit_item_name  # noqa: E402
from platform_app.audit_service import _field_summary  # noqa: E402
from platform_app.report import generate_report  # noqa: E402


def _read_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _parse_file_count(parsed_directory: Path | None) -> int:
    if parsed_directory is None:
        return 0
    analysis = parsed_directory / "文件分析.json"
    if not analysis.is_file():
        return 0
    try:
        files = _read_json(analysis).get("files", [])
    except (OSError, json.JSONDecodeError):
        return 0
    return len(files) if isinstance(files, list) else 0


def _as_source_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(v) for v in value]
    if value:
        return [str(value)]
    return []


def _build_item(item_dir: Path, item_id: str, classification: str) -> dict[str, object]:
    result_file = item_dir / "稽核结果.json"
    fields_file = item_dir / f"{item_id}.json"

    message = ""
    issues: list[str] = []
    source_documents: list[str] = []
    fields: dict[str, object] = {}

    if result_file.is_file():
        result = _read_json(result_file)
        source_documents = _as_source_list(result.get("source_document"))
        detail = result.get("result")
        detail = detail if isinstance(detail, dict) else {}
        message = str(detail.get("message") or "")
        raw_issues = detail.get("issues")
        issues = [str(i) for i in raw_issues] if isinstance(raw_issues, list) else []
        if fields_file.is_file():
            try:
                loaded = _read_json(fields_file)
            except (OSError, json.JSONDecodeError):
                loaded = {}
            fields = loaded if isinstance(loaded, dict) else {}
    else:
        # 缺件/阻断项没有 稽核结果.json，原因写在 批量处理汇总.json 的 runs[0].message
        batch = item_dir / "批量处理汇总.json"
        if batch.is_file():
            runs = _read_json(batch).get("runs", [])
            if isinstance(runs, list) and runs and isinstance(runs[0], dict):
                message = str(runs[0].get("message") or "")

    return {
        "item_id": item_id,
        "item_name": audit_item_name(item_id),
        "status": classification,
        "message": message,
        "source_documents": source_documents,
        "issues": issues,
        "extracted_summary": _field_summary(fields) if fields else [],
        "fields_file": f"{item_id}/{fields_file.name}" if fields_file.is_file() else None,
        "result_file": f"{item_id}/{result_file.name}" if result_file.is_file() else None,
        "ai_narration": None,
    }


def build_summary(system_dir: Path) -> dict[str, object]:
    system_dir = system_dir.resolve()
    source = system_dir / "系统稽核汇总.json"
    if not source.is_file():
        raise FileNotFoundError(f"缺少 系统稽核汇总.json：{system_dir}")

    meta = _read_json(source)
    audit = meta.get("audit") if isinstance(meta.get("audit"), dict) else {}
    entries = audit.get("items", []) if isinstance(audit, dict) else []

    items = [
        _build_item(system_dir / str(entry["item_id"]), str(entry["item_id"]), str(entry.get("classification") or "failed"))
        for entry in entries
        if isinstance(entry, dict) and entry.get("item_id") and (system_dir / str(entry["item_id"])).is_dir()
    ]

    parsed_directory = meta.get("parsed_directory")
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "system": {
            "id": system_dir.name,
            "name": str(meta.get("unit") or system_dir.name),
            "department": system_dir.parent.name,
            "source_path": str(meta.get("source_directory") or ""),
        },
        "parse": {
            "exit_code": (meta.get("parse") or {}).get("exit_code"),
            "file_count": _parse_file_count(Path(str(parsed_directory)) if parsed_directory else None),
        },
        "audit": {"items": items},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 H5 稽核汇总报告")
    parser.add_argument("system_dirs", nargs="+", type=Path, help="系统稽核目录（含 系统稽核汇总.json）")
    args = parser.parse_args(argv)

    for system_dir in args.system_dirs:
        summary = build_summary(system_dir)
        target = generate_report(summary, system_dir / "汇总报告.html")
        counts: dict[str, int] = {}
        for item in summary["audit"]["items"]:
            counts[str(item["status"])] = counts.get(str(item["status"]), 0) + 1
        print(f"{target}  稽核项 {len(summary['audit']['items'])}  通过 {counts.get('passed', 0)}  不通过 {counts.get('failed', 0)}  不适用 {counts.get('not_applicable', 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
