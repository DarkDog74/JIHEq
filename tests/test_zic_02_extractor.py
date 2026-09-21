from __future__ import annotations

import unittest

from extractors.zic_02 import ZIC02ExtractionError, extract_zic_02_text


class ZIC02ExtractorTests(unittest.TestCase):
    def test_uses_development_variant_as_the_standard_asset_type(self) -> None:
        result = extract_zic_02_text(
            "| 资产IP | 资产类型 |\n"
            "| --- | --- |\n"
            "| 10.0.0.1 | 其他应用软件（含自研和第三方开放） |\n"
            "| 10.0.0.2 | 其他应用软件（含自研和第三方开发） |\n"
        )

        self.assertEqual(result["资产类型行数"], 2)
        self.assertEqual(
            [item["资产类型"] for item in result["不在资产类型划分标准表的类型"]],
            ["其他应用软件（含自研和第三方开放）"],
        )

    def test_reports_types_not_in_standard_and_keeps_locations(self) -> None:
        result = extract_zic_02_text(
            """| 资产IP | 资产类型 |\n| --- | --- |\n| 10.0.0.1 | 服务器 |\n| 10.0.0.2 | 其它基础软件 |\n| 10.0.0.3 | 虚拟机 |\n| 10.0.0.4 |  |\n"""
        )

        self.assertEqual(result["资产类型行数"], 4)
        self.assertEqual(
            [(item["资产清单行号"], item["资产类型"]) for item in result["不在资产类型划分标准表的类型"]],
            [(3, "虚拟机"), (4, "")],
        )

    def test_rejects_table_without_asset_type_column(self) -> None:
        with self.assertRaisesRegex(ZIC02ExtractionError, "资产类型.*数据表格"):
            extract_zic_02_text("| 资产IP | 设备厂商 |\n| --- | --- |\n| 10.0.0.1 | 厂商A |\n")


if __name__ == "__main__":
    unittest.main()
