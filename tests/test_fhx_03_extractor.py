from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from extractors.fhx_03 import (
    FHX03ExtractionError,
    extract_fhx_03_documents,
    extract_fhx_03_text,
    extract_filing_network_type,
)


class FHX03ExtractorTests(unittest.TestCase):
    def test_extracts_network_type_from_first_real_table_text(self) -> None:
        result = extract_fhx_03_text(
            """|  |  |  |
| --- | --- | --- |
| 通信网络安全防护符合性评测表－融合网络 |  |  |
| 填写说明 | 后续正文 |  |
"""
        )
        self.assertEqual(result["网络类型"], "融合网络")
        self.assertEqual(result["Markdown行号"], 3)

    def test_supports_year_prefix_ascii_dash_and_heading(self) -> None:
        result = extract_fhx_03_text(
            "# 2025年度通信网络安全防护符合性评测表-移动通信网\n"
        )
        self.assertEqual(result["网络类型"], "移动通信网")

    def test_keeps_hierarchical_type_after_prefix_separator(self) -> None:
        result = extract_fhx_03_text(
            "| 通信网络安全防护符合性评测表－增值业务网—消息网 | |\n"
        )
        self.assertEqual(result["网络类型"], "增值业务网—消息网")

    def test_does_not_use_later_filling_instruction_as_title(self) -> None:
        with self.assertRaisesRegex(FHX03ExtractionError, "未找到"):
            extract_fhx_03_text(
                "| 填写说明 | 本表为通信网络安全防护符合性评测表支撑网部分 |\n"
            )

    def test_extracts_filing_type_from_required_key_with_star(self) -> None:
        result = extract_filing_network_type(
            {"网络/系统单元类型*": "增值业务网-省内智能网"}
        )
        self.assertEqual(result["备案网络/系统单元类型"], "增值业务网-省内智能网")

    def test_rejects_missing_filing_type_key(self) -> None:
        with self.assertRaisesRegex(FHX03ExtractionError, "未找到 key"):
            extract_filing_network_type({"系统名称*": "系统A"})

    def test_extracts_two_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assessment = root / "符合性评测表.md"
            filing = root / "备案信息表.json"
            assessment.write_text(
                "| 通信网络安全防护符合性评测表－融合网络 | |\n",
                encoding="utf-8",
            )
            filing.write_text(
                '{"网络/系统单元类型*": "融合网络"}', encoding="utf-8"
            )
            result = extract_fhx_03_documents(assessment, filing)
        self.assertEqual(result["网络类型"], "融合网络")
        self.assertEqual(result["备案网络/系统单元类型"], "融合网络")


if __name__ == "__main__":
    unittest.main()
