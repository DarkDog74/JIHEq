from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audit_rules.dcz_01 import audit_dcz_01
from extractors.dcz_01 import DCZ01ExtractionError, extract_dcz_01
from extractors.registry import get_extractor_spec


class DCZ01Tests(unittest.TestCase):
    def test_uses_unit_directory_input_and_lists_three_standard_documents(self) -> None:
        spec = get_extractor_spec("DCZ-01")

        self.assertEqual(spec.input_mode, "unit_directory")
        self.assertEqual(
            spec.document_requirements,
            (("备案信息表.md",), ("定级报告.md",), ("资产清单.md",)),
        )

    def test_three_independent_standard_markdown_files_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unit = Path(temporary)
            for file_name in ("备案信息表.md", "定级报告.md", "资产清单.md"):
                (unit / file_name).write_text(f"# {file_name}\n", encoding="utf-8")

            fields = extract_dcz_01(unit)
            result = audit_dcz_01(fields)

        self.assertEqual(
            fields["材料存在性"],
            {"备案信息表": True, "定级报告": True, "资产清单": True},
        )
        self.assertEqual(
            fields["匹配文件"],
            {
                "备案信息表": ["备案信息表.md"],
                "定级报告": ["定级报告.md"],
                "资产清单": ["资产清单.md"],
            },
        )
        self.assertEqual(fields["缺失材料"], [])
        self.assertTrue(result["passed"])
        self.assertEqual(result["required_count"], 3)
        self.assertEqual(result["missing_count"], 0)

    def test_missing_business_documents_are_all_returned_and_rule_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unit = Path(temporary)
            (unit / "定级报告.md").write_text("# 定级报告\n", encoding="utf-8")

            fields = extract_dcz_01(unit)
            result = audit_dcz_01(fields)

        self.assertEqual(fields["缺失材料"], ["备案信息表", "资产清单"])
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason_code"], "business_rule_failed")
        self.assertEqual(result["missing_types"], ["备案信息表", "资产清单"])
        self.assertEqual(result["issues"], ["缺少材料：备案信息表、资产清单"])

    def test_similar_historical_nested_or_combined_files_do_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            unit = Path(temporary)
            nested = unit / "历史材料"
            nested.mkdir()
            (unit / "上一份备案信息表.md").write_text("历史备案", encoding="utf-8")
            (unit / "某系统定级报告.md").write_text("近似名称", encoding="utf-8")
            (nested / "资产清单.md").write_text("子目录副本", encoding="utf-8")
            (unit / "综合材料.md").write_text(
                "备案信息表.md\n定级报告.md\n资产清单.md\n",
                encoding="utf-8",
            )

            fields = extract_dcz_01(unit)

        self.assertEqual(
            fields["材料存在性"],
            {"备案信息表": False, "定级报告": False, "资产清单": False},
        )
        self.assertEqual(fields["缺失材料"], ["备案信息表", "定级报告", "资产清单"])

    def test_invalid_or_unreadable_unit_directory_is_extraction_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = root / "不存在"
            ordinary_file = root / "不是目录.md"
            ordinary_file.write_text("x", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                extract_dcz_01(missing)
            with self.assertRaises(NotADirectoryError):
                extract_dcz_01(ordinary_file)
            with self.assertRaisesRegex(DCZ01ExtractionError, "需要一个系统目录"):
                extract_dcz_01((root,))
            with patch.object(Path, "iterdir", side_effect=PermissionError("拒绝访问")):
                with self.assertRaisesRegex(DCZ01ExtractionError, "系统目录无法读取"):
                    extract_dcz_01(root)

    def test_rule_rejects_inconsistent_extracted_fields(self) -> None:
        fields = {
            "材料存在性": {
                "备案信息表": True,
                "定级报告": False,
                "资产清单": False,
            },
            "匹配文件": {
                "备案信息表": [],
                "定级报告": [],
                "资产清单": [],
            },
            "缺失材料": ["定级报告", "资产清单"],
        }

        with self.assertRaisesRegex(ValueError, "匹配文件与存在性不一致"):
            audit_dcz_01(fields)


if __name__ == "__main__":
    unittest.main()
