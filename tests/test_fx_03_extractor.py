from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extractors.fx_03 import (
    FX03NoUsableReportError,
    extract_fx_03_documents,
    extract_fx_03_text,
)


MAINSTREAM_REPORT = """# 总体评价
# 2. 资产识别
## 2.1. 业务识别
### 2.1.2. 业务重要性赋值
## 2.2. 系统资产识别
### 2.2.2. 资产价值赋值
# 3. 威胁识别
## 3.4. 威胁赋值说明
# 4. 已有安全措施有效性分析
# 5. 脆弱性识别
## 5.3. 脆弱性赋值说明
# 6. 风险计算与评价
"""


class FX03ExtractorTests(unittest.TestCase):
    def test_accepts_mainstream_split_titles(self) -> None:
        result = extract_fx_03_text(MAINSTREAM_REPORT, source_name="风险评估报告.md")
        self.assertTrue(result["完整"])
        self.assertEqual(result["已找到标题数"], 7)
        self.assertEqual(result["缺失标题"], [])

    def test_accepts_confirmed_synonym_titles(self) -> None:
        markdown = MAINSTREAM_REPORT.replace(
            "# 总体评价", "## 6.2. 风险评价方法"
        ).replace(
            "# 4. 已有安全措施有效性分析",
            "## 4.2. 已有安全措施有效性分析结果",
        )

        result = extract_fx_03_text(markdown, source_name="风险评估报告.md")

        self.assertTrue(result["完整"])
        checks = {item["目标标题"]: item for item in result["标题检查"]}
        self.assertEqual(
            checks["已有安全措施确认"]["匹配标题"][0]["标题"],
            "4.2. 已有安全措施有效性分析结果",
        )
        self.assertEqual(
            checks["风险总结"]["匹配标题"][0]["标题"],
            "6.2. 风险评价方法",
        )

    def test_synonym_words_in_prose_do_not_count_as_headings(self) -> None:
        result = extract_fx_03_text(
            "正文说明已有安全措施有效性分析结果和风险评价均符合要求。",
            source_name="风险评估报告.md",
        )

        self.assertIn("已有安全措施确认", result["缺失标题"])
        self.assertIn("风险总结", result["缺失标题"])

    def test_accepts_direct_titles_with_spacing_and_markdown_emphasis(self) -> None:
        markdown = "\n".join(
            f"## ** {index}. {' '.join(title)} **"
            for index, title in enumerate(
                (
                    "业务识别和赋值",
                    "资产识别和赋值",
                    "威胁识别和赋值",
                    "脆弱性识别和赋值",
                    "已有安全措施确认",
                    "风险计算",
                    "风险总结",
                ),
                start=1,
            )
        )
        result = extract_fx_03_text(markdown, source_name="风险评估报告.md")
        self.assertTrue(result["完整"])

    def test_plain_text_and_catalog_links_do_not_count_as_headings(self) -> None:
        markdown = "\n".join(
            [
                "业务识别和赋值",
                "[资产识别和赋值](#asset)",
                "# 威胁识别",
                "# 威胁赋值说明",
            ]
        )
        result = extract_fx_03_text(markdown, source_name="风险评估报告.md")
        self.assertIn("业务识别和赋值", result["缺失标题"])
        self.assertIn("资产识别和赋值", result["缺失标题"])

    def test_does_not_use_complete_duplicate_when_current_is_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "风险评估报告.md"
            primary.write_text("# 风险计算模型\n", encoding="utf-8")
            (root / "风险评估报告（2）.md").write_text(
                MAINSTREAM_REPORT, encoding="utf-8"
            )

            result = extract_fx_03_documents(primary)

            self.assertEqual(result["风险评估报告文件数"], 1)
            self.assertEqual(result["完整标题报告数"], 0)
            self.assertEqual(result["最佳匹配报告"], "风险评估报告.md")

    def test_all_explicit_parse_error_stubs_are_extraction_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            primary = Path(directory) / "风险评估报告.md"
            primary.write_text(
                "<!-- PARSE_ERROR source=broken.docx -->\n", encoding="utf-8"
            )
            with self.assertRaises(FX03NoUsableReportError):
                extract_fx_03_documents(primary)

    def test_current_parse_error_is_not_masked_by_another_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "风险评估报告.md"
            primary.write_text(
                "<!-- PARSE_ERROR source=broken.docx -->\n", encoding="utf-8"
            )
            (root / "风险评估报告（PDF）.md").write_text(
                MAINSTREAM_REPORT, encoding="utf-8"
            )
            with self.assertRaises(FX03NoUsableReportError):
                extract_fx_03_documents(primary)

    def test_risk_calculation_model_alone_is_not_the_risk_calculation_section(self) -> None:
        result = extract_fx_03_text(
            MAINSTREAM_REPORT.replace(
                "# 6. 风险计算与评价",
                "# 1.5.3. 风险计算模型\n"
                "1.5.3. 风险计算模型 介绍计算原理。",
            ),
            source_name="风险评估报告.md",
        )
        self.assertIn("风险计算", result["缺失标题"])

    def test_recovers_numbered_titles_degraded_to_plain_text(self) -> None:
        markdown = """#### 总体评价
|2. 资产识别 2.1.1. 其他内容|2.1. 业务识别 业务识别范围与正文挤在一起|
2.1.2. 业务重要性赋值 应根据业务的重要程度进行等级划分。
2.2.2. 资产价值赋值 系统资产价值依据安全属性赋值。
|前页正文。 3. 威胁识别 3.1. 威胁概述|后续正文|
3.4. 威胁赋值说明 不同等级代表威胁出现频率的高低。
|很低 3.5. 威胁识别结果 4. 已有安全措施有效性分析 4.1. 已有安全措施有效性分析概述|
# 5. 脆弱性识别
## 5.3. 脆弱性赋值说明
# 6. 风险计算与评价
"""

        result = extract_fx_03_text(markdown, source_name="风险评估报告.md")

        self.assertTrue(result["完整"])
        degraded = [
            evidence
            for item in result["标题检查"]
            for evidence in item["匹配标题"]
            if evidence["识别方式"] == "降级编号标题"
        ]
        self.assertGreaterEqual(len(degraded), 6)

    def test_accepts_numbered_toc_dot_leaders_as_structural_evidence(self) -> None:
        markdown = """2. 资产识别....................................................................13
2.1. 业务识别................................................................13
2.1.2. 业务重要性赋值................................................14
3. 威胁识别....................................................................18
3.4. 威胁赋值说明....................................................20
"""

        result = extract_fx_03_text(markdown, source_name="风险评估报告.md")

        self.assertEqual(result["已找到标题数"], 2)
        methods = {
            evidence["识别方式"]
            for item in result["标题检查"]
            for evidence in item["匹配标题"]
        }
        self.assertEqual(methods, {"目录标题项"})

    def test_does_not_match_numbered_target_phrase_in_ordinary_prose(self) -> None:
        markdown = "本报告后续将介绍 3. 威胁识别 以及其他内容。"

        result = extract_fx_03_text(markdown, source_name="风险评估报告.md")

        self.assertIn("威胁识别和赋值", result["缺失标题"])

    def test_accepts_target_title_that_occupies_a_whole_line(self) -> None:
        markdown = MAINSTREAM_REPORT.replace("# 总体评价", "总体评价")

        result = extract_fx_03_text(markdown, source_name="风险评估报告.md")

        self.assertTrue(result["完整"])
        summary = next(
            item for item in result["标题检查"] if item["目标标题"] == "风险总结"
        )
        self.assertEqual(summary["匹配标题"][0]["识别方式"], "独占行标题")


if __name__ == "__main__":
    unittest.main()
