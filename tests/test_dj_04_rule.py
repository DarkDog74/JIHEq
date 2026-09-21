from __future__ import annotations

import unittest

from audit_rules.dj_04 import audit_dj_04


class DJ04RuleTests(unittest.TestCase):
    def test_passes_when_every_populated_row_is_complete(self) -> None:
        result = audit_dj_04(
            {
                "修订历史": [
                    {
                        "行号": 1,
                        "版本号": "1.0",
                        "日期": "2025-12-19",
                        "作者": "高波",
                    }
                ]
            }
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["checked_rows"], 1)
        self.assertEqual(result["violations"], [])

    def test_reports_the_exact_row_and_missing_author(self) -> None:
        result = audit_dj_04(
            {
                "修订历史": [
                    {
                        "行号": 2,
                        "Markdown行号": 19,
                        "版本号": "7.0",
                        "日期": "2025-5-7",
                        "作者": "",
                    }
                ]
            }
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["issues"], ["修订历史第2行缺少作者"])
        self.assertEqual(result["violations"][0]["Markdown行号"], 19)

    def test_reports_every_missing_field_for_each_row(self) -> None:
        result = audit_dj_04(
            {
                "修订历史": [
                    {"行号": 1, "版本号": "", "日期": "2025-1-1", "作者": "张三"},
                    {"行号": 2, "版本号": "2.0", "日期": "", "作者": "李四"},
                    {"行号": 3, "版本号": "3.0", "日期": "2025-3-1", "作者": ""},
                ]
            }
        )
        self.assertEqual(
            result["issues"],
            [
                "修订历史第1行缺少版本号",
                "修订历史第2行缺少日期",
                "修订历史第3行缺少作者",
            ],
        )

    def test_fully_blank_row_does_not_trigger_the_conditional_rule(self) -> None:
        result = audit_dj_04(
            {"修订历史": [{"行号": 1, "版本号": "", "日期": "", "作者": ""}]}
        )
        self.assertTrue(result["passed"])

    def test_invalid_top_level_structure_fails_cleanly(self) -> None:
        result = audit_dj_04({})
        self.assertFalse(result["passed"])
        self.assertIn("修订历史", result["issues"][0])


if __name__ == "__main__":
    unittest.main()
