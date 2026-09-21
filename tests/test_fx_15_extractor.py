from __future__ import annotations

import unittest

from extractors.fx_15 import (
    FX15ExtractionError,
    FX15IncompleteMarkdownError,
    FX15SectionNotFoundError,
    extract_fx_15_text,
)


class FX15ExtractorTests(unittest.TestCase):
    def test_only_matches_method_inside_exact_target_section(self) -> None:
        result = extract_fx_15_text(
            """# 1. 风险计算模型
采用矩阵法。
## 6.1. 风险计算方法
本次采用相乘法计算风险值。
## 6.2. 风险评价方法
采用矩阵法评价。
"""
        )
        self.assertEqual(result["风险计算方法章节标题"], "6.1. 风险计算方法")
        self.assertEqual(result["风险计算方法章节标题行"], 3)
        self.assertEqual(result["命中的风险计算方法"], ["相乘法"])
        self.assertEqual(
            [item["Markdown行号"] for item in result["方法检查"]],
            [[], [4]],
        )

    def test_accepts_matrix_method_with_layout_spaces(self) -> None:
        result = extract_fx_15_text(
            """##### 6.1 风险计算方法
采用矩 阵 法进行计算。
##### 6.2 风险评价方法
"""
        )
        self.assertEqual(result["命中的风险计算方法"], ["矩阵法"])

    def test_outputs_both_methods_in_fixed_order(self) -> None:
        result = extract_fx_15_text(
            """## 风险计算方法
先采用相乘法，部分场景采用矩阵法。
## 风险评价方法
"""
        )
        self.assertEqual(result["命中的风险计算方法"], ["矩阵法", "相乘法"])

    def test_section_without_either_method_is_valid_extraction(self) -> None:
        result = extract_fx_15_text(
            """## 6.1 风险计算方法
本节介绍定量风险计算过程。
## 6.2 风险评价方法
"""
        )
        self.assertEqual(result["命中的风险计算方法"], [])

    def test_rejects_table_of_contents_and_similar_heading(self) -> None:
        markdown = (
            "[6.1 风险计算方法](#61)\n"
            "## 6.1 风险计算方法说明\n"
            + "报告正文。\n" * 11
        )
        with self.assertRaises(FX15SectionNotFoundError):
            extract_fx_15_text(markdown)

    def test_rejects_multiple_target_headings(self) -> None:
        with self.assertRaisesRegex(FX15ExtractionError, "多个标题"):
            extract_fx_15_text(
                """## 6.1 风险计算方法
相乘法。
## 7.1 风险计算方法
矩阵法。
"""
            )

    def test_reports_obviously_incomplete_markdown(self) -> None:
        with self.assertRaises(FX15IncompleteMarkdownError):
            extract_fx_15_text("| 风险等级 | 数目 |\n| --- | --- |\n| 低 | 1 |\n")

    def test_reports_explicit_parse_error_marker(self) -> None:
        with self.assertRaisesRegex(FX15IncompleteMarkdownError, "PARSE_ERROR"):
            extract_fx_15_text(
                """<!-- PARSE_ERROR source="报告.docx" reason="malformed" -->
# 风险评估报告解析失败
该文件未生成正常 Markdown。
"""
            )


if __name__ == "__main__":
    unittest.main()
