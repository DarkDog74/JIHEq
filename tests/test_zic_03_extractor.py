from __future__ import annotations

import unittest

from extractors.zic_03 import ZIC03ExtractionError, extract_zic_03_text


class ZIC03ExtractorTests(unittest.TestCase):
    def test_extracts_missing_fields_with_asset_and_markdown_rows(self) -> None:
        result = extract_zic_03_text(
            """| 资产IP | 映射IP | 资产类型 | 责任人 |
| --- | --- | --- | --- |
| 10.0.0.1 | 20.0.0.1 | 服务器 | 张三 |
| 10.0.0.2 |  | 服务器 |  |
"""
        )

        self.assertEqual(result["资产记录行数"], 2)
        self.assertEqual(
            result["不完整资产记录"],
            [{"资产清单行号": 2, "Markdown行号": 4, "缺失字段": ["映射IP", "责任人"]}],
        )

    def test_ignores_other_tables_and_completely_blank_asset_rows(self) -> None:
        result = extract_zic_03_text(
            """| 名称 | 说明 |
| --- | --- |
| A |  |

| 资产IP | 资产类型 | 责任人 |
| --- | --- | --- |
|  |  |  |
| 10.0.0.1 | 服务器 | 张三 |
"""
        )

        self.assertEqual(result["资产记录行数"], 1)
        self.assertEqual(result["不完整资产记录"], [])

    def test_rejects_markdown_without_asset_table(self) -> None:
        with self.assertRaisesRegex(ZIC03ExtractionError, "资产IP.*资产类型"):
            extract_zic_03_text("| 名称 | 说明 |\n| --- | --- |\n| A | B |\n")


if __name__ == "__main__":
    unittest.main()
