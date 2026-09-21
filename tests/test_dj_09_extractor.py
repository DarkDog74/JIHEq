from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extractors.dj_09 import DJ09ExtractionError, extract_dj_09_documents


INVENTORY = """| 资产IP | 映射IP | 资产类型 | 资产型号 | 设备厂商 | 域名 | 网络位置 | 定级对象名称 | 责任人 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10.0.0.1 |  | 服务器 | M1 | 厂商A |  | 内网 | 系统A | 张三 |
| 10.0.0.2 |  | 数据库 | M2 | 厂商B |  | 外网 | 系统A | 李四 |
"""

REPORT = """# 定级报告

附：定级报告资产信息

| 资产IP | 资产类型 | 资产型号 | 设备厂商 | 网络位置 | 责任人 |
| --- | --- | --- | --- | --- | --- |
| 10.0.0.1 | 服务器 | M1 | 厂商A | 内网 | 张三 |
| 10.0.0.2 | 数据库 | M2 | 厂商B | 外网 | 李四 |
"""


class DJ09ExtractorTests(unittest.TestCase):
    def test_extracts_six_fields_from_both_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "全量资产清单.md"
            report = Path(directory) / "定级报告.md"
            inventory.write_text(INVENTORY, encoding="utf-8")
            report.write_text(REPORT, encoding="utf-8")
            result = extract_dj_09_documents(inventory, report)
        self.assertEqual(len(result["全量资产清单"]), 2)
        self.assertEqual(len(result["定级报告末尾资产"]), 2)
        self.assertEqual(
            result["全量资产清单"][0],
            {
                "资产IP": "10.0.0.1",
                "资产类型": "服务器",
                "资产型号": "M1",
                "设备厂商": "厂商A",
                "网络位置": "内网",
                "责任人": "张三",
            },
        )

    def test_rejects_document_without_target_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "全量资产清单.md"
            report = Path(directory) / "定级报告.md"
            inventory.write_text("没有资产表", encoding="utf-8")
            report.write_text(REPORT, encoding="utf-8")
            with self.assertRaisesRegex(DJ09ExtractionError, "未找到"):
                extract_dj_09_documents(inventory, report)

    def test_accepts_network_location_header_with_scope_note(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "全量资产清单.md"
            report = Path(directory) / "定级报告.md"
            inventory.write_text(
                INVENTORY.replace("网络位置", "网络位置（内网/外网）", 1),
                encoding="utf-8",
            )
            report.write_text(REPORT, encoding="utf-8")

            result = extract_dj_09_documents(inventory, report)

        self.assertEqual(result["全量资产清单"][0]["网络位置"], "内网")

    def test_reports_exact_missing_column_in_partial_asset_table(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            inventory = Path(directory) / "全量资产清单.md"
            report = Path(directory) / "定级报告.md"
            inventory.write_text(INVENTORY, encoding="utf-8")
            report.write_text(
                """| 资产IP | 资产类型 | 资产型号 | 设备厂商 | 责任人 |
| --- | --- | --- | --- | --- |
| 10.0.0.1 | 服务器 | M1 | 厂商A | 张三 |
""",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                DJ09ExtractionError, "定级报告资产表缺少字段：网络位置"
            ):
                extract_dj_09_documents(inventory, report)


if __name__ == "__main__":
    unittest.main()
