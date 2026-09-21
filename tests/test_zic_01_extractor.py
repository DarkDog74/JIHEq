from __future__ import annotations

import unittest

from extractors.zic_01 import ZIC01ExtractionError, extract_zic_01_text


class ZIC01ExtractorTests(unittest.TestCase):
    def test_reports_each_placeholder_with_row_and_field(self) -> None:
        result = extract_zic_01_text(
            """| 资产IP | 资产型号 | 责任人 |
| --- | --- | --- |
| 10.0.0.1 | XXX | 张三 |
| 10.0.0.2 | M1 | 待补充 |
| 10.0.0.3 | N/A | 李四 |
"""
        )

        self.assertEqual(result["资产清单行数"], 3)
        self.assertEqual(
            [(item["资产清单行号"], item["字段"], item["命中占位符"]) for item in result["模糊占位符"]],
            [(1, "资产型号", ["XXX"]), (2, "责任人", ["待补充"]), (3, "资产型号", ["N/A"])],
        )

    def test_does_not_treat_single_x_or_identifier_as_placeholder(self) -> None:
        result = extract_zic_01_text(
            """| 资产IP | 资产型号 |
| --- | --- |
| 10.0.0.1 | X |
| 10.0.0.2 | AXX1 |
"""
        )

        self.assertEqual(result["模糊占位符"], [])

    def test_rejects_markdown_without_data_table(self) -> None:
        with self.assertRaisesRegex(ZIC01ExtractionError, "未找到数据表格"):
            extract_zic_01_text("# 资产清单\n\n暂无表格")


if __name__ == "__main__":
    unittest.main()
