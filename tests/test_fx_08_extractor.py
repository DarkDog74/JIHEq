from __future__ import annotations

import unittest

from extractors.fx_08 import (
    FX08AssetIdentificationNotFoundError,
    FX08IncompleteMarkdownError,
    extract_fx_08_text,
)


STANDARD_TABLE = """# A.3 系统组件和单元资产赋值表
| 序号 | 系统组件和单元资产名称 | 识别内容 |  |  | 系统组件和单元资产赋值 |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  | 分类（大类） | 分类详情（子分类） | 关键数据/关键服务 | 保密性 | 完整性 | 可用性 | 综合赋值Vc |
| C1 | 10.0.0.1 | 系统单元 | 服务器 | 关键服务 | 3 | 3 | 4 | 3 |
| C2 | Linux | 系统组件 | 操作系统 | 关键服务 | 2 | 3 | 4 | 3 |
# A.4 人力资源赋值表
| 序号 | 保密性 | 完整性 | 可用性 |
| D1 |  |  |  |
"""


class FX08ExtractorTests(unittest.TestCase):
    def test_extracts_required_columns_and_values_only_from_target_table(self) -> None:
        result = extract_fx_08_text(STANDARD_TABLE)

        self.assertEqual(result["目标表标题"], "A.3 系统组件和单元资产赋值表")
        self.assertEqual(
            {item["列名"]: item["存在"] for item in result["列检查"]},
            {"保密性": True, "完整性": True, "可用性": True},
        )
        self.assertEqual(len(result["赋值记录"]), 2)
        self.assertEqual(
            [record["是否完整赋值"] for record in result["赋值记录"]],
            [True, True],
        )

    def test_reports_missing_column_and_missing_assignment(self) -> None:
        markdown = STANDARD_TABLE.replace(" | 可用性 |", " | 可恢复性 |").replace(
            "| 2 | 3 | 4 | 3 |", "| 2 |  | 4 | 3 |"
        )
        result = extract_fx_08_text(markdown)

        self.assertFalse(result["列检查"][2]["存在"])
        self.assertEqual(
            result["赋值记录"][1]["缺少赋值"], ["完整性", "可用性"]
        )

    def test_recovers_flattened_title_and_spaced_header(self) -> None:
        result = extract_fx_08_text(
            "|1|系统资产|A.3 系统组件和单元资产赋值表|上一表数据|\n"
            "|序 号||资产名称|分类|关键服务|保密 性|完整 性|可用 性|综合赋值 Vc|\n"
            "|1||服务器|系统单元|关键服务|3|3|4|3|\n"
            "|2|数据库|A.4 人力资源赋值表|系统组件|关键服务|2|3|4|3 人员|\n"
            "|序号|人员类型|姓名|联系方式|\n"
        )

        self.assertEqual(result["目标表标题行"], 1)
        self.assertEqual(len(result["赋值记录"]), 2)
        self.assertEqual(result["赋值记录"][1]["可用性"], "4")
        self.assertTrue(result["赋值记录"][1]["是否完整赋值"])

    def test_uses_fallback_title_when_primary_title_is_absent(self) -> None:
        result = extract_fx_08_text(
            STANDARD_TABLE.replace(
                "系统组件和单元资产赋值表", "系统单元及系统组件赋值表"
            )
        )

        self.assertEqual(result["目标表标题"], "A.3 系统单元及系统组件赋值表")
        self.assertEqual(len(result["赋值记录"]), 2)
        self.assertTrue(all(row["是否完整赋值"] for row in result["赋值记录"]))

    def test_prefers_primary_title_even_when_fallback_title_appears_first(self) -> None:
        markdown = (
            "# A.2 系统单元及系统组件赋值表\n"
            "| 序号 | 保密性 | 完整性 | 可用性 |\n"
            "| F1 | 1 | 1 | 1 |\n"
            "# A.3 系统组件和单元资产赋值表\n"
            "| 序号 | 保密性 | 完整性 | 可用性 |\n"
            "| P1 | 3 | 4 | 4 |\n"
            "# A.4 人力资源赋值表\n"
        )

        result = extract_fx_08_text(markdown)

        self.assertEqual(result["目标表标题"], "A.3 系统组件和单元资产赋值表")
        self.assertEqual([row["记录编号"] for row in result["赋值记录"]], ["P1"])

    def test_does_not_use_same_columns_from_another_table(self) -> None:
        with self.assertRaises(FX08IncompleteMarkdownError):
            extract_fx_08_text(
                "# A.3 系统组件和单元资产赋值表\n"
                "[[IMAGE name=\"Embedded object: Excel.Sheet.12\" "
                "type=\"application/vnd.ms-ole-object\"]]\n"
                "# A.4 人力资源赋值表\n"
                "| 序号 | 保密性 | 完整性 | 可用性 |\n"
                "| D1 | 3 | 3 | 3 |\n"
            )

    def test_recovers_values_split_below_rows_by_pdf_conversion(self) -> None:
        result = extract_fx_08_text(
            "# A.3 系统组件和单元资产赋值表\n"
            "| 序号 | 资产名称 | 分类 | 详情 | 保密性 | 完整性 | 可用性 | 综合赋值Vc |\n"
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| 1 | 服务器 | 系统单元 | 服务器 | | | | |\n"
            "| 2 | 数据库 | 系统组件 | 数据库 | | | | |\n"
            "3 4 4 4\n"
            "2 3 4 3\n"
            "# A.4 人力资源赋值表\n"
        )

        self.assertEqual(result["分离赋值四元组数"], 2)
        self.assertEqual(
            [record["是否完整赋值"] for record in result["赋值记录"]],
            [True, True],
        )
        self.assertEqual(result["赋值记录"][1]["保密性"], "2")

    def test_does_not_guess_when_detached_value_count_does_not_match_rows(self) -> None:
        result = extract_fx_08_text(
            "# A.3 系统组件和单元资产赋值表\n"
            "| 序号 | 资产名称 | 分类 | 详情 | 保密性 | 完整性 | 可用性 | 综合赋值Vc |\n"
            "| 1 | 服务器 | 系统单元 | 服务器 | | | | |\n"
            "| 2 | 数据库 | 系统组件 | 数据库 | | | | |\n"
            "3 4 4 4\n"
            "# A.4 人力资源赋值表\n"
        )

        self.assertEqual(result["分离赋值四元组数"], 1)
        self.assertFalse(result["赋值记录"][0]["是否完整赋值"])
        self.assertFalse(result["赋值记录"][1]["是否完整赋值"])

    def test_rejects_missing_target_table_title(self) -> None:
        with self.assertRaisesRegex(
            FX08AssetIdentificationNotFoundError,
            "系统组件和单元资产赋值表.*系统单元及系统组件赋值表",
        ):
            extract_fx_08_text("# 2. 资产识别\n完整性、保密性、可用性。\n")

    def test_explicit_parse_error_stub_is_extraction_error(self) -> None:
        with self.assertRaises(FX08IncompleteMarkdownError):
            extract_fx_08_text("<!-- PARSE_ERROR source=broken.docx -->\n")


if __name__ == "__main__":
    unittest.main()
