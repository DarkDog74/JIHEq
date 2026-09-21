from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from core.batch_process import format_batch_summary, run_batch
from tools.evaluate_batch_results import classify_prediction_item
from extractors import ExtractorNotApplicableError, RequiredDocumentNotFoundError
from main import format_all_batches_summary, run_all_batches


class BatchThreeWayClassificationTests(unittest.TestCase):
    def test_batch_classifies_every_outcome_as_pass_fail_or_not_applicable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            output = root / "output"
            for name in (
                "business-fail",
                "execution-error",
                "extraction-error",
                "missing",
                "not-applicable",
                "pass",
            ):
                (dataset / name).mkdir(parents=True)

            def select_documents(_spec: object, unit: Path) -> list[Path]:
                if unit.name == "missing":
                    raise RequiredDocumentNotFoundError(
                        "缺少目标文档：测试文档",
                        requirement="测试文档",
                    )
                return [unit / "测试文档.md"]

            def extract(document: Path) -> dict[str, str]:
                case = document.parent.name
                if case == "extraction-error":
                    raise ExtractorNotApplicableError("模拟提取异常")
                if case == "execution-error":
                    raise RuntimeError("模拟程序异常")
                return {"case": case}

            def audit(fields: dict[str, str]) -> dict[str, object]:
                case = fields["case"]
                if case == "not-applicable":
                    # applicable 是分类的优先条件，不应再受 passed 布尔值影响。
                    return {
                        "passed": False,
                        "applicable": False,
                        "message": "本项不适用",
                    }
                passed = case == "pass"
                return {
                    "passed": passed,
                    "applicable": True,
                    "message": "通过" if passed else "业务不通过",
                }

            extractor_spec = SimpleNamespace(
                document_name_contains=("测试文档",),
                document_requirements=(("测试文档",),),
                extractor=extract,
            )
            rule_spec = SimpleNamespace(rule=audit)
            with (
                patch("core.batch_process.get_extractor_spec", return_value=extractor_spec),
                patch("core.batch_process.get_rule_spec", return_value=rule_spec),
                patch("core.batch_process.select_input_documents", side_effect=select_documents),
            ):
                summary = run_batch(
                    dataset_dir=dataset,
                    item_id="DJ-02",
                    output_dir=output,
                    unit_depth=1,
                )

            runs = {record["unit"]: record for record in summary["runs"]}
            self.assertEqual(runs["pass"]["classification"], "passed")
            self.assertEqual(runs["business-fail"]["classification"], "failed")
            self.assertEqual(
                runs["not-applicable"]["classification"], "not_applicable"
            )
            self.assertEqual(runs["missing"]["classification"], "failed")
            self.assertEqual(runs["extraction-error"]["classification"], "failed")
            self.assertEqual(runs["execution-error"]["classification"], "failed")
            self.assertEqual(runs["missing"]["status"], "missing_document")
            self.assertEqual(runs["extraction-error"]["status"], "extraction_error")
            self.assertEqual(runs["execution-error"]["status"], "execution_error")
            self.assertEqual(
                summary["audit_counts"],
                {"passed": 1, "failed": 4, "not_applicable": 1},
            )

            for unit, classification in (
                ("pass", "passed"),
                ("business-fail", "failed"),
                ("not-applicable", "not_applicable"),
            ):
                persisted = json.loads(
                    (output / unit / "DJ-02" / "稽核结果.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(persisted["classification"], classification)

            terminal = format_batch_summary(summary, output)
            self.assertIn("[DJ-02] ✅ 通过 | pass", terminal)
            self.assertIn("[DJ-02] ❌ 不通过 | business-fail", terminal)
            self.assertIn("[DJ-02] ⏭️ 不适用 | not-applicable", terminal)
            self.assertIn("通过：1", terminal)
            self.assertIn("不通过（含缺材料/异常）：4", terminal)
            self.assertIn("不适用：1", terminal)

    def test_all_not_applicable_batch_keeps_compatibility_boolean_true(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            extractor_spec = SimpleNamespace(
                document_name_contains=("测试文档",),
                document_requirements=(("测试文档",),),
                extractor=lambda _document: {},
            )
            rule_spec = SimpleNamespace(
                rule=lambda _fields: {
                    "passed": True,
                    "applicable": False,
                    "message": "本项不适用",
                }
            )
            with (
                patch("core.batch_process.get_extractor_spec", return_value=extractor_spec),
                patch("core.batch_process.get_rule_spec", return_value=rule_spec),
                patch(
                    "core.batch_process.select_input_documents",
                    return_value=[dataset / "测试文档.md"],
                ),
            ):
                summary = run_batch(
                    dataset_dir=dataset,
                    item_id="DJ-02",
                    output_dir=root / "output",
                    unit_depth=0,
                )

            self.assertEqual(summary["classification"], "not_applicable")
            self.assertTrue(summary["batch_passed"])
            self.assertEqual(
                summary["audit_counts"],
                {"passed": 0, "failed": 0, "not_applicable": 1},
            )


class AllBatchesThreeWayClassificationTests(unittest.TestCase):
    @staticmethod
    def _summary(classification: str) -> dict[str, object]:
        return {
            "classification": classification,
            "batch_passed": classification != "failed",
            "total_units": 1,
            "status_counts": {"success": 1},
            "audit_counts": {
                "passed": int(classification == "passed"),
                "failed": int(classification == "failed"),
                "not_applicable": int(classification == "not_applicable"),
            },
        }

    def test_all_batches_preserves_three_way_item_counts_and_terminal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            summaries = {
                "PASS": self._summary("passed"),
                "FAIL": self._summary("failed"),
                "NA": self._summary("not_applicable"),
            }

            def run_one(**kwargs: object) -> dict[str, object]:
                item_id = str(kwargs["item_id"])
                if item_id == "ERROR":
                    raise RuntimeError("模拟程序异常")
                return summaries[item_id]

            with (
                patch(
                    "main._paired_item_ids",
                    return_value=["PASS", "FAIL", "NA", "ERROR"],
                ),
                patch("main.run_batch", side_effect=run_one),
            ):
                result = run_all_batches(
                    dataset_dir=dataset,
                    output_dir=root / "output",
                )

            items = {item["item_id"]: item for item in result["items"]}
            self.assertEqual(items["PASS"]["classification"], "passed")
            self.assertEqual(items["FAIL"]["classification"], "failed")
            self.assertEqual(items["NA"]["classification"], "not_applicable")
            self.assertEqual(items["ERROR"]["classification"], "failed")
            self.assertEqual(result["classification"], "failed")
            self.assertEqual(
                result["counts"],
                {
                    "passed_items": 1,
                    "failed_items": 2,
                    "not_applicable_items": 1,
                    "execution_errors": 1,
                },
            )

            terminal = format_all_batches_summary(result)
            self.assertIn("[PASS] ✅ 全部通过", terminal)
            self.assertIn("[FAIL] ❌ 存在未通过项", terminal)
            self.assertIn("[NA] ⏭️ 不适用", terminal)
            self.assertIn("不适用的稽核项：1", terminal)

    def test_evaluation_prefers_explicit_classification(self) -> None:
        prediction = classify_prediction_item(
            {
                "item_id": "FX-02",
                "status": "completed",
                "classification": "not_applicable",
                # 兼容字段故意给出相反值，确认不再影响新结果。
                "batch_passed": False,
            }
        )

        self.assertEqual(prediction.label, "不适用")
        self.assertEqual(prediction.prediction_source, "classification")

    def test_all_not_applicable_items_keep_all_passed_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            with (
                patch("main._paired_item_ids", return_value=["NA"]),
                patch(
                    "main.run_batch",
                    return_value=self._summary("not_applicable"),
                ),
            ):
                result = run_all_batches(
                    dataset_dir=dataset,
                    output_dir=root / "output",
                )

            self.assertEqual(result["classification"], "not_applicable")
            self.assertTrue(result["all_passed"])
            self.assertEqual(
                result["counts"],
                {
                    "passed_items": 0,
                    "failed_items": 0,
                    "not_applicable_items": 1,
                    "execution_errors": 0,
                },
            )


if __name__ == "__main__":
    unittest.main()
