from __future__ import annotations

import unittest

from audit_rules.fhx_05 import audit_fhx_05


def report(*, readable: bool, reasons: list[str] | None = None) -> dict[str, object]:
    return {
        "文件名": "风险评估报告.docx",
        "可读取": readable,
        "不可读取原因": reasons or [],
    }


class FHX05RuleTests(unittest.TestCase):
    def test_level_one_passes_with_readable_report(self) -> None:
        result = audit_fhx_05(
            {"声明安全等级": 1, "风险评估报告": [report(readable=True)]}
        )
        self.assertTrue(result["passed"])
        self.assertTrue(result["applicable"])
        self.assertEqual(result["risk_report_count"], 1)

    def test_level_one_fails_when_report_is_absent(self) -> None:
        result = audit_fhx_05({"声明安全等级": 1, "风险评估报告": []})
        self.assertFalse(result["passed"])
        self.assertEqual(result["issues"], ["一级系统缺少风险评估报告"])

    def test_level_one_fails_when_report_is_not_readable(self) -> None:
        result = audit_fhx_05(
            {
                "声明安全等级": 1,
                "风险评估报告": [report(readable=False, reasons=["文件大小无效"])],
            }
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["issues"], ["一级系统没有可读取的风险评估报告"])

    def test_non_level_one_is_not_checked(self) -> None:
        result = audit_fhx_05({"声明安全等级": 2, "风险评估报告": []})
        self.assertTrue(result["passed"])
        self.assertFalse(result["applicable"])
        self.assertEqual(result["message"], "非一级不查")


if __name__ == "__main__":
    unittest.main()
