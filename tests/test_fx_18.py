from __future__ import annotations

import unittest

from audit_rules.fx_18 import audit_fx_18
from extractors.fx_18 import extract_fx_18_text


class FX18Tests(unittest.TestCase):
    def test_standard_markdown_heading_passes(self) -> None:
        fields = extract_fx_18_text(
            "[业务资产风险分析](#x)\n## 6.3.3 业务资产风险分析\n"
        )

        self.assertTrue(audit_fx_18(fields)["passed"])
        self.assertEqual(
            fields["业务资产风险分析标题"][0]["识别方式"], "Markdown标题"
        )

    def test_pdf_flattened_table_heading_passes(self) -> None:
        fields = extract_fx_18_text(
            "|6.3.3.||某系统 业务资产风险分析 务风险划分准则如下表所示：|"
        )

        self.assertTrue(audit_fx_18(fields)["passed"])
        self.assertEqual(
            fields["业务资产风险分析标题"][0]["识别方式"], "PDF压平表格标题"
        )

    def test_toc_and_body_mentions_do_not_pass(self) -> None:
        fields = extract_fx_18_text(
            "6.3.3. 业务资产风险分析................ 26\n"
            "本次业务资产风险分析结果如下表所示。\n"
        )

        self.assertFalse(audit_fx_18(fields)["passed"])

    def test_similar_heading_does_not_pass(self) -> None:
        fields = extract_fx_18_text("## 6.3.3 非业务资产风险分析\n")

        self.assertFalse(audit_fx_18(fields)["passed"])

    def test_rule_rejects_malformed_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "第1项格式无效"):
            audit_fx_18({"业务资产风险分析标题": [None]})


if __name__ == "__main__":
    unittest.main()
