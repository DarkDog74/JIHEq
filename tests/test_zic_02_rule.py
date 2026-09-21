from __future__ import annotations

import unittest

from audit_rules.zic_02 import audit_zic_02


class ZIC02RuleTests(unittest.TestCase):
    def test_passes_when_all_types_are_standard(self) -> None:
        result = audit_zic_02({"资产类型行数": 2, "不在资产类型划分标准表的类型": []})
        self.assertTrue(result["passed"])
        self.assertEqual(result["invalid_asset_type_count"], 0)

    def test_fails_with_row_and_type(self) -> None:
        result = audit_zic_02(
            {
                "资产类型行数": 2,
                "不在资产类型划分标准表的类型": [
                    {"资产清单行号": 2, "资产类型": "虚拟机"}
                ],
            }
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["issues"], ["资产清单第2行资产类型“虚拟机”不在资产类型划分标准表中"])


if __name__ == "__main__":
    unittest.main()
