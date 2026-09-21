from __future__ import annotations

import unittest

from extractors.fx_09 import (
    FX09IncompleteMarkdownError,
    FX09SectionNotFoundError,
    FX09TableHeaderNotFoundError,
    extract_fx_09_text,
)


class FX09ExtractorTests(unittest.TestCase):
    def test_extracts_columns_from_table_only_inside_exact_target_heading(self) -> None:
        result = extract_fx_09_text(
            """# 目录
[附录B 威胁识别及赋值表](#附录b)
# 附录A 其他表
| 威胁来源 | 威胁种类 | 威胁行为 |
| --- | --- | --- |
# 附录B 威胁识别及赋值表
| 序号 | 资产名称 | 威胁来源 | 威胁种类 | 威胁行为 |
| --- | --- | --- | --- | --- |
| 1 | 系统A | 人为 | 功能损害 | 网络攻击 |
# 附录C 已有安全措施分析表
"""
        )
        self.assertEqual(result["表头来源"], "Markdown表格")
        self.assertEqual(result["目标表标题行"], 6)
        self.assertEqual(result["表头行"], 7)
        self.assertTrue(all(item["存在"] for item in result["列检查"]))

    def test_supports_anydoc_flattened_header_text(self) -> None:
        result = extract_fx_09_text(
            """##### 附录 B 威胁识别及赋值表

威胁 序号 资产类别 资产名称 威胁来源 威胁种 类 威胁行为 威胁动机 威胁主体 威胁频率
1 业务 系统A 人为 信息损害 网络攻击
##### 附录 D 脆弱性识别及赋值表
"""
        )
        self.assertEqual(result["表头来源"], "扁平化表头文本")
        self.assertEqual(result["表头行"], 3)
        self.assertTrue(all(item["存在"] for item in result["列检查"]))

    def test_supports_interleaved_multiline_threat_type_header(self) -> None:
        result = extract_fx_09_text(
            """##### 附录 B 威胁识别及赋值表
威胁 威胁 序号 资产类别 威胁种 行为 威胁来源 威胁行为 威胁动机 类 能力赋值
1 业务 系统A 人为 信息损害 网络攻击
##### 附录 D 脆弱性识别及赋值表
"""
        )
        presence = {item["列名"]: item["存在"] for item in result["列检查"]}
        self.assertEqual(
            presence,
            {"威胁来源": True, "威胁种类": True, "威胁行为": True},
        )

    def test_does_not_confuse_threat_behavior_ability_with_threat_behavior_column(self) -> None:
        result = extract_fx_09_text(
            """# 附录B 威胁识别及赋值表
| 序号 | 资产名称 | 威胁来源 | 威胁种类 | 威胁行为能力赋值 |
| --- | --- | --- | --- | --- |
| 1 | 系统A | 人为 | 功能损害 | 2 |
# 附录C 已有安全措施分析表
| 威胁来源 | 威胁种类 | 威胁行为 |
| --- | --- | --- |
"""
        )
        presence = {item["列名"]: item["存在"] for item in result["列检查"]}
        self.assertEqual(
            presence,
            {"威胁来源": True, "威胁种类": True, "威胁行为": False},
        )

    def test_rejects_table_of_contents_without_target_heading(self) -> None:
        with self.assertRaises(FX09SectionNotFoundError):
            extract_fx_09_text(
                "[附录B 威胁识别及赋值表](#附录b)\n"
                + "报告正文。\n" * 11
            )

    def test_reports_obviously_incomplete_markdown_as_extraction_error(self) -> None:
        with self.assertRaises(FX09IncompleteMarkdownError):
            extract_fx_09_text(
                """|  | 资产风险等级数目 |
| --- | --- |
| 很高 | 0 |
| 高 | 0 |
| 中 | 0 |
| 低 | 1 |
| 很低 | 0 |
"""
            )

    def test_supports_plain_pdf_title_and_header_on_the_same_line(self) -> None:
        result = extract_fx_09_text(
            """附录 B 威胁识别及赋值表................................ 34
正文引用见附录 B 威胁识别及赋值表。
附录 B 威胁识别及赋值表 序号 资产类别 威胁来源 威胁种类 威胁行为 威胁动机
1 业务 系统A 人为 信息损害 网络攻击
附录 C 已有安全措施分析表
威胁来源 威胁种类 威胁行为
"""
        )
        self.assertEqual(result["表头来源"], "扁平化表头文本")
        self.assertEqual(result["目标表标题行"], 3)
        self.assertEqual(result["表头行"], 3)
        self.assertTrue(all(item["存在"] for item in result["列检查"]))

    def test_supports_table_cell_title_and_header_after_separator(self) -> None:
        result = extract_fx_09_text(
            """|||附录 B 威胁识别及赋值表|||第 36 页 共 149 页|||||||
|---|---|---|---|---|---|---|
|序 号|资 产 类 别|资产名称|威胁 来源|威胁 种类|威胁行为|威胁行为能力赋值|
|1|业务|系统A|人为|信息损害|网络攻击|2|
|||附录 C 已有安全措施分析表||||
|威胁来源|威胁种类|威胁行为|
"""
        )
        self.assertEqual(result["目标表标题行"], 1)
        self.assertEqual(result["表头行"], 3)
        self.assertEqual(result["表头来源"], "Markdown表格")
        self.assertTrue(all(item["存在"] for item in result["列检查"]))

    def test_reports_unrecognizable_header_inside_target_section(self) -> None:
        with self.assertRaises(FX09TableHeaderNotFoundError):
            extract_fx_09_text(
                """# 附录B 威胁识别及赋值表
| 各安全层面 | 已有安全措施 |
| --- | --- |
# 附录C 其他表
"""
            )


if __name__ == "__main__":
    unittest.main()
