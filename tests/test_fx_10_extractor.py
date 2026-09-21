from __future__ import annotations

import unittest

from extractors.fx_10 import FX10SectionNotFoundError, extract_fx_10_text


HEADER = """| 序号 | 资产名称 | 威胁来源 | 威胁行为能力赋值 | 威胁频率赋值 | 威胁时机赋值 | 威胁结果 |
| --- | --- | --- | --- | --- | --- | --- |
"""


class FX10ExtractorTests(unittest.TestCase):
    def test_structured_rows_only_need_an_assignment_number(self) -> None:
        result = extract_fx_10_text(
            "# 附录B 威胁识别及赋值表\n"
            + HEADER
            + "| 1 | 系统A | 人为 | 2 |  |  |  |\n"
            + "| 2 | 系统B | 人为 |  |  | 0 |  |\n"
            + "# 附录C 已有安全措施分析表\n"
        )

        self.assertEqual(len(result["赋值记录"]), 2)
        self.assertEqual(result["赋值记录"][0]["数字"], ["2"])
        self.assertEqual(result["赋值记录"][1]["数字"], ["0"])
        self.assertTrue(result["赋值记录"][1]["是否有数字"])

    def test_structured_row_without_assignment_number_is_recorded(self) -> None:
        result = extract_fx_10_text(
            "# 附录B 威胁识别及赋值表\n"
            + HEADER
            + "| 1 | 系统A | 人为 | 待赋值 |  |  |  |\n"
            + "# 附录C 已有安全措施分析表\n"
        )

        self.assertEqual(result["赋值记录"][0]["数字"], [])
        self.assertFalse(result["赋值记录"][0]["是否有数字"])

    def test_accepts_flattened_pdf_text_when_each_physical_line_has_numbers(self) -> None:
        result = extract_fx_10_text(
            """##### 附录 B 威胁识别及赋值表
序号 资产类别 威胁来源 威胁行为能力赋值 威胁频率赋值 威胁时机赋值 威胁结果
1 业务 人为 网络攻击 2 2 0 2
未授权 2 人为 权限滥用 1 1 0 1
##### 附录 C 已有安全措施分析表
"""
        )

        self.assertEqual(len(result["赋值记录"]), 2)
        self.assertTrue(all(row["是否有数字"] for row in result["赋值记录"]))
        self.assertEqual(result["赋值记录"][0]["记录来源"], "压平文本")

    def test_flattened_text_line_without_number_is_recorded_as_problem(self) -> None:
        result = extract_fx_10_text(
            """##### 附录 B 威胁识别及赋值表
序号 威胁来源 威胁行为能力赋值 威胁结果
1 人为 网络攻击 待赋值
##### 附录 C 已有安全措施分析表
"""
        )
        self.assertFalse(result["赋值记录"][0]["是否有数字"])

    def test_stops_before_next_appendix_even_when_it_is_a_table_row(self) -> None:
        result = extract_fx_10_text(
            """##### 附录 B 威胁识别及赋值表
1 人为 网络攻击 2
|附录 C 已有安全措施分析表|2025年|
|---|---|
|技术层面|1项措施|
##### 附录 D 脆弱性识别及赋值表
"""
        )
        self.assertEqual(len(result["赋值记录"]), 1)

    def test_returns_empty_records_when_appendix_has_no_data(self) -> None:
        result = extract_fx_10_text(
            """# 附录B 威胁识别及赋值表
序号 威胁来源 威胁行为能力赋值 威胁结果
# 附录C 已有安全措施分析表
"""
        )
        self.assertEqual(result["赋值记录"], [])

    def test_requires_target_appendix_title(self) -> None:
        with self.assertRaises(FX10SectionNotFoundError):
            extract_fx_10_text("\n".join(f"# 其他章节{i}" for i in range(12)))


if __name__ == "__main__":
    unittest.main()
