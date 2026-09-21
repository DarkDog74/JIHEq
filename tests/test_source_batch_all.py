from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

import main as main_module
from parsing_engine import ParseResult


def _audit_summary(
    *,
    all_passed: bool = True,
    classification: str | None = None,
) -> dict[str, object]:
    resolved_classification = classification or ("passed" if all_passed else "failed")
    return {
        "dataset_directory": "/mock/parsed-system",
        "output_directory": "/mock/audit-system",
        "execution_mode": "rule_only",
        "excluded_model_items": [],
        "item_count": 1,
        "classification": resolved_classification,
        "counts": {
            "passed_items": int(resolved_classification == "passed"),
            "failed_items": int(resolved_classification == "failed"),
            "not_applicable_items": int(resolved_classification == "not_applicable"),
            "execution_errors": 0,
        },
        "all_passed": all_passed,
        "items": [
            {
                "item_id": "ZIC-01",
                "status": "completed",
                "classification": resolved_classification,
                "batch_passed": all_passed,
                "total_units": 1,
                "status_counts": {"success": 1},
                "audit_counts": {
                    "passed": int(resolved_classification == "passed"),
                    "failed": int(resolved_classification == "failed"),
                    "not_applicable": int(
                        resolved_classification == "not_applicable"
                    ),
                },
            }
        ],
    }


def _successful_audit_summary() -> dict[str, object]:
    return _audit_summary()


def _run_all_batches_side_effect(*summaries: dict[str, object]):
    """模拟 run_all_batches 的真实落盘行为，供 system-first 编排测试使用。"""
    remaining = iter(summaries)

    def run(**kwargs: object) -> dict[str, object]:
        summary = next(remaining)
        output_dir = Path(kwargs["output_dir"])
        output_dir.mkdir(parents=True)
        (output_dir / "完整稽核汇总.json").write_text(
            json.dumps(summary, ensure_ascii=False),
            encoding="utf-8",
        )
        return summary

    return run


