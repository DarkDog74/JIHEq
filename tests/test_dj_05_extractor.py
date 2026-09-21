from __future__ import annotations

import unittest

from extractors.dj_05 import DJ05ExtractionError, extract_dj_05_text


VALID_MARKDOWN = """
<!-- 页眉：某系统定级报告 -->
某系统定级报告

**Ver.1.5**

**2025年5月**

| **修订历史** (REVISION HISTORY) |  |  |  |  |
| **版本号** | **日 期** | **作 者** | **审阅人** | **摘 要** |
| 1.0 | 2008-02-20 | 张三 |  |  |
| **版本号** | **日  期** | **修改人** | **审阅人** | **摘 要** |
| 1.5 | 2025-5-31 | 李四 |  |  |
"""


class DJ05ExtractorTests(unittest.TestCase):
    def test_extracts_cover_and_latest_revision_fields(self) -> None:
        self.assertEqual(
            extract_dj_05_text(VALID_MARKDOWN),
            {
                "封面版本": "1.5",
                "封面日期": "2025-05",
                "最新修订版本": "1.5",
                "最新修订日期": "2025-05-31",
            },
        )

    def test_latest_revision_is_selected_by_date_not_last_row(self) -> None:
        markdown = VALID_MARKDOWN.replace(
            "| 1.5 | 2025-5-31 | 李四 |  |  |",
            "| 1.5 | 2025-5-31 | 李四 |  |  |\n| 1.4 | 2024-12-1 | 王五 |  |  |",
        )
        result = extract_dj_05_text(markdown)
        self.assertEqual(result["最新修订版本"], "1.5")
        self.assertEqual(result["最新修订日期"], "2025-05-31")

    def test_preserves_version_segments_instead_of_using_float(self) -> None:
        markdown = VALID_MARKDOWN.replace("1.5", "3.10")
        self.assertEqual(extract_dj_05_text(markdown)["封面版本"], "3.10")

    def test_supports_parenthesized_version_and_spaced_cover_month(self) -> None:
        markdown = VALID_MARKDOWN.replace("**Ver.1.5**", "**(Ver. 1.5)**").replace(
            "2025年5月", "2025年 5 月"
        )
        result = extract_dj_05_text(markdown)
        self.assertEqual(result["封面版本"], "1.5")
        self.assertEqual(result["封面日期"], "2025-05")

    def test_supports_values_split_by_markdown_bold_runs(self) -> None:
        markdown = VALID_MARKDOWN.replace(
            "**Ver.1.5**", "**Ver.****1****.5**"
        ).replace("**2025年5月**", "**2025年** **5月**")
        result = extract_dj_05_text(markdown)
        self.assertEqual(result["封面版本"], "1.5")
        self.assertEqual(result["封面日期"], "2025-05")

    def test_missing_cover_fields_are_returned_as_none_for_the_rule(self) -> None:
        markdown = VALID_MARKDOWN.replace("**Ver.1.5**", "").replace("**2025年5月**", "")
        result = extract_dj_05_text(markdown)
        self.assertIsNone(result["封面版本"])
        self.assertIsNone(result["封面日期"])

    def test_conflicting_versions_on_the_latest_date_are_rejected(self) -> None:
        markdown = VALID_MARKDOWN.replace(
            "| 1.5 | 2025-5-31 | 李四 |  |  |",
            "| 1.5 | 2025-5-31 | 李四 |  |  |\n| 1.6 | 2025-5-31 | 王五 |  |  |",
        )
        with self.assertRaisesRegex(DJ05ExtractionError, "多个不同版本号"):
            extract_dj_05_text(markdown)

    def test_invalid_revision_date_is_rejected(self) -> None:
        markdown = VALID_MARKDOWN.replace("2025-5-31", "2025-13-31")
        with self.assertRaisesRegex(DJ05ExtractionError, "日期无效"):
            extract_dj_05_text(markdown)


if __name__ == "__main__":
    unittest.main()
