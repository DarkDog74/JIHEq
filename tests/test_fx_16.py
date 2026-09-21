from __future__ import annotations

import unittest

from audit_rules.fx_16 import audit_fx_16
from extractors.fx_16 import FX16AppendixNotFoundError, extract_fx_16_text


class FX16Tests(unittest.TestCase):
    def test_collects_all_content_rows_from_standard_table(self) -> None:
        fields = extract_fx_16_text(
            "# 附录 E 安全风险计算结果\n"
            "|序号|风险等级|\n"
            "|---|---|\n"
            "|1|低|\n"
            "|2|很低|\n"
            "# 附录 F 其他\n"
        )

        self.assertEqual(fields["有内容的数据行"], [4, 5])
        self.assertTrue(audit_fx_16(fields)["passed"])

    def test_empty_table_fails(self) -> None:
        fields = extract_fx_16_text(
            "# 附录E安全风险计算结果\n|序号|风险等级|\n|---|---|\n"
        )

        self.assertFalse(audit_fx_16(fields)["passed"])

    def test_flattened_title_and_values_on_same_line_pass(self) -> None:
        fields = extract_fx_16_text(
            "附录 E 安全风险计算结果 序号 资产名称 风险值 风险等级 "
            "1 某系统 1 很低\n"
        )

        self.assertEqual(fields["附录标题Markdown行号"], 1)
        self.assertEqual(fields["有内容的数据行"], [1])
        self.assertTrue(audit_fx_16(fields)["passed"])

    def test_flattened_title_inside_previous_table_and_plain_data_pass(self) -> None:
        fields = extract_fx_16_text(
            "|4|某资产 附录 E 安全风险计算结果|脆弱性|\n"
            "|序号|资产名称|风险值|风险等级|\n"
            "\n"
            "1 某系统 1 很低 2 某组件 2 低\n"
            "|附录 F 风险处置建议|\n"
        )

        self.assertEqual(fields["附录标题Markdown行号"], 1)
        self.assertEqual(fields["有内容的数据行"], [4])

    def test_blank_first_row_does_not_hide_later_data(self) -> None:
        fields = extract_fx_16_text(
            "# 附录 E 安全风险计算结果\n"
            "|序号|风险等级|\n"
            "|---|---|\n"
            "|||\n"
            "|1|低|\n"
        )

        self.assertEqual(fields["有内容的数据行"], [5])

    def test_toc_and_reference_do_not_count_as_appendix(self) -> None:
        with self.assertRaises(FX16AppendixNotFoundError):
            extract_fx_16_text(
                "附录 E 安全风险计算结果................ 37\n"
                "[附录E 安全风险计算结果 37](#附录e-安全风险计算结果)\n"
                "详细分析结果见附录 E 安全风险计算结果。\n"
            )

    def test_rule_requires_title_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "附录标题格式无效"):
            audit_fx_16({"有内容的数据行": [4]})


if __name__ == "__main__":
    unittest.main()
