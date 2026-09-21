#!/usr/bin/env python3
"""将旧版批量解析结果的分类子目录迁移为扁平结构。

旧：部门/业务系统/2.1 定级报告/定级报告.md
新：部门/业务系统/定级报告.md

附件会合并到业务系统目录下的“附件”目录，原Markdown里的相对路径仍然有效。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path


CATEGORY_DIRECTORIES = {
    "2.1 定级报告",
    "2.2 备案信息表",
    "2.3 资产清单",
    "2.4 三同步报告",
    "2.5 符合性评测表",
    "2.6 符合性评测证明文件",
    "2.7 风险评估报告",
}


def discover_units(batch_root: Path) -> list[Path]:
    """只识别“部门/业务系统/文件分析.json”，避免误处理其他目录。"""
    return sorted(
        (analysis.parent for analysis in batch_root.glob("*/*/文件分析.json")),
        key=lambda path: path.relative_to(batch_root).as_posix(),
    )


def build_move_plan(batch_root: Path) -> tuple[list[tuple[Path, Path]], list[Path]]:
    moves: list[tuple[Path, Path]] = []
    category_dirs: list[Path] = []
    destinations: dict[Path, Path] = {}

    for unit in discover_units(batch_root):
        if unit.is_symlink():
            raise ValueError(f"拒绝处理符号链接：{unit}")
        for category_dir in sorted(unit.iterdir(), key=lambda path: path.name):
            if not category_dir.is_dir() or category_dir.name not in CATEGORY_DIRECTORIES:
                continue
            if category_dir.is_symlink():
                raise ValueError(f"拒绝处理符号链接：{category_dir}")
            category_dirs.append(category_dir)
            for source in sorted(category_dir.rglob("*")):
                if source.is_symlink():
                    raise ValueError(f"拒绝处理符号链接：{source}")
                if not source.is_file():
                    continue
                destination = unit / source.relative_to(category_dir)
                previous = destinations.get(destination)
                if previous is not None:
                    raise FileExistsError(
                        f"两个文件将移到同一位置：{previous}、{source} -> {destination}"
                    )
                if destination.exists():
                    raise FileExistsError(f"目标已存在，为避免覆盖已停止：{destination}")
                destinations[destination] = source
                moves.append((source, destination))
    return moves, category_dirs


def strip_category_prefix(value: object) -> object:
    if not isinstance(value, str):
        return value
    for directory in CATEGORY_DIRECTORIES:
        prefix = directory + "/"
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


def update_analysis(unit: Path) -> None:
    json_path = unit / "文件分析.json"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    for record in data.get("files", []):
        record["output"] = strip_category_prefix(record.get("output"))
        record["asset_directory"] = strip_category_prefix(record.get("asset_directory"))
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    markdown_path = unit / "文件分析.md"
    if markdown_path.is_file():
        text = markdown_path.read_text(encoding="utf-8")
        for directory in CATEGORY_DIRECTORIES:
            text = text.replace(directory + "/", "")
        markdown_path.write_text(text, encoding="utf-8")


def remove_empty_category_directories(category_dirs: list[Path]) -> None:
    for category_dir in category_dirs:
        descendants = sorted(
            (path for path in category_dir.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in descendants:
            directory.rmdir()
        category_dir.rmdir()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="将旧批量解析结果改成Markdown直接位于业务系统目录的结构。")
    parser.add_argument("batch_root", type=Path, help="批量解析结果根目录")
    parser.add_argument("--dry-run", action="store_true", help="只显示迁移数量，不修改文件")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    batch_root = args.batch_root.expanduser().resolve()
    if not batch_root.is_dir():
        print(f"批量结果目录不存在：{batch_root}", file=sys.stderr)
        return 2
    if batch_root.is_symlink():
        print(f"拒绝处理符号链接：{batch_root}", file=sys.stderr)
        return 2

    try:
        units = discover_units(batch_root)
        moves, category_dirs = build_move_plan(batch_root)
        print(f"识别到{len(units)}个业务系统单元，{len(category_dirs)}个分类目录，{len(moves)}个待移动文件。")
        if args.dry_run:
            return 0

        for source, destination in moves:
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.rename(destination)
        remove_empty_category_directories(category_dirs)
        for unit in units:
            update_analysis(unit)

        log = {
            "tool": "flatten_existing_results.py",
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "batch_root": str(batch_root),
            "units": len(units),
            "moved_files": len(moves),
            "removed_category_directories": len(category_dirs),
        }
        log_path = batch_root / "扁平化迁移记录.json"
        log_path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"迁移完成：{log_path}")
        return 0
    except Exception as error:
        print(f"迁移失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
