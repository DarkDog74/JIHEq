from __future__ import annotations

import unittest

from audit_rules.fx_03 import audit_fx_03


def fields(*, missing: list[str]) -> dict[str, object]:
    matched = 7 - len(missing)
    return {
        "风险评估报告文件数": 1,
        "完整标题报告数": 0 if missing else 1,
        "最佳匹配报告": "风险评估报告.md",
        "已找到标题数": matched,
        "缺失标题": missing,
        "报告检查结果": [{}],
    }


class FX03RuleTests(unittest.TestCase):
    def test_passes_when_one_report_has_all_titles(self) -> None:
        result = audit_fx_03(fields(missing=[]))
        self.assertTrue(result["passed"])
        self.assertEqual(result["matched_title_count"], 7)

    def test_fails_and_reports_each_missing_title(self) -> None:
        result = audit_fx_03(fields(missing=["已有安全措施确认", "风险总结"]))
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_title_count"], 2)
        self.assertEqual(
            result["issues"], ["缺少标题：已有安全措施确认、风险总结"]
        )

    def test_rejects_inconsistent_counts(self) -> None:
        invalid = fields(missing=["风险总结"])
        invalid["已找到标题数"] = 7
        with self.assertRaisesRegex(ValueError, "不一致"):
            audit_fx_03(invalid)


if __name__ == "__main__":
    unittest.main()
