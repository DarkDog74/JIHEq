from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extractors.bean_02 import extract_bean_02_documents


def filing_payload(ip_text: str) -> dict[str, object]:
    return {
        "网络单元使用的主要协议和端口（公网，和资产清单公网IP对应）": [
            {"对应IP段*": ip_text}
        ],
    }


ASSET_TABLE = """| 资产IP | 映射IP | 资产类型 | 网络位置（内网/外网） |
| --- | --- | --- | --- |
| 10.0.0.1～10.0.0.3 | 192.168.0.1 | 服务器 | 外网 |
| 10.0.0.4-6 | 192.168.0.2 | 服务器 | 外网 |
| 2409:8087::10/128 | 192.168.0.3 | 服务器 | 外网 |
| 10.0.0.9 | 203.0.113.9 | 服务器 | 内网 |
"""


class BEAN02ExtractorTests(unittest.TestCase):
    def test_extracts_filing_ranges_and_external_asset_ip_column(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            filing = Path(directory) / "备案信息表.json"
            inventory = Path(directory) / "资产清单.md"
            filing.write_text(
                json.dumps(filing_payload("10.0.0.1～10.0.0.3；10.0.0.4-6、2409:8087::10等")),
                encoding="utf-8",
            )
            inventory.write_text(ASSET_TABLE, encoding="utf-8")

            result = extract_bean_02_documents(filing, inventory)

        self.assertEqual(
            result["备案IP"],
            [
                "10.0.0.1",
                "10.0.0.2",
                "10.0.0.3",
                "10.0.0.4",
                "10.0.0.5",
                "10.0.0.6",
                "2409:8087::10",
            ],
        )
        self.assertTrue(result["备案IP是否含未列明项"])
        self.assertEqual(result["资产清单外网资产IP条数"], 7)
        self.assertEqual(result["资产清单外网资产IP"], result["备案IP"])

    def test_treats_non_ip_filing_text_as_no_checkable_ip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            filing = Path(directory) / "备案信息表.json"
            inventory = Path(directory) / "资产清单.md"
            filing.write_text(
                json.dumps(filing_payload("无公网")), encoding="utf-8"
            )
            inventory.write_text(ASSET_TABLE, encoding="utf-8")

            result = extract_bean_02_documents(filing, inventory)

        self.assertEqual(result["备案IP"], [])

    def test_accepts_host_ips_with_cidr_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            filing = Path(directory) / "备案信息表.json"
            inventory = Path(directory) / "资产清单.md"
            filing.write_text(
                json.dumps(
                    filing_payload(
                        "223.92.13.188/32；2409:8087:2805:20::10/72"
                    )
                ),
                encoding="utf-8",
            )
            inventory.write_text(ASSET_TABLE, encoding="utf-8")

            result = extract_bean_02_documents(filing, inventory)

        self.assertEqual(
            result["备案IP"],
            ["223.92.13.188", "2409:8087:2805:20::10"],
        )

    def test_splits_semicolon_separated_external_asset_ips_and_removes_32(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            filing = Path(directory) / "备案信息表.json"
            inventory = Path(directory) / "资产清单.md"
            filing.write_text(
                json.dumps(filing_payload("223.92.13.188/32;223.92.49.79/32")),
                encoding="utf-8",
            )
            inventory.write_text(
                "| 资产 IP | 映射 IP | 网络位置 |\n| --- | --- | --- |\n"
                "| 223.92.13.188/32;223.92.49.79/32 | 10.0.0.1 | 外网 |\n",
                encoding="utf-8",
            )
            result = extract_bean_02_documents(filing, inventory)

        self.assertEqual(
            result["资产清单外网资产IP"],
            ["223.92.13.188", "223.92.49.79"],
        )

    def test_treats_inventory_without_required_filter_columns_as_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            filing = Path(directory) / "备案信息表.json"
            inventory = Path(directory) / "资产清单.md"
            filing.write_text(
                json.dumps(filing_payload("10.0.0.1")), encoding="utf-8"
            )
            inventory.write_text("| 主机名 |\\n| --- |\\n| A |\\n", encoding="utf-8")

            result = extract_bean_02_documents(filing, inventory)

        self.assertEqual(result["资产清单外网资产IP"], [])
        self.assertFalse(result["外网资产筛选表头存在"])
        self.assertEqual(result["数据问题"], [])

    def test_ignores_internal_asset_ips_when_filing_has_no_ip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            filing = Path(directory) / "备案信息表.json"
            inventory = Path(directory) / "资产清单.md"
            filing.write_text(
                json.dumps(filing_payload("")), encoding="utf-8"
            )
            inventory.write_text(
                "| 资产IP | 资产类型 | 网络位置 |\n"
                "| --- | --- | --- |\n"
                "| 10.0.0.1 | 服务器 | 内网 |\n",
                encoding="utf-8",
            )

            result = extract_bean_02_documents(filing, inventory)

        self.assertEqual(result["备案IP"], [])
        self.assertEqual(result["资产清单外网资产IP"], [])
        self.assertTrue(result["外网资产筛选表头存在"])

    def test_ignores_unrelated_inventory_columns_when_mapped_ip_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            filing = Path(directory) / "备案信息表.json"
            inventory = Path(directory) / "资产清单.md"
            filing.write_text(json.dumps(filing_payload("")), encoding="utf-8")
            inventory.write_text(
                "| 主机名 |\n| --- |\n| A |\n", encoding="utf-8"
            )

            result = extract_bean_02_documents(filing, inventory)

        self.assertEqual(result["备案IP"], [])
        self.assertEqual(result["资产清单外网资产IP"], [])
        self.assertFalse(result["外网资产筛选表头存在"])

    def test_only_exact_external_location_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            filing = Path(directory) / "备案信息表.json"
            inventory = Path(directory) / "资产清单.md"
            filing.write_text(json.dumps(filing_payload("")), encoding="utf-8")
            inventory.write_text(
                "| 资产IP | 资产类型 | 网络位置 |\n"
                "| --- | --- | --- |\n"
                "| 203.0.113.10 | 服务器 | 外网 |\n"
                "| 203.0.113.11 | 服务器 | 公网 |\n"
                "| 10.0.0.1 | 服务器 | 内网 |\n",
                encoding="utf-8",
            )

            result = extract_bean_02_documents(filing, inventory)

        self.assertEqual(result["备案IP"], [])
        self.assertEqual(result["资产清单外网资产IP"], ["203.0.113.10"])
        self.assertTrue(result["外网资产筛选表头存在"])

    def test_accepts_short_filing_section_and_corresponding_ip_header(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            filing = Path(directory) / "备案信息表.json"
            inventory = Path(directory) / "资产清单.md"
            filing.write_text(
                json.dumps(
                    {"网络单元使用的主要协议和端口": [{"对应IP*": "10.0.0.1"}]}
                ),
                encoding="utf-8",
            )
            inventory.write_text(
                "| 资产IP | 网络位置（内网/外网） |\n"
                "| --- | --- |\n"
                "| 10.0.0.1 | 外网 |\n",
                encoding="utf-8",
            )

            result = extract_bean_02_documents(filing, inventory)

        self.assertEqual(result["备案IP"], ["10.0.0.1"])
        self.assertEqual(result["资产清单外网资产IP"], ["10.0.0.1"])

    def test_records_external_row_without_valid_asset_ip_as_data_issue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            filing = Path(directory) / "备案信息表.json"
            inventory = Path(directory) / "资产清单.md"
            filing.write_text(json.dumps(filing_payload("")), encoding="utf-8")
            inventory.write_text(
                "| 资产IP | 网络位置 |\n"
                "| --- | --- |\n"
                "| - | 外网 |\n",
                encoding="utf-8",
            )

            result = extract_bean_02_documents(filing, inventory)

        self.assertEqual(result["资产清单外网资产IP"], [])
        self.assertIn("资产IP为空或无有效IP", result["数据问题"][0])

    def test_records_reversed_filing_range_as_data_issue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            filing = Path(directory) / "备案信息表.json"
            inventory = Path(directory) / "资产清单.md"
            filing.write_text(
                json.dumps(filing_payload("112.15.239.85-73")), encoding="utf-8"
            )
            inventory.write_text("| 资产IP |\n| --- |\n| 10.0.0.1 |\n", encoding="utf-8")

            result = extract_bean_02_documents(filing, inventory)

        self.assertEqual(result["备案IP"], [])
        self.assertEqual(result["资产清单外网资产IP"], [])
        self.assertIn("起止顺序错误", result["数据问题"][0])


if __name__ == "__main__":
    unittest.main()
