#!/usr/bin/env python3
"""严格按“部门/业务系统文件夹”单位批量调用parse_folder。"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from parse_folder import CATEGORY_BY_CODE, category_selector, main as parse_one_folder


EXCLUDED_TOP_LEVEL = {
    "定级报告解析测试集",
    "批量解析结果",
}


def is_generated_directory(path: Path, output_root: Path) -> bool:
    resolved = path.resolve(strict=False)
    resolved_output = output_root.resolve(strict=False)
    return (
        resolved == resolved_output
        or resolved_output in resolved.parents
        or path.name.startswith(".")
        or path.name.endswith("_anydoc解析结果")
        or "_旧结果备份_" in path.name
    )


def discover_units(source_root: Path, output_root: Path) -> list[tuple[Path, Path]]:
    """只把部门的直接子目录视为单位，不把更深层扫描目录拆成单位。"""
    units: list[tuple[Path, Path]] = []
    departments = sorted((path for path in source_root.iterdir() if path.is_dir()), key=lambda p: p.name)
    for department in departments:
        if department.name in EXCLUDED_TOP_LEVEL or is_generated_directory(department, output_root):
            continue
        for unit in sorted((path for path in department.iterdir() if path.is_dir()), key=lambda p: p.name):
            if is_generated_directory(unit, output_root):
                continue
            units.append((department, unit))
    return units


def prepare_output(output_root: Path, overwrite: bool) -> Path | None:
    if not output_root.exists():
        output_root.mkdir(parents=True)
        return None
    if not overwrite:
        raise FileExistsError(f"批量输出目录已存在：{output_root}（重新生成请加--overwrite）")
    if output_root.is_symlink():
        raise ValueError(f"拒绝覆盖符号链接：{output_root}")

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = output_root.with_name(f"{output_root.name}_旧结果备份_{timestamp}")
    serial = 2
    while backup.exists():
        backup = output_root.with_name(f"{output_root.name}_旧结果备份_{timestamp}_{serial}")
        serial += 1
    output_root.rename(backup)
    output_root.mkdir(parents=True)
    return backup


def unit_summary(
    unit_output: Path,
    selected_category_code: str | None = None,
    emergency_only: bool = False,
) -> dict[str, object]:
    analysis_path = unit_output / "文件分析.json"
    emergency_path = unit_output / "应急材料分析.json"
    emergency_data = (
        json.loads(emergency_path.read_text(encoding="utf-8"))
        if emergency_path.is_file()
        else None
    )
    if not analysis_path.is_file() and not emergency_only:
        return {
            "grading_reports_found": 0,
            "grading_reports_success": 0,
            "grading_reports_unsupported": 0,
            "registration_forms_found": 0,
            "registration_forms_success": 0,
            "registration_forms_unsupported": 0,
            "asset_lists_found": 0,
            "asset_lists_success": 0,
            "asset_lists_unsupported": 0,
            "conformity_tables_found": 0,
            "conformity_tables_success": 0,
            "conformity_tables_unsupported": 0,
            "conformity_proofs_found": 0,
            "conformity_proofs_success": 0,
            "conformity_proofs_unsupported": 0,
            "conformity_reports_found": 0,
            "conformity_reports_success": 0,
            "conformity_reports_unsupported": 0,
            "risk_reports_found": 0,
            "risk_reports_success": 0,
            "risk_reports_unsupported": 0,
            "scan_reports_found": 0,
            "scan_reports_success": 0,
            "scan_reports_unsupported": 0,
            "drill_plans_found": 0,
            "drill_plans_success": 0,
            "drill_plans_unsupported": 0,
            "failure_count": 0,
            "failure_summary": "",
            "failure_details": [],
            "analysis": None,
        }
    data = (
        json.loads(analysis_path.read_text(encoding="utf-8"))
        if analysis_path.is_file()
        else {"files": []}
    )
    grading = [item for item in data.get("files", []) if item.get("category_code") == "2.1"]
    registrations = [item for item in data.get("files", []) if item.get("category_code") == "2.2"]
    asset_lists = [item for item in data.get("files", []) if item.get("category_code") == "2.3"]
    conformity_tables = [
        item for item in data.get("files", []) if item.get("category_code") == "2.5"
    ]
    conformity_proofs = [
        item for item in data.get("files", []) if item.get("category_code") == "2.6"
    ]
    risk_reports = [
        item for item in data.get("files", []) if item.get("category_code") == "2.7"
    ]
    scan_reports = [
        item for item in data.get("files", []) if item.get("category_code") == "2.8"
    ]
    conformity_reports = [
        item for item in data.get("files", []) if item.get("category_code") == "2.9"
    ]
    drill_plans = [
        item for item in data.get("files", []) if item.get("category_code") == "2.10"
    ]
    failed_files = [
        item
        for item in data.get("files", [])
        if item.get("status") == "failed"
        and (
            selected_category_code is None
            or item.get("category_code") == selected_category_code
        )
    ]
    failure_details = [
        {
            "source": item.get("source"),
            "category": item.get("category_name"),
            "failure_type": item.get("failure_type") or "parse_error",
            "reason": item.get("failure_reason") or item.get("error") or "解析失败，未记录具体原因。",
            "suggestion": item.get("suggestion") or "请查看该单位的文件分析.json和技术错误。",
            "technical_error": item.get("technical_error") or item.get("error"),
        }
        for item in failed_files
    ]
    if (emergency_only or selected_category_code is None) and emergency_data is not None:
        failure_details.extend(
            {
                "source": item.get("源文件"),
                "category": item.get("用途"),
                "failure_type": item.get("错误类型") or "parse_error",
                "reason": item.get("错误原因") or "解析失败，未记录具体原因。",
                "suggestion": item.get("处理建议") or "请检查应急材料原件。",
                "technical_error": item.get("技术错误"),
            }
            for item in emergency_data.get("files", [])
            if item.get("解析状态") == "failed"
        )
    return {
        "grading_reports_found": len(grading),
        "grading_reports_success": sum(item.get("status") == "success" for item in grading),
        "grading_reports_unsupported": sum(
            item.get("status") == "unsupported_format" for item in grading
        ),
        "registration_forms_found": len(registrations),
        "registration_forms_success": sum(
            item.get("status") == "success" for item in registrations
        ),
        "registration_forms_unsupported": sum(
            item.get("status") == "unsupported_format" for item in registrations
        ),
        "asset_lists_found": len(asset_lists),
        "asset_lists_success": sum(item.get("status") == "success" for item in asset_lists),
        "asset_lists_unsupported": sum(
            item.get("status") == "unsupported_format" for item in asset_lists
        ),
        "conformity_tables_found": len(conformity_tables),
        "conformity_tables_success": sum(
            item.get("status") == "success" for item in conformity_tables
        ),
        "conformity_tables_unsupported": sum(
            item.get("status") == "unsupported_format" for item in conformity_tables
        ),
        "conformity_proofs_found": len(conformity_proofs),
        "conformity_proofs_success": sum(
            item.get("status") == "success" for item in conformity_proofs
        ),
        "conformity_proofs_unsupported": sum(
            item.get("status") == "unsupported_format" for item in conformity_proofs
        ),
        "conformity_reports_found": len(conformity_reports),
        "conformity_reports_success": sum(
            item.get("status") == "success" for item in conformity_reports
        ),
        "conformity_reports_unsupported": sum(
            item.get("status") == "unsupported_format" for item in conformity_reports
        ),
        "risk_reports_found": len(risk_reports),
        "risk_reports_success": sum(
            item.get("status") == "success" for item in risk_reports
        ),
        "risk_reports_unsupported": sum(
            item.get("status") == "unsupported_format" for item in risk_reports
        ),
        "scan_reports_found": len(scan_reports),
        "scan_reports_success": sum(
            item.get("status") == "success" for item in scan_reports
        ),
        "scan_reports_unsupported": sum(
            item.get("status") == "unsupported_format" for item in scan_reports
        ),
        "drill_plans_found": len(drill_plans),
        "drill_plans_success": sum(
            item.get("status") == "success" for item in drill_plans
        ),
        "drill_plans_unsupported": sum(
            item.get("status") == "unsupported_format" for item in drill_plans
        ),
        "failure_count": len(failure_details),
        "failure_summary": "；".join(
            f"{item['source']}：{item['reason']}" for item in failure_details
        ),
        "failure_details": failure_details,
        "analysis": (
            analysis_path.relative_to(unit_output.parents[1]).as_posix()
            if analysis_path.is_file()
            else None
        ),
        "emergency_materials_found": int((emergency_data or {}).get("matched_count", 0)),
        "emergency_materials_success": int((emergency_data or {}).get("success_count", 0)),
        "emergency_materials_failed": int((emergency_data or {}).get("failed_count", 0)),
        "emergency_analysis": (
            emergency_path.relative_to(unit_output.parents[1]).as_posix()
            if emergency_path.is_file()
            else None
        ),
    }


def write_failure_report(output_root: Path, rows: list[dict[str, object]]) -> None:
    """生成便于人工处理的批量失败原因汇总。"""
    failures = [row for row in rows if row.get("status") == "failed"]

    def cell(value: object) -> str:
        return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ")

    lines = [
        "# 批量解析失败明细",
        "",
        f"- 失败单位：{len(failures)} 个",
        f"- 失败文件：{sum(int(row.get('failure_count') or 0) for row in failures)} 个",
        "",
    ]
    if not failures:
        lines.append("本次没有解析失败。")
    else:
        lines.extend(
            [
                "| 部门 | 业务系统文件夹 | 失败文件 | 失败原因 | 处理建议 | 技术错误 |",
                "|---|---|---|---|---|---|",
            ]
        )
        for row in failures:
            details = row.get("failure_details") or []
            if not isinstance(details, list) or not details:
                details = [
                    {
                        "source": "—",
                        "reason": "处理未完成，且没有生成文件级错误记录。",
                        "suggestion": "请检查该单位输出目录及运行日志。",
                        "technical_error": f"进程退出码：{row.get('exit_code')}",
                    }
                ]
            for detail in details:
                lines.append(
                    f"| {cell(row.get('department'))} | {cell(row.get('unit_folder'))} | "
                    f"{cell(detail.get('source'))} | {cell(detail.get('reason'))} | "
                    f"{cell(detail.get('suggestion'))} | {cell(detail.get('technical_error'))} |"
                )
    (output_root / "批量失败明细.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_batch_manifest(
    output_root: Path,
    source_root: Path,
    rows: list[dict[str, object]],
    backup: Path | None,
    only_category_code: str | None = None,
    emergency_only: bool = False,
) -> None:
    manifest = {
        "tool": "anydoc文件夹解析工具-批量入口",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_root": str(source_root),
        "output_root": str(output_root),
        "unit_rule": "只处理源目录下的 部门/业务系统文件夹 两级；每个业务系统文件夹独立投入。",
        "previous_output_backup": str(backup) if backup else None,
        "regeneration_mode": (
            "emergency_only"
            if emergency_only
            else ("single_category" if only_category_code else "full")
        ),
        "selected_category_code": only_category_code,
        "selected_category_name": (
            CATEGORY_BY_CODE[only_category_code].name if only_category_code else None
        ),
        "total_units": len(rows),
        "successful_units": sum(row["status"] == "success" for row in rows),
        "failed_units": sum(row["status"] == "failed" for row in rows),
        "units": rows,
    }
    (output_root / "批量汇总.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_failure_report(output_root, rows)
    fields = [
        "department",
        "unit_folder",
        "input",
        "output",
        "status",
        "exit_code",
        "grading_reports_found",
        "grading_reports_success",
        "grading_reports_unsupported",
        "registration_forms_found",
        "registration_forms_success",
        "registration_forms_unsupported",
        "asset_lists_found",
        "asset_lists_success",
        "asset_lists_unsupported",
        "conformity_tables_found",
        "conformity_tables_success",
        "conformity_tables_unsupported",
        "conformity_proofs_found",
        "conformity_proofs_success",
        "conformity_proofs_unsupported",
        "conformity_reports_found",
        "conformity_reports_success",
        "conformity_reports_unsupported",
        "risk_reports_found",
        "risk_reports_success",
        "risk_reports_unsupported",
        "scan_reports_found",
        "scan_reports_success",
        "scan_reports_unsupported",
        "drill_plans_found",
        "drill_plans_success",
        "drill_plans_unsupported",
        "failure_count",
        "failure_summary",
        "analysis",
        "emergency_materials_found",
        "emergency_materials_success",
        "emergency_materials_failed",
        "emergency_analysis",
    ]
    with (output_root / "批量汇总.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="严格按部门下的业务系统文件夹为单位，批量解析定级、备案、资产、符合性、风险评估、扫描报告和演练计划。"
    )
    parser.add_argument("source", type=Path, help="网管中心根目录")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="集中输出目录；默认是网管中心根目录下的“批量解析结果”",
    )
    parser.add_argument("--overwrite", action="store_true", help="备份旧批量结果后重新生成")
    parser.add_argument("--analysis-only", action="store_true", help="只分析文件，不调用anydoc")
    parser.add_argument(
        "--workers",
        type=positive_worker_count,
        default=3,
        metavar="N",
        help="并行处理的业务系统数；默认3，设为1可使用原串行模式",
    )
    regeneration = parser.add_mutually_exclusive_group()
    regeneration.add_argument(
        "--only",
        type=category_selector,
        metavar="类别",
        help="只重新生成一种类别，可填定级报告、备案信息表、资产清单、符合性评测表、符合性评测表证明材料、符合性评测报告、风险评估报告、扫描报告、演练计划或2.1/2.2/2.3/2.5/2.6/2.7/2.8/2.9/2.10",
    )
    regeneration.add_argument(
        "--emergency-only",
        action="store_true",
        help="只增量生成应急预案/演练方案/应急演练报告标准Markdown",
    )
    return parser.parse_args(argv)


def positive_worker_count(value: str) -> int:
    try:
        workers = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("并行数必须是正整数") from error
    if workers < 1:
        raise argparse.ArgumentTypeError("并行数必须大于等于1")
    return workers


def run_unit(arguments: list[str]) -> int:
    """在独立工作进程中调用单位解析入口。"""
    command = [sys.executable, str(Path(__file__).with_name("parse_folder.py")), *arguments]
    return subprocess.run(command, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    source_root = args.source.expanduser().resolve()
    if not source_root.is_dir():
        print(f"源目录不存在：{source_root}", file=sys.stderr)
        return 2
    output_root = (
        args.output.expanduser().resolve()
        if args.output
        else source_root / "批量解析结果"
    )
    if output_root == source_root:
        print("批量输出目录不能与源目录相同。", file=sys.stderr)
        return 2

    try:
        units = discover_units(source_root, output_root)
        if args.only or args.emergency_only:
            if output_root.is_symlink():
                raise ValueError(f"拒绝写入符号链接：{output_root}")
            output_root.mkdir(parents=True, exist_ok=True)
            backup = None
        else:
            backup = prepare_output(output_root, args.overwrite)
    except Exception as error:
        print(f"批量准备失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 2

    print(f"识别到{len(units)}个独立业务系统文件夹。")
    if args.only:
        print(f"单项重新生成：{CATEGORY_BY_CODE[args.only].name}；其他解析结果保持不动。")
    if args.emergency_only:
        print("增量生成应急材料；其他解析结果保持不动。")
    if backup:
        print(f"旧批量结果已备份：{backup}")

    jobs: list[tuple[Path, Path, Path, list[str]]] = []
    for department, unit in units:
        relative = unit.relative_to(source_root)
        unit_output = output_root / relative
        arguments = [str(unit), "--output", str(unit_output)]
        if args.analysis_only:
            arguments.append("--analysis-only")
        if args.only:
            arguments.extend(["--only", args.only])
        if args.emergency_only:
            arguments.append("--emergency-only")
            if args.overwrite:
                arguments.append("--overwrite")
        jobs.append((department, unit, unit_output, arguments))

    # 纯分析只做扫描和哈希，启动子进程的成本反而更高。
    if args.analysis_only or not jobs:
        worker_count = 1
    else:
        worker_count = min(args.workers, len(jobs))
    if worker_count > 1:
        print(f"并行处理：{worker_count}个工作进程。")
        executor = ThreadPoolExecutor(max_workers=worker_count)
        futures = [executor.submit(run_unit, arguments) for _, _, _, arguments in jobs]
    else:
        executor = None
        futures = []

    rows: list[dict[str, object]] = []
    try:
        for index, (department, unit, unit_output, arguments) in enumerate(jobs, start=1):
            relative = unit.relative_to(source_root)
            print(f"[{index}/{len(units)}] {relative.as_posix()}")
            exit_code = (
                futures[index - 1].result()
                if executor is not None
                else parse_one_folder(arguments)
            )
            summary = unit_summary(unit_output, args.only, args.emergency_only)
            row = {
                "department": department.name,
                "unit_folder": unit.name,
                "input": str(unit),
                "output": str(unit_output),
                "status": "success" if exit_code == 0 else "failed",
                "exit_code": exit_code,
                **summary,
            }
            if exit_code != 0 and not row.get("failure_details"):
                row["failure_count"] = 1
                row["failure_summary"] = "处理未完成，且没有生成文件级错误记录。"
                row["failure_details"] = [
                    {
                        "source": None,
                        "category": CATEGORY_BY_CODE[args.only].name if args.only else None,
                        "failure_type": "unit_process_error",
                        "reason": "处理未完成，且没有生成文件级错误记录。",
                        "suggestion": "请检查该单位输出目录及运行日志。",
                        "technical_error": f"进程退出码：{exit_code}",
                    }
                ]
            if row["status"] == "failed":
                for detail in row.get("failure_details", []):
                    print(f"  失败文件：{detail.get('source') or '未确定'}")
                    print(f"  失败原因：{detail.get('reason')}")
                    print(f"  处理建议：{detail.get('suggestion')}")
            rows.append(row)
            write_batch_manifest(
                output_root,
                source_root,
                rows,
                backup,
                args.only,
                args.emergency_only,
            )
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)

    failed = sum(row["status"] == "failed" for row in rows)
    print(f"批量完成：成功{len(rows) - failed}个单位，失败{failed}个单位。")
    print(f"批量汇总：{output_root / '批量汇总.json'}")
    print(f"失败明细：{output_root / '批量失败明细.md'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
