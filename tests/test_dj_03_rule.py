from __future__ import annotations

import unittest

from audit_rules.dj_03 import audit_dj_03


class DJ03RuleTests(unittest.TestCase):
    def test_ignores_all_whitespace_when_comparing(self) -> None:
        result = audit_dj_03(
            {
                "页眉": "中国移动通信集团 浙江有限公司\n某系统定级报告",
                "文档名称": "中国移动通信集团浙江有限公司某系统定级报告",
            }
        )
        self.assertTrue(result["passed"])
        self.assertTrue(result["consistent"])

    def test_reports_mismatch_with_both_original_values(self) -> None:
        result = audit_dj_03(
            {"页眉": "甲系统定级报告", "文档名称": "乙系统定级报告"}
        )
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["issues"],
            ["页眉与文档名称不一致：页眉“甲系统定级报告”，文档名称“乙系统定级报告”"],
        )

    def test_reports_missing_field(self) -> None:
        result = audit_dj_03({"页眉": "某系统定级报告", "文档名称": "  "})
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_fields"], ["文档名称"])
        self.assertEqual(result["issues"], ["缺少文档名称"])

    def test_literal_empty_marker_is_reported_as_missing_header(self) -> None:
        result = audit_dj_03(
            {"页眉": "空", "文档名称": "中国移动浙江公司彩铃系统业务定级报告"}
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_fields"], ["页眉"])
        self.assertEqual(result["issues"], ["缺少页眉"])
        self.assertEqual(result["message"], "缺少页眉")


if __name__ == "__main__":
    unittest.main()
