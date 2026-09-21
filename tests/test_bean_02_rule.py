from __future__ import annotations

import unittest

from audit_rules.bean_02 import audit_bean_02


class BEAN02RuleTests(unittest.TestCase):
    def test_fails_when_inventory_has_extra_external_asset_ip(self) -> None:
        result = audit_bean_02(
            {
                "备案IP": ["10.0.0.1", "10.0.0.2"],
                "资产清单外网资产IP": ["10.0.0.1", "10.0.0.2", "192.168.1.1"],
                "数据问题": [],
            }
        )
        self.assertFalse(result["passed"])
        self.assertTrue(result["applicable"])
        self.assertEqual(result["missing_in_asset_inventory"], [])
        self.assertEqual(result["extra_in_asset_inventory"], ["192.168.1.1"])

    def test_passes_when_both_ip_sets_are_equal(self) -> None:
        result = audit_bean_02(
            {
                "备案IP": ["10.0.0.1", "10.0.0.2"],
                "资产清单外网资产IP": ["10.0.0.2", "10.0.0.1"],
                "数据问题": [],
            }
        )
        self.assertTrue(result["passed"])
        self.assertIn("一一对应", result["message"])

    def test_reports_each_filing_ip_missing_from_inventory(self) -> None:
        result = audit_bean_02(
            {
                "备案IP": ["10.0.0.1", "10.0.0.2", "10.0.0.3"],
                "资产清单外网资产IP": ["10.0.0.1"],
                "数据问题": [],
            }
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_in_asset_inventory"], ["10.0.0.2", "10.0.0.3"])
        self.assertIn("资产清单外网资产IP缺少2个备案 IP", result["issues"][0])

    def test_fails_when_filing_ip_is_empty_but_inventory_has_ip(self) -> None:
        result = audit_bean_02(
            {"备案IP": [], "资产清单外网资产IP": ["10.0.0.1"], "数据问题": []}
        )

        self.assertFalse(result["passed"])
        self.assertTrue(result["applicable"])
        self.assertIn("外网资产IP多出", result["issues"][0])

    def test_passes_when_both_ip_lists_are_empty(self) -> None:
        result = audit_bean_02(
            {"备案IP": [], "资产清单外网资产IP": [], "数据问题": []}
        )

        self.assertTrue(result["passed"])
        self.assertTrue(result["applicable"])
        self.assertIn("双方一致", result["message"])

    def test_data_issue_is_a_failed_audit_not_an_exception(self) -> None:
        result = audit_bean_02(
            {
                "备案IP": [],
                "资产清单外网资产IP": [],
                "数据问题": ["备案 IP 范围起止顺序错误：112.15.239.85-112.15.239.73"],
            }
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["data_issue_count"], 1)
        self.assertIn("起止顺序错误", result["issues"][0])


if __name__ == "__main__":
    unittest.main()
