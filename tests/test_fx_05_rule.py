from __future__ import annotations

import unittest

from audit_rules.fx_05 import audit_fx_05


class FX05RuleTests(unittest.TestCase):
    def test_passes_when_quantity_total_equals_asset_record_count(self) -> None:
        result = audit_fx_05(
            {"风险评估报告资产数量": 55, "资产清单记录数": 55}
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["quantity_difference"], 0)

    def test_reports_quantity_mismatch(self) -> None:
        result = audit_fx_05(
            {"风险评估报告资产数量": 55, "资产清单记录数": 53}
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["quantity_difference"], 2)
        self.assertIn("相差2", result["issues"][0])

    def test_reports_ip_row_fallback_as_the_quantity_source(self) -> None:
        result = audit_fx_05(
            {
                "风险评估报告资产数量": 3,
                "资产清单记录数": 2,
                "风险评估报告数量来源": "IP行兜底",
            }
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["quantity_source"], "IP行兜底")
        self.assertIn("IP行统计为3", result["issues"][0])

    def test_rejects_invalid_counts(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须是非负整数"):
            audit_fx_05(
                {"风险评估报告资产数量": "55", "资产清单记录数": 55}
            )


if __name__ == "__main__":
    unittest.main()
