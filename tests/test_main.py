from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from main import (
    _compact_terminal_result,
    _expand_single_unit_shortcut,
    _paired_item_ids,
    build_parser,
    run_all_batches,
)


class MainOutputTests(unittest.TestCase):
    def test_compacts_dj_09_asset_lists_for_terminal_only(self) -> None:
        fields = {
            "全量资产清单": [{"资产IP": "10.0.0.1"}],
            "定级报告末尾资产": [{"资产IP": "10.0.0.1"}],
        }
        result = _compact_terminal_result(
            {"item_id": "DJ-09", "fields": fields, "audit": {"passed": True}}
        )
        self.assertEqual(result["fields"]["全量资产清单条数"], 1)
        self.assertNotIn("全量资产清单", result["fields"])
        self.assertIs(result["audit"]["passed"], True)

    def test_leaves_small_existing_results_unchanged(self) -> None:
        result = {"社会影响力赋值": 2}
        self.assertIs(_compact_terminal_result(result), result)

    def test_parser_accepts_batch_all_command(self) -> None:
        args = build_parser().parse_args(
            [
                "batch-all",
                "--dataset-dir",
                "/dataset",
                "--output-dir",
                "/output",
            ]
        )
        self.assertEqual(args.command, "batch-all")
        self.assertEqual(args.unit_depth, 2)
        self.assertFalse(args.rule_only)

    def test_parser_accepts_rule_only_batch_all_mode(self) -> None:
        args = build_parser().parse_args(
            [
                "batch-all",
                "--dataset-dir",
                "/dataset",
                "--output-dir",
                "/output",
                "--rule-only",
            ]
        )

        self.assertTrue(args.rule_only)

    def test_rule_only_mode_excludes_every_model_capable_item(self) -> None:
        model_items = {
            "DJ-01",
            "DJ-08",
            "DJ-10",
            "FX-06",
            "FX-11",
            "STB-00",
            "STB-04",
            "YLBG-05",
        }

        all_items = set(_paired_item_ids())
        rule_items = set(_paired_item_ids(rule_only=True))

        self.assertEqual(all_items - rule_items, model_items)
        self.assertEqual(len(rule_items), 46)

    def test_single_folder_argument_expands_to_all_audits_for_that_unit(self) -> None:
        args = _expand_single_unit_shortcut(["/data/部门A/系统一"])
        parsed = build_parser().parse_args(args)

        self.assertEqual(parsed.command, "batch-all")
        self.assertEqual(parsed.dataset_dir, Path("/data/部门A/系统一"))
        self.assertEqual(parsed.unit_depth, 0)
        self.assertEqual(parsed.output_dir, Path("output/系统一_完整稽核结果"))
        self.assertTrue(parsed.overwrite)

    def test_single_folder_shortcut_allows_custom_output_directory(self) -> None:
        args = _expand_single_unit_shortcut(
            ["/data/系统一", "--output-dir", "/results/本次结果"]
        )
        parsed = build_parser().parse_args(args)
        self.assertEqual(parsed.output_dir, Path("/results/本次结果"))

    def test_batch_all_continues_after_one_item_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset = root / "dataset"
            dataset.mkdir()

            def fake_run_batch(**kwargs):
                if kwargs["item_id"] == "FX-18":
                    raise ValueError("模拟异常")
                return {
                    "classification": "failed",
                    "batch_passed": False,
                    "total_units": 2,
                    "status_counts": {"success": 1, "missing_document": 1},
                    "audit_counts": {
                        "passed": 1,
                        "failed": 1,
                        "not_applicable": 0,
                    },
                }

            with (
                patch("main._paired_item_ids", return_value=["FX-16", "FX-18"]),
                patch("main.run_batch", side_effect=fake_run_batch),
            ):
                result = run_all_batches(
                    dataset_dir=dataset,
                    output_dir=root / "output",
                )

            self.assertEqual(result["item_count"], 2)
            self.assertEqual(result["counts"]["failed_items"], 2)
            self.assertEqual(result["counts"]["execution_errors"], 1)
            self.assertEqual(
                [item["classification"] for item in result["items"]],
                ["failed", "failed"],
            )
            self.assertTrue((root / "output" / "完整稽核汇总.json").is_file())

    def test_rule_only_batch_records_excluded_model_items(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            dataset = root / "dataset"
            dataset.mkdir()
            summary = {
                "classification": "passed",
                "batch_passed": True,
                "total_units": 1,
                "status_counts": {"success": 1},
                "audit_counts": {
                    "passed": 1,
                    "failed": 0,
                    "not_applicable": 0,
                },
            }

            def paired_items(*, rule_only=False):
                return ["DJ-02"] if rule_only else ["DJ-01", "DJ-02"]

            with (
                patch("main._paired_item_ids", side_effect=paired_items),
                patch("main.run_batch", return_value=summary) as run_batch,
            ):
                result = run_all_batches(
                    dataset_dir=dataset,
                    output_dir=root / "output",
                    rule_only=True,
                )

            self.assertEqual(result["execution_mode"], "rule_only")
            self.assertEqual(result["excluded_model_items"], ["DJ-01"])
            self.assertEqual([item["item_id"] for item in result["items"]], ["DJ-02"])
            self.assertEqual(run_batch.call_count, 1)


if __name__ == "__main__":
    unittest.main()
