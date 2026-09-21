from __future__ import annotations

import unittest

from audit_rules.zic_03 import audit_zic_03


class ZIC03RuleTests(unittest.TestCase):
    def test_passes_when_every_cell_is_filled(self) -> None:
        result = audit_zic_03({"资产记录行数": 2, "不完整资产记录": []})
        self.assertTrue(result["passed"])
        self.assertEqual(result["missing_cell_count"], 0)

    def test_fails_with_specific_row_and_fields(self) -> None:
        result = audit_zic_03(
            {
                "资产记录行数": 2,
                "不完整资产记录": [
                    {"资产清单行号": 2, "Markdown行号": 4, "缺失字段": ["映射IP", "域名"]}
                ],
            }
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_cell_count"], 2)
        self.assertEqual(result["issues"], ["资产清单第2行缺少：映射IP、域名"])


if __name__ == "__main__":
    unittest.main()
