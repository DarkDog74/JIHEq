from __future__ import annotations

import unittest

from audit_rules.dj_09 import audit_dj_09


def row(ip: str, asset_type: str = "服务器") -> dict[str, str]:
    return {
        "资产IP": ip,
        "资产类型": asset_type,
        "资产型号": "M1",
        "设备厂商": "厂商A",
        "网络位置": "内网",
        "责任人": "张三",
    }


class DJ09RuleTests(unittest.TestCase):
    def test_passes_when_multisets_match_regardless_of_order(self) -> None:
        first, second = row("10.0.0.1"), row("10.0.0.2")
        result = audit_dj_09(
            {
                "全量资产清单": [first, second, first],
                "定级报告末尾资产": [first, first, second],
            }
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["asset_inventory_count"], 3)

    def test_reports_field_mismatch_for_same_ip(self) -> None:
        result = audit_dj_09(
            {
                "全量资产清单": [row("10.0.0.1", "服务器")],
                "定级报告末尾资产": [row("10.0.0.1", "数据库")],
            }
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["mismatch_count"], 1)
        mismatch = result["differences"]["字段不一致"][0]
        self.assertEqual(mismatch["资产IP"], "10.0.0.1")
        self.assertEqual(
            mismatch["不一致字段"]["资产类型"],
            {"资产清单": "服务器", "定级报告": "数据库"},
        )

    def test_reports_missing_and_extra_assets(self) -> None:
        result = audit_dj_09(
            {
                "全量资产清单": [row("10.0.0.1")],
                "定级报告末尾资产": [row("10.0.0.2")],
            }
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["missing_in_report_count"], 1)
        self.assertEqual(result["extra_in_report_count"], 1)


if __name__ == "__main__":
    unittest.main()