class SourceBatchAllParserTests(unittest.TestCase):
    def test_parser_accepts_raw_source_batch_all_arguments(self) -> None:
        args = main_module.build_parser().parse_args(
            [
                "batch-all-source",
                "--source-dir",
                "/raw/核心网部",
                "--output-dir",
                "/results/核心网部",
                "--unit-depth",
                "1",
                "--rule-only",
                "--overwrite",
            ]
        )

        self.assertEqual(args.command, "batch-all-source")
        self.assertEqual(args.source_dir, Path("/raw/核心网部"))
        self.assertEqual(args.output_dir, Path("/results/核心网部"))
        self.assertEqual(args.unit_depth, 1)
        self.assertTrue(args.rule_only)
        self.assertTrue(args.overwrite)
        self.assertFalse(args.skip_evaluation)

    def test_parser_accepts_skip_evaluation_flag(self) -> None:
        args = main_module.build_parser().parse_args(
            [
                "batch-all-source",
                "--source-dir",
                "/raw/核心网部",
                "--output-dir",
                "/results/核心网部",
                "--skip-evaluation",
            ]
        )

        self.assertTrue(args.skip_evaluation)

    def test_cli_returns_two_for_parse_program_error(self) -> None:
        result = {
            "parse_execution_errors": 1,
            "audit": _successful_audit_summary(),
            "all_passed": False,
        }
        with (
            patch("main.run_source_batch_all", return_value=result),
            patch("main.format_source_batch_all_summary", return_value="summary"),
            patch("builtins.print"),
        ):
            exit_code = main_module.main(
                [
                    "batch-all-source",
                    "--source-dir",
                    "/raw",
                    "--output-dir",
                    "/result",
                    "--skip-evaluation",
                ]
            )

        self.assertEqual(exit_code, 2)

    def test_cli_runs_evaluation_after_source_batch_by_default(self) -> None:
        result = {
            "source_directory": "/raw/核心网部",
            "output_directory": "/result/核心网部_全流程结果",
            "parse_execution_errors": 0,
            "audit": {"counts": {"execution_errors": 0}},
            "all_passed": True,
        }
        evaluation = {"status": "completed"}
        with (
            patch("main.run_source_batch_all", return_value=result),
            patch("main.format_source_batch_all_summary", return_value="summary"),
            patch(
                "main.run_source_batch_evaluation", return_value=evaluation
            ) as run_evaluation,
            patch(
                "main.format_source_batch_evaluation_summary",
                return_value="evaluation",
            ),
            patch("builtins.print"),
        ):
            exit_code = main_module.main(
                [
                    "batch-all-source",
                    "--source-dir",
                    "/raw/核心网部",
                    "--output-dir",
                    "/result/核心网部_全流程结果",
                ]
            )

        self.assertEqual(exit_code, 0)
        run_evaluation.assert_called_once_with(
            source_dir=Path("/raw/核心网部"),
            result_dir=Path("/result/核心网部_全流程结果"),
        )

    def test_cli_skip_evaluation_does_not_call_evaluator(self) -> None:
        result = {
            "source_directory": "/raw/核心网部",
            "output_directory": "/result",
            "parse_execution_errors": 0,
            "audit": {"counts": {"execution_errors": 0}},
            "all_passed": True,
        }
        with (
            patch("main.run_source_batch_all", return_value=result),
            patch("main.format_source_batch_all_summary", return_value="summary"),
            patch("main.run_source_batch_evaluation") as run_evaluation,
            patch("builtins.print"),
        ):
            exit_code = main_module.main(
                [
                    "batch-all-source",
                    "--source-dir",
                    "/raw/核心网部",
                    "--output-dir",
                    "/result",
                    "--skip-evaluation",
                ]
            )

        self.assertEqual(exit_code, 0)
        run_evaluation.assert_not_called()

    def test_cli_returns_two_when_automatic_evaluation_is_invalid(self) -> None:
        result = {
            "source_directory": "/raw/核心网部",
            "output_directory": "/result",
            "parse_execution_errors": 0,
            "audit": {"counts": {"execution_errors": 0}},
            "all_passed": True,
        }
        with (
            patch("main.run_source_batch_all", return_value=result),
            patch("main.format_source_batch_all_summary", return_value="summary"),
            patch(
                "main.run_source_batch_evaluation",
                side_effect=ValueError("标签格式错误"),
            ),
            patch("builtins.print"),
        ):
            exit_code = main_module.main(
                [
                    "batch-all-source",
                    "--source-dir",
                    "/raw/核心网部",
                    "--output-dir",
                    "/result",
                ]
            )

        self.assertEqual(exit_code, 2)


