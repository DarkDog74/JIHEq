from __future__ import annotations

import unittest

from extractors.dj_04 import (
    DJ04ExtractionError,
    DJ04NotApplicableError,
    extract_dj_04_text,
)


REVISION_HISTORY_MARKDOWN = """
|  |  |  |  |  |  |
| --- | --- | --- | --- | --- | --- |
|  | 修订历史 (REVISION HISTORY) |  |  |  |  |
|  | 版本号 | 日 期 | 作 者 | 审阅人 | 摘 要 |
| 8.0 |  | 2025-12-19 | 高波 | 茅宏业 |  |
| 7.0 |  | 2025-5-7 |  | 茅宏业 |  |

# 目录
"""


class DJ04ExtractorTests(unittest.TestCase):
    def test_extracts_required_fields_and_row_numbers(self) -> None:
        self.assertEqual(
            extract_dj_04_text(REVISION_HISTORY_MARKDOWN),
            {
                "修订历史": [
                    {
                        "行号": 1,
                        "Markdown行号": 6,
                        "版本号": "8.0",
                        "日期": "2025-12-19",
                        "作者": "高波",
                    },
                    {
                        "行号": 2,
                        "Markdown行号": 7,
                        "版本号": "7.0",
                        "日期": "2025-5-7",
                        "作者": "",
                    },
                ]
            },
        )

    def test_ignores_fully_blank_template_rows_but_keeps_their_row_position(self) -> None:
        markdown = REVISION_HISTORY_MARKDOWN.replace(
            "| 7.0 |  | 2025-5-7 |  | 茅宏业 |  |",
            "|  |  |  |  |  |  |\n| 7.0 |  | 2025-5-7 |  | 茅宏业 |  |",
        )
        rows = extract_dj_04_text(markdown)["修订历史"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["行号"], 3)

    def test_requires_complete_revision_history_title(self) -> None:
        markdown = REVISION_HISTORY_MARKDOWN.replace(
            "修订历史 (REVISION HISTORY)", "修订历史填写说明"
        )
        with self.assertRaises(DJ04NotApplicableError):
            extract_dj_04_text(markdown)

    def test_requires_version_date_and_author_headers(self) -> None:
        markdown = REVISION_HISTORY_MARKDOWN.replace("作 者", "编写人员")
        with self.assertRaisesRegex(DJ04ExtractionError, "完整表头"):
            extract_dj_04_text(markdown)

    def test_multiple_revision_history_tables_are_rejected(self) -> None:
        markdown = REVISION_HISTORY_MARKDOWN + REVISION_HISTORY_MARKDOWN
        with self.assertRaisesRegex(DJ04ExtractionError, "多个修订历史表标题"):
            extract_dj_04_text(markdown)


if __name__ == "__main__":
    unittest.main()
