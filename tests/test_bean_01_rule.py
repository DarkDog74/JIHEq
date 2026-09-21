from __future__ import annotations

import unittest

from audit_rules.bean_01 import audit_bean_01


class BEAN01RuleTests(unittest.TestCase):
    def test_passes_when_all_required_fields_are_filled(self) -> None:
        result = audit_bean_01(
            {"必填项总数": 3, "已填写必填项数": 3, "缺失必填项": []}
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["missing_count"], 0)

    def test_reports_every_missing_required_field(self) -> None:
        result = audit_bean_01(
            {
                "必填项总数": 3,
                "已填写必填项数": 1,
                "缺失必填项": [
                    {"字段": "安全系统", "原因": "必填明细表无记录"},
                    {"字段": "设备[第2条].品牌", "原因": "未填写"},
                ],
            }
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_count"], 2)
        self.assertIn("安全系统：必填明细表无记录", result["issues"])
        self.assertIn("设备[第2条].品牌：未填写", result["issues"])

    def test_reports_original_placeholder_value(self) -> None:
        result = audit_bean_01(
            {
                "必填项总数": 1,
                "已填写必填项数": 0,
                "缺失必填项": [
                    {
                        "字段": "定级时间",
                        "原始值": "年 月 日",
                        "原因": "日期未填写完整",
                    }
                ],
            }
        )

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["issues"],
            ["定级时间：日期未填写完整（原值：年 月 日）"],
        )

    def test_incompatible_template_is_manual_review_not_field_failure(self) -> None:
        result = audit_bean_01(
            {
                "必填项总数": 0,
                "已填写必填项数": 0,
                "缺失必填项": [],
                "需人工复核": True,
                "人工复核原因": "识别为旧版备案模板",
            }
        )
        self.assertFalse(result["passed"])
        self.assertTrue(result["review_required"])
        self.assertEqual(result["missing_count"], 0)


if __name__ == "__main__":
    unittest.main()