class SourceBatchEvaluationTests(unittest.TestCase):
    def test_missing_sibling_label_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "核心网部"
            source.mkdir()
            result = Path(temp_dir) / "result"

            with patch("main.evaluate_batch_results") as evaluate:
                with self.assertRaisesRegex(FileNotFoundError, "未找到评估标签目录"):
                    main_module.run_source_batch_evaluation(
                        source_dir=source,
                        result_dir=result,
                    )

            evaluate.assert_not_called()

    def test_fixed_ground_truth_json_in_sibling_label_directory_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "核心网部"
            source.mkdir()
            label_dir = root / "核心网部label"
            label_dir.mkdir()
            label_file = label_dir / "ground_truth.v1.json"
            label_file.write_text("{}", encoding="utf-8")
            (label_dir / "其他标签.json").write_text("{}", encoding="utf-8")
            nested = label_dir / "nested"
            nested.mkdir()
            (nested / "ground_truth.v1.json").write_text("{}", encoding="utf-8")

            discovered = main_module.discover_source_evaluation_label_file(source)

            self.assertEqual(discovered, label_file.resolve())

    def test_missing_fixed_ground_truth_json_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "核心网部"
            source.mkdir()
            (root / "核心网部label").mkdir()

            with self.assertRaisesRegex(
                FileNotFoundError,
                "ground_truth\\.v1\\.json",
            ):
                main_module.discover_source_evaluation_label_file(source)

    def test_legacy_markdown_is_not_accepted_as_ground_truth(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "核心网部"
            source.mkdir()
            label_dir = root / "核心网部label"
            label_dir.mkdir()
            (label_dir / "稽核label.md").write_text(
                "| 旧标签 |",
                encoding="utf-8",
            )

            with patch("main.evaluate_batch_results") as evaluate:
                with self.assertRaisesRegex(
                    FileNotFoundError,
                    "ground_truth\\.v1\\.json",
                ):
                    main_module.run_source_batch_evaluation(
                        source_dir=source,
                        result_dir=root / "result",
                    )

            evaluate.assert_not_called()

    def test_dangling_sibling_label_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "核心网部"
            source.mkdir()
            (root / "核心网部label").symlink_to(
                root / "missing-label-directory",
                target_is_directory=True,
            )

            with self.assertRaisesRegex(ValueError, "符号链接"):
                main_module.discover_source_evaluation_label_file(source)

    def test_fixed_ground_truth_json_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "核心网部"
            source.mkdir()
            label_dir = root / "核心网部label"
            label_dir.mkdir()
            outside_label = root / "outside.json"
            outside_label.write_text("{}", encoding="utf-8")
            (label_dir / "ground_truth.v1.json").symlink_to(outside_label)

            with self.assertRaisesRegex(ValueError, "标签文件不允许使用符号链接"):
                main_module.discover_source_evaluation_label_file(source)

    def test_run_evaluation_passes_source_result_and_discovered_label(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "核心网部"
            source.mkdir()
            result = root / "result"
            label_dir = root / "核心网部label"
            label_dir.mkdir()
            label_file = label_dir / "ground_truth.v1.json"
            label_file.write_text("{}", encoding="utf-8")
            evaluation_result = {
                "result_directory": str(result),
                "totals": {
                    "score": 220,
                    "max_score": 230,
                    "accuracy": 220 / 230,
                    "incorrect_items": 10,
                    "truth_only_items": 50,
                    "prediction_only_items": 5,
                },
                "mismatches": [
                    {
                        "system": "系统A-3",
                        "item_id": "ZIC-02",
                        "item_name": "ZIC-02 资产类型符合标准",
                        "truth_label": "不通过",
                        "prediction_label": "通过",
                    }
                ],
            }

            with patch(
                "main.evaluate_batch_results",
                return_value=evaluation_result,
            ) as evaluate:
                summary = main_module.run_source_batch_evaluation(
                    source_dir=source,
                    result_dir=result,
                )

            evaluate.assert_called_once_with(
                source_dir=source.resolve(),
                result_dir=result,
                label_file=label_file.resolve(),
            )
            self.assertEqual(summary["status"], "completed")
            self.assertEqual(summary["score"], 220)
            self.assertEqual(summary["max_score"], 230)
            self.assertEqual(summary["mismatches"], evaluation_result["mismatches"])

    def test_evaluation_terminal_prints_mismatch_details_by_system(self) -> None:
        terminal = main_module.format_source_batch_evaluation_summary(
            {
                "label_file": "/labels/ground_truth.v1.json",
                "output_directory": "/result/评估结果",
                "score": 2,
                "max_score": 4,
                "accuracy": 0.5,
                "incorrect_items": 2,
                "truth_only_items": 0,
                "prediction_only_items": 0,
                "mismatches": [
                    {
                        "system": "系统A-3",
                        "item_id": "ZIC-02",
                        "item_name": "ZIC-02 资产类型符合标准",
                        "truth_label": "不通过",
                        "prediction_label": "通过",
                    },
                    {
                        "system": "系统A-3",
                        "item_id": "FHX-04",
                        "item_name": "FHX-04 检查证明材料",
                        "truth_label": "不通过",
                        "prediction_label": "通过",
                    },
                ],
            }
        )

        self.assertIn("不一致项明细：", terminal)
        self.assertEqual(terminal.count("[系统A-3]"), 1)
        self.assertIn(
            "[ZIC-02] 资产类型符合标准 | 真实标签=不通过 | 程序标签=通过",
            terminal,
        )
        self.assertIn(
            "[FHX-04] 检查证明材料 | 真实标签=不通过 | 程序标签=通过",
            terminal,
        )


class SourceBatchAllTests(unittest.TestCase):
    def test_source_discovery_excludes_generated_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "网管中心"
            real_unit = source / "核心网部" / "系统A"
            real_unit.mkdir(parents=True)
            (source / "批量解析结果" / "核心网部").mkdir(parents=True)
            (source / "传输部" / "系统B_anydoc解析结果").mkdir(parents=True)
            (source / ".缓存" / "伪系统").mkdir(parents=True)
            (source / "核心网部label" / "伪系统").mkdir(parents=True)
            output = root / "result"

            def fake_parse(source_dir: Path, output_dir: Path) -> ParseResult:
                output_dir.mkdir(parents=True)
                return ParseResult(0, {"files": []}, output_dir)

            with (
                patch("main.run_unit_parse", side_effect=fake_parse) as run_parse,
                patch(
                    "main.run_all_batches", return_value=_successful_audit_summary()
                ) as run_audits,
            ):
                result = main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=output,
                    unit_depth=2,
                    rule_only=True,
                )

            run_parse.assert_called_once()
            self.assertEqual(run_parse.call_args.args[0], real_unit.resolve())
            run_audits.assert_called_once_with(
                dataset_dir=output.resolve() / "解析结果" / "核心网部" / "系统A",
                output_dir=output.resolve() / "稽核结果" / "核心网部" / "系统A",
                unit_depth=0,
                overwrite=False,
                rule_only=True,
                flatten_single_unit_output=True,
            )
            self.assertEqual(result["total_units"], 1)

    def test_parser_exit_code_two_is_recorded_as_execution_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            (source / "系统A").mkdir(parents=True)
            output = root / "result"

            def fake_parse(source_dir: Path, output_dir: Path) -> ParseResult:
                output_dir.mkdir(parents=True)
                return ParseResult(2, {"files": [{"status": "dependency_missing"}]}, output_dir)

            with (
                patch("main.run_unit_parse", side_effect=fake_parse),
                patch(
                    "main.run_all_batches", return_value=_successful_audit_summary()
                ),
            ):
                result = main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=output,
                    unit_depth=1,
                    rule_only=True,
                )

            self.assertEqual(result["parse_counts"], {"success": 0, "partial": 0, "error": 1})
            self.assertEqual(result["parse_execution_errors"], 1)
            self.assertEqual(result["units"][0]["status"], "error")
            self.assertEqual(result["units"][0]["exit_code"], 2)

    def test_source_discovery_rejects_directory_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            outside = root / "outside-system"
            source.mkdir()
            outside.mkdir()
            (source / "链接系统").symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "符号链接"):
                main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=root / "result",
                    unit_depth=1,
                    rule_only=True,
                )

            self.assertFalse((root / "result").exists())

    def test_malformed_parser_manifest_is_an_execution_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            (source / "系统A").mkdir(parents=True)

            with (
                patch(
                    "main.run_unit_parse",
                    side_effect=json.JSONDecodeError("清单损坏", "{", 1),
                ),
                patch(
                    "main.run_all_batches", return_value=_successful_audit_summary()
                ),
            ):
                result = main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=root / "result",
                    unit_depth=1,
                    rule_only=True,
                )

            self.assertEqual(result["parse_execution_errors"], 1)

    def test_depth_zero_uses_source_name_as_the_system_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "单个系统"
            source.mkdir()
            output = root / "result"

            def fake_parse(source_dir: Path, output_dir: Path) -> ParseResult:
                self.assertFalse(output_dir.exists())
                output_dir.mkdir(parents=True)
                return ParseResult(0, {"files": []}, output_dir)

            with (
                patch("main.run_unit_parse", side_effect=fake_parse) as run_parse,
                patch(
                    "main.run_all_batches", return_value=_successful_audit_summary()
                ) as run_audits,
            ):
                result = main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=output,
                    unit_depth=0,
                    overwrite=False,
                    rule_only=True,
                )

            parsed_root = output.resolve() / "解析结果"
            parsed_system = parsed_root / "单个系统"
            run_parse.assert_called_once_with(source.resolve(), parsed_system)
            run_audits.assert_called_once_with(
                dataset_dir=parsed_system,
                output_dir=output.resolve() / "稽核结果" / "单个系统",
                unit_depth=0,
                overwrite=False,
                rule_only=True,
                flatten_single_unit_output=True,
            )
            self.assertEqual(result["total_units"], 1)
            self.assertEqual(result["units"][0]["unit"], "单个系统")

    def test_depth_one_preserves_system_directory_mapping_and_rule_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "核心网部"
            system_a = source / "系统A"
            system_b = source / "系统B"
            system_a.mkdir(parents=True)
            system_b.mkdir()
            (system_a / "定级报告.docx").touch()
            (system_b / "资产清单.xlsx").touch()
            output = root / "result"

            def fake_parse(
                source_dir: Path,
                output_dir: Path,
                on_event=None,
            ) -> ParseResult:
                output_dir.mkdir(parents=True)
                (output_dir / "定级报告.md").write_text("# 定级报告", encoding="utf-8")
                return ParseResult(0, {"files": []}, output_dir)

            with (
                patch("main.run_unit_parse", side_effect=fake_parse) as run_parse,
                patch(
                    "main.run_all_batches",
                    side_effect=_run_all_batches_side_effect(
                        _successful_audit_summary(),
                        _audit_summary(all_passed=False),
                    ),
                ) as run_audits,
            ):
                result = main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=output,
                    unit_depth=1,
                    overwrite=False,
                    rule_only=True,
                )

            parsed_root = output.resolve() / "解析结果"
            audit_root = output.resolve() / "稽核结果"
            self.assertEqual(
                run_parse.call_args_list,
                [
                    call(system_a.resolve(), parsed_root / "系统A"),
                    call(system_b.resolve(), parsed_root / "系统B"),
                ],
            )
            self.assertEqual(
                run_audits.call_args_list,
                [
                    call(
                        dataset_dir=parsed_root / "系统A",
                        output_dir=audit_root / "系统A",
                        unit_depth=0,
                        overwrite=False,
                        rule_only=True,
                        flatten_single_unit_output=True,
                    ),
                    call(
                        dataset_dir=parsed_root / "系统B",
                        output_dir=audit_root / "系统B",
                        unit_depth=0,
                        overwrite=False,
                        rule_only=True,
                        flatten_single_unit_output=True,
                    ),
                ],
            )
            self.assertEqual(result["parse_counts"], {"success": 2, "partial": 0, "error": 0})
            self.assertEqual(
                [Path(item["parsed_directory"]) for item in result["units"]],
                [parsed_root / "系统A", parsed_root / "系统B"],
            )
            self.assertFalse(result["all_passed"])
            self.assertTrue((audit_root / "系统A" / "系统稽核汇总.json").is_file())
            self.assertTrue((audit_root / "系统B" / "系统稽核汇总.json").is_file())
            all_systems_summary = audit_root / "全部系统汇总.json"
            self.assertTrue(all_systems_summary.is_file())
            serialized_summary = all_systems_summary.read_text(encoding="utf-8")
            self.assertIn("系统A", serialized_summary)
            self.assertIn("系统B", serialized_summary)
            self.assertTrue((output / "全流程汇总.json").is_file())

    def test_one_parse_error_continues_and_creates_placeholder_for_audits(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            (source / "系统A").mkdir(parents=True)
            (source / "系统B").mkdir()
            output = root / "result"

            def fake_parse(
                source_dir: Path,
                output_dir: Path,
                on_event=None,
            ) -> ParseResult:
                if source_dir.name == "系统A":
                    raise RuntimeError("模拟 anydoc 失败")
                output_dir.mkdir(parents=True)
                return ParseResult(1, {"files": [{"status": "failed"}]}, output_dir)

            with (
                patch("main.run_unit_parse", side_effect=fake_parse) as run_parse,
                patch(
                    "main.run_all_batches", return_value=_successful_audit_summary()
                ) as run_audits,
            ):
                result = main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=output,
                    unit_depth=1,
                    overwrite=False,
                    rule_only=False,
                )

            parsed_root = output.resolve() / "解析结果"
            self.assertEqual(run_parse.call_count, 2)
            self.assertTrue((parsed_root / "系统A").is_dir())
            self.assertTrue((parsed_root / "系统B").is_dir())
            audit_root = output.resolve() / "稽核结果"
            self.assertEqual(
                run_audits.call_args_list,
                [
                    call(
                        dataset_dir=parsed_root / "系统A",
                        output_dir=audit_root / "系统A",
                        unit_depth=0,
                        overwrite=False,
                        rule_only=False,
                        flatten_single_unit_output=True,
                    ),
                    call(
                        dataset_dir=parsed_root / "系统B",
                        output_dir=audit_root / "系统B",
                        unit_depth=0,
                        overwrite=False,
                        rule_only=False,
                        flatten_single_unit_output=True,
                    ),
                ],
            )
            self.assertEqual(
                result["parse_counts"], {"success": 0, "partial": 1, "error": 1}
            )
            self.assertEqual(
                [item["status"] for item in result["units"]], ["error", "partial"]
            )
            self.assertIn("RuntimeError", result["units"][0]["error"])
            self.assertEqual(result["units"][1]["exit_code"], 1)
            self.assertEqual(result["units"][1]["failed_file_count"], 1)
            self.assertFalse(result["all_passed"])

    def test_terminal_and_overall_summary_are_grouped_by_system(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "核心网部"
            (source / "系统A").mkdir(parents=True)
            (source / "系统B").mkdir()
            output = root / "result"

            def fake_parse(source_dir: Path, output_dir: Path) -> ParseResult:
                output_dir.mkdir(parents=True)
                return ParseResult(0, {"files": []}, output_dir)

            with (
                patch("main.run_unit_parse", side_effect=fake_parse),
                patch(
                    "main.run_all_batches",
                    side_effect=_run_all_batches_side_effect(
                        _successful_audit_summary(),
                        _audit_summary(all_passed=False),
                    ),
                ),
            ):
                result = main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=output,
                    unit_depth=1,
                    rule_only=True,
                )

            terminal = main_module.format_source_batch_all_summary(result)
            self.assertIn("系统A", terminal)
            self.assertIn("系统B", terminal)
            self.assertLess(terminal.index("系统A"), terminal.index("系统B"))
            self.assertNotIn("不通过稽核项", terminal)
            self.assertIn("全部系统汇总.json", terminal)

            overall = json.loads((output / "全流程汇总.json").read_text(encoding="utf-8"))
            serialized_overall = json.dumps(overall, ensure_ascii=False)
            self.assertIn("系统A", serialized_overall)
            self.assertIn("系统B", serialized_overall)
            self.assertFalse(overall["all_passed"])

    def test_system_first_rollup_counts_and_displays_all_three_classifications(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "核心网部"
            (source / "全部不适用系统").mkdir(parents=True)
            (source / "存在失败系统").mkdir()
            output = root / "result"

            def fake_parse(source_dir: Path, output_dir: Path) -> ParseResult:
                output_dir.mkdir(parents=True)
                return ParseResult(0, {"files": []}, output_dir)

            with (
                patch("main.run_unit_parse", side_effect=fake_parse),
                patch(
                    "main.run_all_batches",
                    side_effect=_run_all_batches_side_effect(
                        _audit_summary(
                            all_passed=True,
                            classification="not_applicable",
                        ),
                        _audit_summary(all_passed=False, classification="failed"),
                    ),
                ),
            ):
                result = main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=output,
                    unit_depth=1,
                    rule_only=True,
                )

            systems = {system["unit"]: system for system in result["audit"]["systems"]}
            self.assertEqual(
                systems["全部不适用系统"]["classification"],
                "not_applicable",
            )
            self.assertEqual(systems["存在失败系统"]["classification"], "failed")
            self.assertEqual(
                systems["全部不适用系统"]["counts"],
                {
                    "passed_items": 0,
                    "failed_items": 0,
                    "not_applicable_items": 1,
                    "execution_errors": 0,
                },
            )
            self.assertEqual(
                result["audit"]["counts"],
                {
                    "passed_systems": 0,
                    "failed_systems": 1,
                    "not_applicable_systems": 1,
                    "execution_errors": 0,
                },
            )

            terminal = main_module.format_source_batch_all_summary(result)
            self.assertRegex(
                terminal,
                r"\[全部不适用系统\] ⏭️ (?:全部)?不适用",
            )
            self.assertIn("通过项=0 | 未通过项=0 | 不适用项=1", terminal)
            self.assertIn("不适用的系统：1", terminal)

            system_summary = json.loads(
                (
                    output
                    / "稽核结果"
                    / "全部不适用系统"
                    / "系统稽核汇总.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(system_summary["classification"], "not_applicable")

    def test_existing_output_without_overwrite_is_rejected_before_work_starts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            (source / "系统A").mkdir(parents=True)
            output = root / "result"
            output.mkdir()

            with (
                patch("main.run_unit_parse") as run_parse,
                patch("main.run_all_batches") as run_audits,
            ):
                with self.assertRaises(FileExistsError):
                    main_module.run_source_batch_all(
                        source_dir=source,
                        output_dir=output,
                        unit_depth=1,
                        overwrite=False,
                        rule_only=False,
                    )

            run_parse.assert_not_called()
            run_audits.assert_not_called()

    def test_overwrite_backs_up_whole_output_and_removes_stale_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            (source / "系统A").mkdir(parents=True)
            output = root / "result"
            stale = output / "稽核结果" / "已下线稽核项" / "旧结果.json"
            stale.parent.mkdir(parents=True)
            stale.write_text("old", encoding="utf-8")

            def fake_parse(
                source_dir: Path,
                output_dir: Path,
                on_event=None,
            ) -> ParseResult:
                output_dir.mkdir(parents=True)
                return ParseResult(0, {"files": []}, output_dir)

            with (
                patch("main.run_unit_parse", side_effect=fake_parse),
                patch(
                    "main.run_all_batches", return_value=_successful_audit_summary()
                ),
            ):
                main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=output,
                    unit_depth=1,
                    overwrite=True,
                    rule_only=False,
                )

            backups = sorted(root.glob("result_旧结果备份_*"))
            self.assertEqual(len(backups), 1)
            self.assertTrue(
                (backups[0] / "稽核结果" / "已下线稽核项" / "旧结果.json").is_file()
            )
            self.assertFalse(stale.exists())
            self.assertTrue((output / "全流程汇总.json").is_file())

    def test_rejects_output_nested_inside_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            (source / "系统A").mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, "不能|nested|包含"):
                main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=source / "result",
                    unit_depth=1,
                    overwrite=False,
                    rule_only=False,
                )

    def test_rejects_source_nested_inside_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "container"
            source = output / "source"
            (source / "系统A").mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, "不能|nested|包含"):
                main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=output,
                    unit_depth=1,
                    overwrite=False,
                    rule_only=False,
                )

    def test_rejects_symlink_output_before_following_its_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source"
            (source / "系统A").mkdir(parents=True)
            actual_output = root / "actual-output"
            actual_output.mkdir()
            linked_output = root / "linked-output"
            linked_output.symlink_to(actual_output, target_is_directory=True)

            with self.assertRaisesRegex(ValueError, "符号链接"):
                main_module.run_source_batch_all(
                    source_dir=source,
                    output_dir=linked_output,
                    unit_depth=1,
                    overwrite=True,
                    rule_only=False,
                )

            self.assertTrue(actual_output.is_dir())


if __name__ == "__main__":
    unittest.main()
