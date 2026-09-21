from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extractors.fx_17 import (
    FX17NoUsableReportError,
    extract_fx_17_documents,
    extract_fx_17_text,
)


class FX17ExtractorTests(unittest.TestCase):
    def test_finds_heading_and_numbered_toc_evidence_but_ignores_body(self) -> None:
        fields = extract_fx_17_text(
            "\n".join(
                (
                    "[6.3.2. 系统资产风险分析 30](#632-系统资产风险分析)",
                    "6.3.2. 系统资产风险分析..................30",
                    "正文提到了系统资产风险分析结果，但不是标题。",
                    "#### 6.3.2. 系统资产风险分析",
                )
            )
        )
        self.assertEqual(len(fields["系统资产风险分析标题证据"]), 3)
        self.assertEqual(
            [item["识别方式"] for item in fields["系统资产风险分析标题证据"]],
            ["目录标题项", "目录标题项", "Markdown标题"],
        )

    def test_does_not_accept_ordinary_link_or_unnumbered_toc_text(self) -> None:
        fields = extract_fx_17_text(
            "[系统资产风险分析](https://example.com)\n"
            "系统资产风险分析..................30\n"
            "正文提到了系统资产风险分析结果。\n"
        )
        self.assertEqual(fields["系统资产风险分析标题证据"], [])

    def test_accepts_numbered_standalone_title_with_spacing(self) -> None:
        fields = extract_fx_17_text("6.3.2. 系 统 资 产 风 险 分 析\n")
        self.assertEqual(len(fields["系统资产风险分析标题证据"]), 1)
        self.assertEqual(
            fields["系统资产风险分析标题证据"][0]["识别方式"],
            "独立行标题",
        )

    def test_accepts_pdf_flattened_table_title(self) -> None:
        fields = extract_fx_17_text(
            "|序号 1 6.3.2.|资产名称 服务器 系统资产风险分析|后续正文被压平|\n"
        )
        self.assertEqual(len(fields["系统资产风险分析标题证据"]), 1)
        self.assertEqual(
            fields["系统资产风险分析标题证据"][0]["识别方式"],
            "PDF压平表格标题",
        )

    def test_similar_heading_does_not_match(self) -> None:
        fields = extract_fx_17_text("## 6.3.2. 业务资产风险分析\n")
        self.assertEqual(fields["系统资产风险分析标题证据"], [])

    def test_failed_current_report_does_not_use_numbered_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit = Path(directory)
            primary = unit / "风险评估报告.md"
            primary.write_text(
                "<!-- PARSE_ERROR source=broken.docx -->\n", encoding="utf-8"
            )
            (unit / "风险评估报告（2）.md").write_text(
                "## 6.3.2. 系统资产风险分析\n", encoding="utf-8"
            )
            with self.assertRaises(FX17NoUsableReportError):
                extract_fx_17_documents(primary)

    def test_all_parse_error_copies_are_extraction_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            primary = Path(directory) / "风险评估报告.md"
            primary.write_text(
                "<!-- PARSE_ERROR source=broken.docx -->\n", encoding="utf-8"
            )
            with self.assertRaises(FX17NoUsableReportError):
                extract_fx_17_documents(primary)


if __name__ == "__main__":
    unittest.main()
