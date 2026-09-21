from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from audit_rules import get_rule_spec, list_rule_items
from core.audit_catalog import DISABLED_ITEMS, audit_item_name
from core.batch_process import format_batch_summary, run_batch
from tools.evaluate_batch_results import (
    GROUND_TRUTH_FILENAME,
    evaluate_batch_results,
    ground_truth_path_for_source,
)
from extractors import get_extractor_spec, list_extractor_items
from parsing_engine import run_unit_parse
from core.pipeline import run_audit, run_extract, run_pipeline, write_json


def _add_item_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--item", required=True, help="稽核项编号，例如 DJ-02")


def _add_overwrite_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有输出")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按稽核项组织的文档提取与稽核程序",
        epilog=(
            "快捷用法：python3 main.py /某个系统文件夹 "
            "（自动对该系统执行全部稽核项）"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract_parser = subparsers.add_parser("extract", help="执行一个稽核项的字段提取")
    _add_item_argument(extract_parser)
    extract_parser.add_argument(
        "--input-dir", required=True, type=Path, help="单个系统的材料目录"
    )
    extract_parser.add_argument("--output", required=True, type=Path, help="字段 JSON 输出路径")
    _add_overwrite_flag(extract_parser)

    audit_parser = subparsers.add_parser("audit", help="对字段 JSON 执行配对的稽核规则")
    _add_item_argument(audit_parser)
    audit_parser.add_argument("--input", required=True, type=Path, help="字段 JSON")
    audit_parser.add_argument("--output", required=True, type=Path, help="稽核结果 JSON")
    _add_overwrite_flag(audit_parser)

    run_parser = subparsers.add_parser("run", help="依次执行同一稽核项的提取和稽核")
    _add_item_argument(run_parser)
    run_parser.add_argument(
        "--input-dir", required=True, type=Path, help="单个系统的材料目录"
    )
    run_parser.add_argument(
        "--extracted-output", required=True, type=Path, help="字段 JSON 输出路径"
    )
    run_parser.add_argument(
        "--audit-output", required=True, type=Path, help="稽核结果 JSON 输出路径"
    )
    _add_overwrite_flag(run_parser)

    batch_parser = subparsers.add_parser("batch", help="批量处理目录中的 Markdown")
    _add_item_argument(batch_parser)
    batch_parser.add_argument(
        "--dataset-dir", required=True, type=Path, help="完整测试集根目录"
    )
    batch_parser.add_argument("--output-dir", required=True, type=Path, help="批量输出目录")
    batch_parser.add_argument(
        "--unit-depth",
        default=2,
        type=int,
        help="系统单位相对测试集根目录的层级，默认：2；输入本身是单个系统时用0",
    )
    _add_overwrite_flag(batch_parser)

    batch_all_parser = subparsers.add_parser(
        "batch-all", help="对完整测试集依次执行全部可用稽核项"
    )
    batch_all_parser.add_argument(
        "--dataset-dir", required=True, type=Path, help="完整测试集根目录"
    )
    batch_all_parser.add_argument(
        "--output-dir", required=True, type=Path, help="全部稽核结果的根目录"
    )
    batch_all_parser.add_argument(
        "--unit-depth",
        default=2,
        type=int,
        help="系统单位相对测试集根目录的层级，默认：2；输入本身是单个系统时用0",
    )
    batch_all_parser.add_argument(
        "--rule-only",
        action="store_true",
        help="只运行纯规则稽核项，保证不触发任何模型调用",
    )
    _add_overwrite_flag(batch_all_parser)

    source_batch_parser = subparsers.add_parser(
        "batch-all-source",
        help="从原始 Word/Excel/PDF 开始解析，并执行全部稽核项",
    )
    source_batch_parser.add_argument(
        "--source-dir", required=True, type=Path, help="原始材料根目录"
    )
    source_batch_parser.add_argument(
        "--output-dir", required=True, type=Path, help="全流程结果根目录"
    )
    source_batch_parser.add_argument(
        "--unit-depth",
        default=2,
        type=int,
        help=(
            "系统单位相对原始材料根目录的层级，默认：2；"
            "输入部门目录时用1，输入单个系统时用0"
        ),
    )
    source_batch_parser.add_argument(
        "--rule-only",
        action="store_true",
        help="解析全部原始材料，但只运行纯规则稽核项，不触发模型调用",
    )
    source_batch_parser.add_argument(
        "--skip-evaluation",
        action="store_true",
        help="跳过同级 <部门名>label/ground_truth.v1.json 评估",
    )
    _add_overwrite_flag(source_batch_parser)

    subparsers.add_parser("list", help="列出提取器、规则及成对可用的稽核项")
    return parser


def _read_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError(f"字段 JSON 顶层必须是对象：{path}")
    return payload


def _expand_single_unit_shortcut(argv: list[str]) -> list[str]:
    """把 `main.py <系统目录>` 转换成单系统的完整稽核命令。"""
    commands = {
        "extract",
        "audit",
        "run",
        "batch",
        "batch-all",
        "batch-all-source",
        "list",
    }
    if not argv or argv[0] in commands or argv[0].startswith("-"):
        return argv

    input_dir = Path(argv[0])
    remaining = argv[1:]
    output_dir = Path("output") / f"{input_dir.name}_完整稽核结果"
    expanded = [
        "batch-all",
        "--dataset-dir",
        str(input_dir),
        "--unit-depth",
        "0",
    ]
    if "--output-dir" not in remaining:
        expanded.extend(["--output-dir", str(output_dir)])
    expanded.extend(remaining)
    if "--overwrite" not in remaining:
        expanded.append("--overwrite")
    return expanded


def _compact_terminal_result(result: object) -> object:
    if not isinstance(result, dict):
        return result

    def compact_fields(fields: object) -> object:
        if not isinstance(fields, dict) or not {
            "全量资产清单",
            "定级报告末尾资产",
        }.issubset(fields):
            return fields
        return {
            "全量资产清单条数": len(fields["全量资产清单"]),
            "定级报告末尾资产条数": len(fields["定级报告末尾资产"]),
            "说明": "完整资产记录已写入提取 JSON",
        }

    if "fields" in result:
        return {**result, "fields": compact_fields(result["fields"])}
    return compact_fields(result)


def _paired_item_ids(*, rule_only: bool = False) -> list[str]:
    paired = sorted(
        (set(list_extractor_items()) & set(list_rule_items())) - set(DISABLED_ITEMS)
    )
    if not rule_only:
        return paired
    return [item_id for item_id in paired if not get_rule_spec(item_id).uses_model]


def run_all_batches(
    *,
    dataset_dir: Path,
    output_dir: Path,
    unit_depth: int = 2,
    overwrite: bool = False,
    rule_only: bool = False,
    flatten_single_unit_output: bool = False,
) -> dict[str, object]:
    """依次运行全部已成对注册的稽核项；单项异常不会中断其他项目。"""
    dataset_dir = Path(dataset_dir)
    output_dir = Path(output_dir)
    if not dataset_dir.is_dir():
        raise FileNotFoundError(f"测试集目录不存在：{dataset_dir}")
    if unit_depth < 0:
        raise ValueError("unit_depth 必须大于等于 0")
    if flatten_single_unit_output and unit_depth != 0:
        raise ValueError("flatten_single_unit_output 仅支持 unit_depth=0")
    if output_dir.exists() and not overwrite:
        raise FileExistsError(
            f"完整稽核输出目录已存在：{output_dir}；如需覆盖请使用 --overwrite"
        )

    all_item_ids = _paired_item_ids()
    item_ids = _paired_item_ids(rule_only=rule_only)
    if not item_ids:
        message = (
            "当前没有可运行的纯规则稽核项"
            if rule_only
            else "当前没有提取器与规则成对可用的稽核项"
        )
        raise ValueError(message)
    output_dir.mkdir(parents=True, exist_ok=True)

    item_results: list[dict[str, object]] = []
    for item_id in item_ids:
        item_output_dir = output_dir / item_id
        try:
            summary = run_batch(
                dataset_dir=dataset_dir,
                item_id=item_id,
                output_dir=item_output_dir,
                unit_depth=unit_depth,
                overwrite=overwrite,
                flatten_single_unit_output=flatten_single_unit_output,
            )
            item_results.append(
                {
                    "item_id": item_id,
                    "status": "completed",
                    "classification": str(summary["classification"]),
                    "batch_passed": bool(summary["batch_passed"]),
                    "total_units": summary["total_units"],
                    "status_counts": summary["status_counts"],
                    "audit_counts": summary["audit_counts"],
                    "execution_error_count": int(
                        summary["status_counts"].get("execution_error", 0)
                    ),
                    "summary_file": str(item_output_dir / "批量处理汇总.json"),
                }
            )
        except Exception as error:  # 某一项程序异常时仍继续执行其余稽核项
            item_results.append(
                {
                    "item_id": item_id,
                    "status": "execution_error",
                    "classification": "failed",
                    "reason_code": "execution_error",
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    completed = [item for item in item_results if item["status"] == "completed"]
    execution_errors = [
        item for item in item_results if item["status"] == "execution_error"
    ]
    passed_items = [item for item in item_results if item["classification"] == "passed"]
    failed_items = [item for item in item_results if item["classification"] == "failed"]
    not_applicable_items = [
        item for item in item_results if item["classification"] == "not_applicable"
    ]
    classification = (
        "failed"
        if failed_items
        else "not_applicable"
        if len(not_applicable_items) == len(item_results)
        else "passed"
    )
    execution_error_count = len(execution_errors) + sum(
        int(item.get("execution_error_count", 0)) for item in completed
    )
    result: dict[str, object] = {
        "dataset_directory": str(dataset_dir),
        "output_directory": str(output_dir),
        "unit_depth": unit_depth,
        "execution_mode": "rule_only" if rule_only else "all",
        "excluded_model_items": (
            sorted(set(all_item_ids) - set(item_ids)) if rule_only else []
        ),
        "item_count": len(item_ids),
        "classification": classification,
        "counts": {
            "passed_items": len(passed_items),
            "failed_items": len(failed_items),
            "not_applicable_items": len(not_applicable_items),
            "execution_errors": execution_error_count,
        },
        # 兼容旧调用方：不适用视为“无失败”，精确结果请读 classification。
        "all_passed": classification != "failed",
        "items": item_results,
    }
    write_json(output_dir / "完整稽核汇总.json", result, overwrite=overwrite)
    return result


def format_all_batches_summary(result: dict[str, object]) -> str:
    lines = ["=" * 72, "全部稽核项运行结果", "=" * 72]
    if result.get("execution_mode") == "rule_only":
        excluded = result.get("excluded_model_items", [])
        lines.append(
            f"运行模式：纯规则（已跳过 {len(excluded)} 个可能调用模型的稽核项）"
        )
    for item in result["items"]:
        if item["status"] == "execution_error":
            lines.append(
                f"[{item['item_id']}] ❌ 不通过 | "
                f"原因：程序错误 | {item['error']}"
            )
            continue
        audit_counts = item["audit_counts"]
        status_counts = item["status_counts"]
        item_classification = item["classification"]
        label = {
            "passed": "✅ 全部通过",
            "failed": "❌ 存在未通过项",
            "not_applicable": "⏭️ 不适用",
        }[item_classification]
        other_statuses = "、".join(
            f"{status}={count}"
            for status, count in status_counts.items()
            if status != "success" and count
        ) or "无"
        lines.append(
            f"[{item['item_id']}] {label} | "
            f"通过={audit_counts.get('passed', 0)} | "
            f"不通过={audit_counts.get('failed', 0)} | "
            f"不适用={audit_counts.get('not_applicable', 0)} | "
            f"其他状态={other_statuses}"
        )
    counts = result["counts"]
    lines.extend(
        [
            "-" * 72,
            f"稽核项总数：{result['item_count']}",
            f"全部单位通过的稽核项：{counts['passed_items']}",
            f"存在未通过单位的稽核项：{counts['failed_items']}",
            f"不适用的稽核项：{counts['not_applicable_items']}",
            f"程序错误：{counts['execution_errors']}",
            f"总汇总文件：{Path(str(result['output_directory'])) / '完整稽核汇总.json'}",
            "=" * 72,
        ]
    )
    return "\n".join(lines)


def _validate_source_output_paths(source_dir: Path, output_dir: Path) -> tuple[Path, Path]:
    """校验原件与结果目录隔离，避免覆盖原始材料。"""
    source = Path(source_dir).expanduser().resolve()
    requested_output = Path(output_dir).expanduser()
    if requested_output.is_symlink():
        raise ValueError(f"拒绝使用符号链接作为全流程输出目录：{requested_output}")
    output = requested_output.resolve(strict=False)
    if not source.is_dir():
        raise FileNotFoundError(f"原始材料目录不存在：{source}")
    if output == source or source in output.parents or output in source.parents:
        raise ValueError("原始材料目录和全流程输出目录不能相同或互相包含")
    return source, output


_SOURCE_GENERATED_DIRECTORY_NAMES = frozenset(
    {
        "批量解析结果",
        "解析结果",
        "稽核结果",
        "定级报告解析测试集",
    }
)


def _is_generated_source_directory(path: Path) -> bool:
    name = path.name
    return (
        name in _SOURCE_GENERATED_DIRECTORY_NAMES
        or name.startswith(".")
        or name.endswith("_anydoc解析结果")
        or name.endswith("_完整稽核结果")
        or name.endswith("_全流程结果")
        or name.casefold().endswith("label")
        or "_旧结果备份_" in name
    )


def _discover_source_unit_directories(
    source_dir: Path, unit_depth: int
) -> list[Path]:
    """只遍历到指定层级，并排除已生成的解析、稽核和备份目录。"""
    if unit_depth == 0:
        return [source_dir]

    current_level = [source_dir]
    for _ in range(unit_depth):
        next_level: list[Path] = []
        for parent in current_level:
            for child in parent.iterdir():
                if _is_generated_source_directory(child) or not child.is_dir():
                    continue
                if child.is_symlink():
                    raise ValueError(f"原始材料层级中不允许使用目录符号链接：{child}")
                next_level.append(child)
        next_level.sort(key=lambda path: str(path.relative_to(source_dir)))
        if not next_level:
            raise FileNotFoundError(
                f"在 {source_dir} 下没有找到深度为 {unit_depth} 的原始系统目录"
            )
        current_level = next_level
    return current_level


def _prepare_source_batch_output(output_dir: Path, overwrite: bool) -> Path | None:
    """创建全新的流程输出；覆盖时先把旧结果整体改名备份。"""
    output = Path(output_dir)
    if output.exists():
        if not output.is_dir():
            raise NotADirectoryError(f"全流程输出路径不是目录：{output}")
        if not overwrite:
            raise FileExistsError(
                f"全流程输出目录已存在：{output}；如需重新生成请使用 --overwrite"
            )
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = output.with_name(f"{output.name}_旧结果备份_{timestamp}")
        serial = 2
        while backup.exists():
            backup = output.with_name(
                f"{output.name}_旧结果备份_{timestamp}_{serial}"
            )
            serial += 1
        output.rename(backup)
        output.mkdir(parents=True)
        return backup
    output.mkdir(parents=True)
    return None


def discover_source_evaluation_label_file(source_dir: Path) -> Path:
    """发现 ``<原始目录名>label/ground_truth.v1.json``。"""
    source = Path(source_dir).expanduser().resolve()
    label_dir = source.with_name(f"{source.name}label")
    if label_dir.is_symlink():
        raise ValueError(f"评估标签目录不允许使用符号链接：{label_dir}")
    if not label_dir.exists():
        raise FileNotFoundError(
            f"未找到评估标签目录：{label_dir}；"
            "如需不评估请显式使用 --skip-evaluation"
        )
    if not label_dir.is_dir():
        raise NotADirectoryError(f"评估标签路径不是目录：{label_dir}")
    label_file = ground_truth_path_for_source(source)
    if label_file.is_symlink():
        raise ValueError(f"评估标签文件不允许使用符号链接：{label_file}")
    if not label_file.exists():
        raise FileNotFoundError(
            f"未找到固定的结构化标签文件：{label_file}；"
            f"文件名必须为 {GROUND_TRUTH_FILENAME}"
        )
    if not label_file.is_file():
        raise FileNotFoundError(f"结构化标签路径不是普通文件：{label_file}")
    return label_file.resolve()


def run_source_batch_evaluation(
    *, source_dir: Path, result_dir: Path
) -> dict[str, object]:
    """在原始材料批处理完成后，按同级标签目录自动执行评估。"""
    source = Path(source_dir).expanduser().resolve()
    label_file = discover_source_evaluation_label_file(source)
    summary = evaluate_batch_results(
        source_dir=source,
        result_dir=Path(result_dir),
        label_file=label_file,
    )
    totals = summary["totals"]
    return {
        "status": "completed",
        "label_directory": str(label_file.parent),
        "label_file": str(label_file),
        "output_directory": str(
            Path(str(summary["result_directory"])) / "评估结果"
        ),
        "score": totals["score"],
        "max_score": totals["max_score"],
        "accuracy": totals["accuracy"],
        "incorrect_items": totals["incorrect_items"],
        "truth_only_items": totals["truth_only_items"],
        "prediction_only_items": totals["prediction_only_items"],
        "mismatches": list(summary.get("mismatches", [])),
    }


def format_source_batch_evaluation_summary(result: dict[str, object]) -> str:
    """格式化自动评估的终端摘要。"""
    accuracy = result["accuracy"]
    accuracy_text = "-" if accuracy is None else f"{float(accuracy):.2%}"
    lines = [
        "=" * 72,
        "真实标签三分类评估",
        "=" * 72,
        f"评估标签：{result['label_file']}",
        f"得分：{result['score']} / {result['max_score']}",
        f"准确率：{accuracy_text}",
        f"不一致项：{result['incorrect_items']}",
        f"真实标签有、程序缺失：{result['truth_only_items']}",
        f"程序有、真实标签缺失：{result['prediction_only_items']}",
    ]
    mismatches = [
        mismatch
        for mismatch in result.get("mismatches", [])
        if isinstance(mismatch, dict)
    ]
    if mismatches:
        lines.append("不一致项明细：")
        current_system = None
        for mismatch in mismatches:
            system = str(mismatch.get("system") or "未知系统")
            if system != current_system:
                lines.append(f"  [{system}]")
                current_system = system
            item_id = str(mismatch.get("item_id") or "未知稽核项")
            item_name = str(mismatch.get("item_name") or audit_item_name(item_id)).strip()
            if item_name.startswith(item_id):
                item_name = item_name[len(item_id) :].strip()
            lines.append(
                f"    [{item_id}] {item_name} | "
                f"真实标签={mismatch.get('truth_label', '-')} | "
                f"程序标签={mismatch.get('prediction_label', '-')}"
            )
    lines.extend(
        [
            f"评估结果：{result['output_directory']}",
            "=" * 72,
        ]
    )
    return "\n".join(lines)


def run_source_batch_all(
    *,
    source_dir: Path,
    output_dir: Path,
    unit_depth: int = 2,
    overwrite: bool = False,
    rule_only: bool = False,
) -> dict[str, object]:
    """从原始办公文档开始，逐系统解析后执行全部稽核项。"""
    if unit_depth < 0:
        raise ValueError("unit_depth 必须大于等于 0")

    source, output = _validate_source_output_paths(source_dir, output_dir)
    # 必须先发现原始单位，再创建或备份输出目录，避免任何输出参与单位发现。
    source_units = _discover_source_unit_directories(source, unit_depth)
    previous_output_backup = _prepare_source_batch_output(output, overwrite)
    parsed_root = output / "解析结果"
    audit_root = output / "稽核结果"
    parsed_root.mkdir(parents=True)

    unit_results: list[dict[str, object]] = []
    for index, source_unit in enumerate(source_units, start=1):
        relative_unit = source_unit.relative_to(source)
        result_relative = Path(source.name) if unit_depth == 0 else relative_unit
        parsed_unit = parsed_root / result_relative
        display_unit = result_relative.as_posix()
        print(f"[解析 {index}/{len(source_units)}] {display_unit}")
        try:
            parse_result = run_unit_parse(source_unit, parsed_unit)
            failed_files = parse_result.failed_files
            if parse_result.exit_code == 0:
                parse_status = "success"
            elif parse_result.exit_code == 1:
                parse_status = "partial"
            else:
                parse_status = "error"
            unit_results.append(
                {
                    "unit": display_unit,
                    "source_directory": str(source_unit),
                    "parsed_directory": str(parsed_unit),
                    "status": parse_status,
                    "exit_code": parse_result.exit_code,
                    "failed_file_count": len(failed_files),
                    "execution_error": parse_status == "error",
                    **(
                        {"error": f"解析进程退出码 {parse_result.exit_code}"}
                        if parse_status == "error"
                        else {}
                    ),
                }
            )
            status_label = {
                "success": "完成",
                "partial": "部分文件失败",
                "error": "解析程序失败",
            }[parse_status]
            print(
                f"[解析 {index}/{len(source_units)}] {status_label} | "
                f"失败文件={len(failed_files)} | {display_unit}"
            )
        except Exception as error:  # 单个系统失败不能阻断其他系统和后续稽核
            # 保留单位目录，使各稽核项能够明确输出“缺少文档”，而不是漏掉该系统。
            parsed_unit.mkdir(parents=True, exist_ok=True)
            unit_results.append(
                {
                    "unit": display_unit,
                    "source_directory": str(source_unit),
                    "parsed_directory": str(parsed_unit),
                    "status": "error",
                    "exit_code": None,
                    "failed_file_count": 0,
                    # preview_source 的“没有可解析材料”使用直接 ValueError，属于
                    # 输入问题；JSONDecodeError 等 ValueError 子类仍是程序错误。
                    "execution_error": type(error) is not ValueError,
                    "error": f"{type(error).__name__}: {error}",
                }
            )
            print(
                f"[解析 {index}/{len(source_units)}] 失败，继续处理其他系统 | "
                f"{display_unit} | {type(error).__name__}: {error}"
            )

    parse_counts = {
        status: sum(item["status"] == status for item in unit_results)
        for status in ("success", "partial", "error")
    }
    parse_execution_errors = sum(
        bool(item.get("execution_error")) for item in unit_results
    )

    # 每个系统分别运行全部稽核项。这样一个系统的解析结果、稽核项和汇总
    # 都能通过同一个相对系统路径定位，不再按稽核项把多个系统混排。
    system_audits: list[dict[str, object]] = []
    for index, unit_result in enumerate(unit_results, start=1):
        unit_name = str(unit_result["unit"])
        parsed_unit = Path(str(unit_result["parsed_directory"]))
        system_audit_dir = audit_root / Path(unit_name)
        print(f"[稽核 {index}/{len(unit_results)}] {unit_name}")
        try:
            system_audit = run_all_batches(
                dataset_dir=parsed_unit,
                output_dir=system_audit_dir,
                unit_depth=0,
                overwrite=False,
                rule_only=rule_only,
                flatten_single_unit_output=True,
            )
            system_classification = (
                "failed"
                if unit_result["status"] != "success"
                or system_audit["classification"] == "failed"
                else "not_applicable"
                if system_audit["classification"] == "not_applicable"
                else "passed"
            )
            system_all_passed = system_classification != "failed"
            failed_item_ids = [
                str(item["item_id"])
                for item in system_audit["items"]
                if item["classification"] == "failed"
            ]
            not_applicable_item_ids = [
                str(item["item_id"])
                for item in system_audit["items"]
                if item["classification"] == "not_applicable"
            ]
            execution_error_item_ids = [
                str(item["item_id"])
                for item in system_audit["items"]
                if item["status"] == "execution_error"
            ]
            system_summary_file = system_audit_dir / "系统稽核汇总.json"
            system_summary: dict[str, object] = {
                "unit": unit_name,
                "source_directory": unit_result["source_directory"],
                "parsed_directory": str(parsed_unit),
                "audit_directory": str(system_audit_dir),
                "execution_mode": "rule_only" if rule_only else "all",
                "parse": unit_result,
                "audit": system_audit,
                "classification": system_classification,
                "all_passed": system_all_passed,
            }
            write_json(system_summary_file, system_summary)
            system_audits.append(
                {
                    "unit": unit_name,
                    "status": "completed",
                    "parse_status": unit_result["status"],
                    "audit_directory": str(system_audit_dir),
                    "summary_file": str(system_summary_file),
                    "item_count": system_audit["item_count"],
                    "excluded_model_items": system_audit["excluded_model_items"],
                    "counts": system_audit["counts"],
                    "failed_item_ids": failed_item_ids,
                    "not_applicable_item_ids": not_applicable_item_ids,
                    "execution_error_item_ids": execution_error_item_ids,
                    "classification": system_classification,
                    "all_passed": system_all_passed,
                }
            )
        except Exception as error:  # 一个系统稽核异常不阻断其他系统
            system_audit_dir.mkdir(parents=True, exist_ok=True)
            system_summary_file = system_audit_dir / "系统稽核汇总.json"
            system_summary = {
                "unit": unit_name,
                "source_directory": unit_result["source_directory"],
                "parsed_directory": str(parsed_unit),
                "audit_directory": str(system_audit_dir),
                "execution_mode": "rule_only" if rule_only else "all",
                "parse": unit_result,
                "audit_error": f"{type(error).__name__}: {error}",
                "classification": "failed",
                "all_passed": False,
            }
            write_json(system_summary_file, system_summary)
            system_audits.append(
                {
                    "unit": unit_name,
                    "status": "execution_error",
                    "parse_status": unit_result["status"],
                    "audit_directory": str(system_audit_dir),
                    "summary_file": str(system_summary_file),
                    "error": f"{type(error).__name__}: {error}",
                    "classification": "failed",
                    "all_passed": False,
                }
            )

    passed_systems = [
        item for item in system_audits if item["classification"] == "passed"
    ]
    failed_systems = [
        item for item in system_audits if item["classification"] == "failed"
    ]
    not_applicable_systems = [
        item
        for item in system_audits
        if item["classification"] == "not_applicable"
    ]
    completed_system = next(
        (item for item in system_audits if item["status"] == "completed"), None
    )
    audit_execution_errors = sum(
        1
        if item["status"] == "execution_error"
        else int(item["counts"]["execution_errors"])
        for item in system_audits
    )
    audit_result: dict[str, object] = {
        "output_directory": str(audit_root),
        "execution_mode": "rule_only" if rule_only else "all",
        "item_count": completed_system["item_count"] if completed_system else 0,
        "excluded_model_items": (
            completed_system["excluded_model_items"] if completed_system else []
        ),
        "system_count": len(system_audits),
        "classification": (
            "failed"
            if failed_systems
            else "not_applicable"
            if len(not_applicable_systems) == len(system_audits)
            else "passed"
        ),
        "counts": {
            "passed_systems": len(passed_systems),
            "failed_systems": len(failed_systems),
            "not_applicable_systems": len(not_applicable_systems),
            "execution_errors": audit_execution_errors,
        },
        "all_passed": not failed_systems,
        "systems": system_audits,
    }
    write_json(audit_root / "全部系统汇总.json", audit_result)
    all_passed = parse_counts["success"] == len(source_units) and bool(
        audit_result["all_passed"]
    )
    classification = (
        "failed"
        if not all_passed
        else "not_applicable"
        if audit_result["classification"] == "not_applicable"
        else "passed"
    )
    result: dict[str, object] = {
        "source_directory": str(source),
        "output_directory": str(output),
        "parsed_directory": str(parsed_root),
        "audit_directory": str(audit_root),
        "unit_depth": unit_depth,
        "execution_mode": "rule_only" if rule_only else "all",
        "previous_output_backup": (
            str(previous_output_backup) if previous_output_backup else None
        ),
        "total_units": len(source_units),
        "parse_counts": parse_counts,
        "parse_execution_errors": parse_execution_errors,
        "units": unit_results,
        "audit": audit_result,
        "classification": classification,
        "all_passed": all_passed,
    }
    write_json(output / "全流程汇总.json", result)
    return result


def format_source_batch_all_summary(result: dict[str, object]) -> str:
    counts = result["parse_counts"]
    lines = [
        "=" * 72,
        "原始文件解析与全部稽核结果",
        "=" * 72,
        f"原始材料：{result['source_directory']}",
        f"系统单位：{result['total_units']}",
        (
            "解析结果："
            f"成功={counts['success']} | 部分失败={counts['partial']} | "
            f"失败={counts['error']}"
        ),
        f"解析程序错误：{result['parse_execution_errors']}",
        f"标准 Markdown：{result['parsed_directory']}",
        f"稽核结果：{result['audit_directory']}",
    ]
    if result.get("previous_output_backup"):
        lines.append(f"旧结果备份：{result['previous_output_backup']}")
    audit = result["audit"]
    lines.extend(["-" * 72, "逐系统稽核汇总", "-" * 72])
    for system in audit["systems"]:
        if system["status"] == "execution_error":
            lines.append(
                f"[{system['unit']}] ❌ 不通过 | "
                f"原因：稽核程序错误 | {system['error']} | "
                f"汇总={system['summary_file']}"
            )
            continue
        system_counts = system["counts"]
        label = {
            "passed": "✅ 全部通过",
            "failed": "❌ 存在未通过项",
            "not_applicable": "⏭️ 不适用",
        }[system["classification"]]
        lines.append(
            f"[{system['unit']}] {label} | 解析={system['parse_status']} | "
            f"通过项={system_counts['passed_items']} | "
            f"未通过项={system_counts['failed_items']} | "
            f"不适用项={system_counts['not_applicable_items']} | "
            f"程序错误={system_counts['execution_errors']}"
        )
        lines.append(f"  系统汇总：{system['summary_file']}")
    audit_counts = audit["counts"]
    lines.extend(
        [
            "-" * 72,
            f"全部通过的系统：{audit_counts['passed_systems']}",
            f"存在未通过项的系统：{audit_counts['failed_systems']}",
            f"全部不适用的系统：{audit_counts['not_applicable_systems']}",
            f"稽核程序错误：{audit_counts['execution_errors']}",
            f"全部系统汇总：{Path(str(result['audit_directory'])) / '全部系统汇总.json'}",
            f"全流程汇总：{Path(str(result['output_directory'])) / '全流程汇总.json'}",
            "=" * 72,
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(_expand_single_unit_shortcut(raw_argv))
    try:
        if args.command == "extract":
            result = run_extract(
                item_id=args.item,
                input_unit_dir=args.input_dir,
                output_path=args.output,
                overwrite=args.overwrite,
            )
        elif args.command == "audit":
            result = run_audit(
                item_id=args.item,
                extracted_fields=_read_json_object(args.input),
                output_path=args.output,
                overwrite=args.overwrite,
            )
        elif args.command == "run":
            result = run_pipeline(
                item_id=args.item,
                input_unit_dir=args.input_dir,
                extracted_output_path=args.extracted_output,
                audit_output_path=args.audit_output,
                overwrite=args.overwrite,
            )
        elif args.command == "batch":
            result = run_batch(
                dataset_dir=args.dataset_dir,
                item_id=args.item,
                output_dir=args.output_dir,
                unit_depth=args.unit_depth,
                overwrite=args.overwrite,
            )
            print(format_batch_summary(result, args.output_dir))
            if result["status_counts"].get("execution_error", 0):
                return 2
            return 0 if result["batch_passed"] else 1
        elif args.command == "batch-all":
            result = run_all_batches(
                dataset_dir=args.dataset_dir,
                output_dir=args.output_dir,
                unit_depth=args.unit_depth,
                overwrite=args.overwrite,
                rule_only=args.rule_only,
            )
            print(format_all_batches_summary(result))
            if result["counts"]["execution_errors"]:
                return 2
            return 0 if result["all_passed"] else 1
        elif args.command == "batch-all-source":
            result = run_source_batch_all(
                source_dir=args.source_dir,
                output_dir=args.output_dir,
                unit_depth=args.unit_depth,
                overwrite=args.overwrite,
                rule_only=args.rule_only,
            )
            print(format_source_batch_all_summary(result))
            if args.skip_evaluation:
                print("评估：已按 --skip-evaluation 跳过")
            else:
                evaluation = run_source_batch_evaluation(
                    source_dir=Path(str(result["source_directory"])),
                    result_dir=Path(str(result["output_directory"])),
                )
                print(format_source_batch_evaluation_summary(evaluation))
            audit_counts = result["audit"]["counts"]
            if result["parse_execution_errors"] or audit_counts["execution_errors"]:
                return 2
            return 0 if result["all_passed"] else 1
        else:
            extractor_items = list_extractor_items()
            rule_items = list_rule_items()
            paired_items = _paired_item_ids()
            result = {
                "extractor_items": extractor_items,
                "rule_items": rule_items,
                "paired_items": paired_items,
                "rule_only_items": _paired_item_ids(rule_only=True),
                "model_items": [
                    item_id
                    for item_id in paired_items
                    if get_rule_spec(item_id).uses_model
                ],
                "document_requirements": {
                    item: (
                        list(get_extractor_spec(item).document_requirements[0])
                        if len(get_extractor_spec(item).document_requirements) == 1
                        else [
                            list(group)
                            for group in get_extractor_spec(item).document_requirements
                        ]
                    )
                    for item in extractor_items
                },
            }

        print(json.dumps(_compact_terminal_result(result), ensure_ascii=False, indent=2))
        return 0
    except (
        FileExistsError,
        FileNotFoundError,
        KeyError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
