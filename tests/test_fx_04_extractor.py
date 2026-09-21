from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extractors import get_extractor_spec, select_input_document
from extractors.fx_04 import (
    FX04NoUsableReportError,
    extract_fx_04_documents,
    extract_fx_04_text,
)


class FX04ExtractorTests(unittest.TestCase):
    def test_associates_image_on_next_nonempty_line(self) -> None:
        result = extract_fx_04_text(
            '- 拓扑图\n\n[[IMAGE id=IMG-001 file="topology.png"]]'
        )
        self.assertEqual(result["拓扑图文字数量"], 1)
        self.assertEqual(result["关联IMAGE标识数量"], 1)
        self.assertEqual(result["拓扑图证据"][0]["关联IMAGE行号"], [3])

    def test_associates_image_on_same_or_previous_line(self) -> None:
        markdown = (
            '[[IMAGE id=IMG-001 file="topology.png"]]\n\n系统拓扑图\n'
            '[[IMAGE id=IMG-002 name="备份拓扑图"]]'
        )
        result = extract_fx_04_text(markdown)
        self.assertEqual(result["拓扑图文字数量"], 2)
        self.assertEqual(result["关联IMAGE标识数量"], 2)

    def test_does_not_use_unrelated_image_elsewhere(self) -> None:
        markdown = (
            '[[IMAGE id=IMG-001 file="cover.png"]]\n'
            '# 项目简介\n'
            '- 拓扑图\n'
            '本系统组网如下。\n'
            '# 下一章\n'
            '[[IMAGE id=IMG-002 file="another.png"]]'
        )
        result = extract_fx_04_text(markdown)
        self.assertEqual(result["拓扑图文字数量"], 1)
        self.assertEqual(result["关联IMAGE标识数量"], 0)

    def test_allows_explanatory_text_before_image_in_topology_section(self) -> None:
        markdown = (
            '- 拓扑图\n\n'
            '本系统组网示意图如下图所示：\n\n'
            '[[IMAGE id=IMG-001 file="topology.png"]]\n\n'
            '# 评估方法\n'
            '[[IMAGE id=IMG-002 file="workflow.png"]]\n'
        )
        result = extract_fx_04_text(markdown)
        self.assertEqual(result["关联IMAGE标识数量"], 1)
        self.assertEqual(result["拓扑图证据"][0]["关联IMAGE行号"], [5])

    def test_selects_only_standard_risk_report_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "风险评估报告.md"
            primary.write_text("- 拓扑图", encoding="utf-8")
            (root / "风险评估报告（2）.md").write_text("副本", encoding="utf-8")
            selected = select_input_document(get_extractor_spec("FX-04"), root)
            self.assertEqual(selected, primary)

    def test_current_parse_failure_does_not_use_numbered_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "风险评估报告.md"
            primary.write_text(
                '<!-- PARSE_ERROR source="broken.docx" reason="malformed" -->\n',
                encoding="utf-8",
            )
            (root / "风险评估报告（2）.md").write_text(
                '- 拓扑图\n\n[[IMAGE id=IMG-001 file="topology.png"]]\n',
                encoding="utf-8",
            )

            with self.assertRaises(FX04NoUsableReportError):
                extract_fx_04_documents(primary)

    def test_all_failed_copies_are_an_extraction_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            primary = Path(directory) / "风险评估报告.md"
            primary.write_text(
                '<!-- PARSE_ERROR source="broken.docx" reason="malformed" -->\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(FX04NoUsableReportError, "全部为显式解析失败"):
                extract_fx_04_documents(primary)


if __name__ == "__main__":
    unittest.main()
