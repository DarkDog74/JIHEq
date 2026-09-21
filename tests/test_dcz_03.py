from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audit_rules.dcz_03 import audit_dcz_03
from extractors.dcz_03 import DCZ03ExtractionError, extract_dcz_03
from extractors.registry import get_extractor_spec


class DCZ03Tests(unittest.TestCase):
    def test_uses_unit_directory_input_and_lists_three_standard_documents(self) -> None:
        spec = get_extractor_spec("DCZ-03")

        self.assertEqual(spec.input_mode, "unit_directory")
        self.assertEqual(
            spec.document_requirements,
            (("应急预案.md",), ("演练方案.md",), ("应急演练报告.md",)),
        )

    def test_three_standard_files_pass_without_template_or_structure_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unit = Path(temporary)
            (unit / "应急预案.md").write_text("任意内容", encoding="utf-8")
            (unit / "演练方案.md").write_text("", encoding="utf-8")
            (unit / "应急演练报告.md").write_text("无固定章节", encoding="utf-8")

            fields = extract_dcz_03(unit)
            result = audit_dcz_03(fields)

        self.assertTrue(result["passed"])
        self.assertEqual(result["reason_code"], "passed")
        self.assertFalse(fields["检查内容质量"])
        self.assertFalse(result["content_quality_checked"])
        self.assertEqual(fields["缺失材料"], [])

    def test_missing_materials_are_all_reported_as_business_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unit = Path(temporary)
            (unit / "应急预案.md").write_text("# 应急预案", encoding="utf-8")

            fields = extract_dcz_03(unit)
            result = audit_dcz_03(fields)

        self.assertFalse(result["passed"])
        self.assertEqual(result["reason_code"], "business_rule_failed")
        self.assertEqual(fields["缺失材料"], ["演练方案", "应急演练报告"])
        self.assertEqual(result["issues"], ["缺少材料：演练方案、应急演练报告"])

    def test_historical_similar_and_nested_files_do_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unit = Path(temporary)
            nested = unit / "历史材料"
            nested.mkdir()
            (unit / "上一份应急预案.md").write_text("旧预案", encoding="utf-8")
            (unit / "应急演练方案.md").write_text("近似名称", encoding="utf-8")
            (nested / "应急演练报告.md").write_text("子目录报告", encoding="utf-8")

            fields = extract_dcz_03(unit)

        self.assertEqual(
            fields["材料存在性"],
            {"应急预案": False, "演练方案": False, "应急演练报告": False},
        )

    def test_invalid_or_unreadable_unit_directory_is_extraction_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "不存在"
            ordinary_file = root / "不是目录.md"
            ordinary_file.write_text("x", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                extract_dcz_03(missing)
            with self.assertRaises(NotADirectoryError):
                extract_dcz_03(ordinary_file)
            with self.assertRaisesRegex(DCZ03ExtractionError, "需要一个系统目录"):
                extract_dcz_03((root,))
            with patch.object(Path, "iterdir", side_effect=PermissionError("拒绝访问")):
                with self.assertRaisesRegex(DCZ03ExtractionError, "系统目录无法读取"):
                    extract_dcz_03(root)

    def test_rule_rejects_inconsistent_extracted_fields(self) -> None:
        fields = {
            "材料存在性": {
                "应急预案": True,
                "演练方案": False,
                "应急演练报告": False,
            },
            "匹配文件": {
                "应急预案": [],
                "演练方案": [],
                "应急演练报告": [],
            },
            "缺失材料": ["演练方案", "应急演练报告"],
        }

        with self.assertRaisesRegex(ValueError, "匹配文件与存在性不一致"):
            audit_dcz_03(fields)


if __name__ == "__main__":
    unittest.main()
