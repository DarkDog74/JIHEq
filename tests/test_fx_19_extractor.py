from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extractors.fx_19 import (
    FX19NoUsableReportError,
    extract_fx_19_documents,
    extract_fx_19_text,
)


class FX19ExtractorTests(unittest.TestCase):
    def test_finds_exact_markdown_heading_and_ignores_toc_and_body(self) -> None:
        fields = extract_fx_19_text(
            "\n".join(
                (
                    "[7.5. 残余风险处置 30](#75-残余风险处置)",
                    "7.5. 残余风险处置..................30",
                    "正文提到了残余风险处置，但不是标题。",
                    "#### 7.5. 残余风险处置",
                )
            )
        )
        self.assertEqual(len(fields["残余风险处置标题证据"]), 1)
        self.assertEqual(
            fields["残余风险处置标题证据"][0]["识别方式"],
            "Markdown标题",
        )

    def test_accepts_numbered_standalone_title_with_spacing(self) -> None:
        fields = extract_fx_19_text("7.5. 残 余 风 险 处 置\n")
        self.assertEqual(len(fields["残余风险处置标题证据"]), 1)
        self.assertEqual(
            fields["残余风险处置标题证据"][0]["识别方式"],
            "独立行标题",
        )

    def test_accepts_pdf_flattened_table_title(self) -> None:
        fields = extract_fx_19_text(
            "|序号 1 7.5.|资产名称 服务器 残余风险处置|后续正文被压平|\n"
        )
        self.assertEqual(len(fields["残余风险处置标题证据"]), 1)
        self.assertEqual(
            fields["残余风险处置标题证据"][0]["识别方式"],
            "PDF压平表格标题",
        )

    def test_similar_heading_does_not_match(self) -> None:
        fields = extract_fx_19_text("## 7.5. 残余风险处理\n")
        self.assertEqual(fields["残余风险处置标题证据"], [])

    def test_failed_current_report_does_not_use_numbered_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit = Path(directory)
            primary = unit / "风险评估报告.md"
            primary.write_text(
                "<!-- PARSE_ERROR source=broken.docx -->\n", encoding="utf-8"
            )
            (unit / "风险评估报告（2）.md").write_text(
                "## 7.5. 残余风险处置\n", encoding="utf-8"
            )
            with self.assertRaises(FX19NoUsableReportError):
                extract_fx_19_documents(primary)

    def test_all_parse_error_copies_are_extraction_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            primary = Path(directory) / "风险评估报告.md"
            primary.write_text(
                "<!-- PARSE_ERROR source=broken.docx -->\n", encoding="utf-8"
            )
            with self.assertRaises(FX19NoUsableReportError):
                extract_fx_19_documents(primary)


if __name__ == "__main__":
    unittest.main()
