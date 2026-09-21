from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from audit_rules.dcz_04 import audit_dcz_04
from core.batch_process import run_batch
from extractors.dcz_04 import extract_dcz_04, extract_dcz_04_data
from extractors.registry import get_extractor_spec


class DCZ04Tests(unittest.TestCase):
    def test_both_current_standard_markdown_files_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "符合性评测表.md").write_text("# 表", encoding="utf-8")
            (root / "符合性评测报告.md").write_text("# 报告", encoding="utf-8")

            fields = extract_dcz_04(root)
            result = audit_dcz_04(fields)

        self.assertTrue(result["passed"])
        self.assertTrue(result["applicable"])
        self.assertEqual(result["present_count"], 2)
        self.assertEqual(result["missing_materials"], [])
        self.assertEqual(
            fields["匹配文件"],
            {
                "符合性评测表": "符合性评测表.md",
                "符合性评测报告": "符合性评测报告.md",
            },
        )

    def test_one_missing_is_normal_business_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "符合性评测表.md").write_text("# 表", encoding="utf-8")

            fields = extract_dcz_04_data(root)
            result = audit_dcz_04(fields)

        self.assertFalse(result["passed"])
        self.assertTrue(result["applicable"])
        self.assertEqual(result["reason_code"], "business_rule_failed")
        self.assertEqual(result["missing_count"], 1)
        self.assertEqual(result["missing_materials"], ["符合性评测报告.md"])
        self.assertEqual(result["issues"], ["缺少符合性评测报告.md"])

    def test_both_missing_are_reported_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fields = extract_dcz_04_data(directory)
            result = audit_dcz_04(fields)

        self.assertFalse(result["passed"])
        self.assertEqual(result["present_count"], 0)
        self.assertEqual(
            result["missing_materials"],
            ["符合性评测表.md", "符合性评测报告.md"],
        )
        self.assertEqual(len(result["issues"]), 2)

    def test_previous_and_history_files_do_not_fill_current_materials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "上一份符合性评测表.md").write_text("# 旧表", encoding="utf-8")
            (root / "历史符合性评测报告_2025.md").write_text(
                "# 旧报告", encoding="utf-8"
            )

            fields = extract_dcz_04_data(root)
            result = audit_dcz_04(fields)

        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_count"], 2)
        self.assertEqual(
            fields["匹配文件"],
            {"符合性评测表": None, "符合性评测报告": None},
        )

    def test_same_named_file_in_nested_directory_does_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "附件"
            nested.mkdir()
            (nested / "符合性评测表.md").write_text("# 附件", encoding="utf-8")

            result = audit_dcz_04(extract_dcz_04_data(root))

        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_count"], 2)

    def test_rule_rejects_inconsistent_extraction_contract(self) -> None:
        with self.assertRaisesRegex(ValueError, "匹配文件与存在性矛盾"):
            audit_dcz_04(
                {
                    "材料存在性": {"符合性评测表": True, "符合性评测报告": False},
                    "匹配文件": {"符合性评测表": None, "符合性评测报告": None},
                    "缺失材料": ["符合性评测报告.md"],
                }
            )

    def test_registration_uses_unit_directory_mode(self) -> None:
        spec = get_extractor_spec("DCZ-04")
        self.assertEqual(spec.input_mode, "unit_directory")

    def test_batch_treats_missing_materials_as_business_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            unit = dataset / "system"
            unit.mkdir(parents=True)

            summary = run_batch(
                dataset_dir=dataset,
                item_id="DCZ-04",
                output_dir=root / "output",
                unit_depth=1,
            )

        self.assertEqual(summary["status_counts"], {"success": 1})
        self.assertEqual(
            summary["audit_counts"],
            {"passed": 0, "failed": 1, "not_applicable": 0},
        )
        self.assertEqual(summary["runs"][0]["missing_count"], 2)


if __name__ == "__main__":
    unittest.main()
