from __future__ import annotations

import unittest

from audit_rules.zic_01 import audit_zic_01


class ZIC01RuleTests(unittest.TestCase):
    def test_passes_without_placeholder(self) -> None:
        result = audit_zic_01({"资产清单行数": 2, "模糊占位符": []})
        self.assertTrue(result["passed"])
        self.assertEqual(result["placeholder_count"], 0)

    def test_fails_with_specific_placeholder_location(self) -> None:
        result = audit_zic_01(
            {
                "资产清单行数": 2,
                "模糊占位符": [
                    {
                        "资产清单行号": 2,
                        "Markdown行号": 4,
                        "字段": "设备厂商",
                        "值": "待定",
                        "命中占位符": ["待定"],
                    }
                ],
            }
        )
        self.assertFalse(result["passed"])
        self.assertIn("资产清单第2行“设备厂商”包含待定", result["issues"])


if __name__ == "__main__":
    unittest.main()
