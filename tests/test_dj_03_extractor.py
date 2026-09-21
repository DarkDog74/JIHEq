from __future__ import annotations

import unittest

from extractors.dj_03 import (
    DJ03ExtractionError,
    DJ03NotApplicableError,
    extract_dj_03_text,
)


class DJ03ExtractorTests(unittest.TestCase):
    def test_extracts_explicit_header_comment_and_document_name(self) -> None:
        markdown = """<!-- 页眉：中国移动通信集团浙江有限公司省内光传送网骨干传送网定级报告 -->
中国移动通信集团浙江有限公司省内光传送网骨干传送网定级报告

|  |  |  |
| --- | --- | --- |
|  | 文档名称 | **中国移动通信集团浙江有限公司省内光传送网骨干传送网定级报告** |
"""
        self.assertEqual(
            extract_dj_03_text(markdown),
            {
                "页眉": "中国移动通信集团浙江有限公司省内光传送网骨干传送网定级报告",
                "文档名称": "中国移动通信集团浙江有限公司省内光传送网骨干传送网定级报告",
            },
        )

    def test_uses_only_first_section_header_from_multi_section_summary(self) -> None:
        markdown = """<!-- 页眉：第1节默认页眉=完整系统名称定级报告；第2节默认页眉=缩写名称定级报告 -->
| 文档名称 | 完整系统名称定级报告 |
"""
        result = extract_dj_03_text(markdown)
        self.assertEqual(result["页眉"], "完整系统名称定级报告")

    def test_prefers_first_page_header_when_word_enables_different_first_page(self) -> None:
        markdown = """<!-- 页眉：默认页眉=正文页眉；首页页眉=封面标题 -->
| 文档名称 | 封面标题 |
"""
        result = extract_dj_03_text(markdown)
        self.assertEqual(result["页眉"], "封面标题")

    def test_falls_back_to_first_nonempty_text_for_legacy_markdown(self) -> None:
        markdown = """
**某 系统 定级报告**

| **文档名称** | **某系统定级报告** |  |
"""
        result = extract_dj_03_text(markdown)
        self.assertEqual(result["页眉"], "某 系统 定级报告")
        self.assertEqual(result["文档名称"], "某系统定级报告")

    def test_supports_document_name_split_by_html_line_break(self) -> None:
        markdown = """某系统定级报告
| 文档名称 | **某系统**<br>**定级报告** |
"""
        self.assertEqual(extract_dj_03_text(markdown)["文档名称"], "某系统 定级报告")

    def test_missing_document_name_is_not_applicable(self) -> None:
        with self.assertRaises(DJ03NotApplicableError):
            extract_dj_03_text("某系统定级报告\n| 文件名称 | 某系统定级报告 |")

    def test_conflicting_document_names_are_rejected(self) -> None:
        markdown = """某系统定级报告
| 文档名称 | 某系统定级报告 |
| 文档名称 | 另一个系统定级报告 |
"""
        with self.assertRaisesRegex(DJ03ExtractionError, "多个不同的文档名称"):
            extract_dj_03_text(markdown)


if __name__ == "__main__":
    unittest.main()
