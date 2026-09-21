from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extractors.errors import TargetSectionNotFoundError
from extractors.fx_11 import extract_fx_11, extract_fx_11_text


HEADING_REPORT = """# 风险评估报告

## 5. 脆弱性识别

脆弱性识别内容包括物理环境、网络结构等方面。

### 5.1. 脆弱性识别概述

脆弱性是指资产或资产组中能被威胁所利用的弱点。

## 6. 风险计算与评价

按相乘法计算风险。
"""

FLATTENED_REPORT = """# 风险评估报告

5.1. 脆弱性识别概述................................................................ 24

|4.2. 已有安全措施有效性分析结果 安全措施有效性分析结果见附录 C。 5. 脆弱性识别 5.1. 脆弱性识别概述 脆弱性是指资产或资产组中能被威胁所利用的弱点，包括物理环境、组织机构、业务流程、人员、管理、硬件、软件及通讯设施等各个方面。|
|---|

##### 附录 D 脆弱性识别及赋值表

|序 号|资产名称|威胁名称|脆弱 性名 称|脆弱性描述|
|---|---|---|---|---|
|1|服务器|网络攻击|系统 漏洞|补丁未及时安装|
|2||网络攻击|配置缺陷|访问控制配置不当|
|3|安全管理|管理制度|规程缺失|制度不完整|

# 6. 风险计算与评价

按相乘法计算风险。
"""


REAL_SHAPE_REPORT = """# 风险评估报告

## 5. 脆弱性识别

## 5.1. 脆弱性识别概述

脆弱性是指资产或资产组中能被威胁所利用的弱点，包括物理环境、组织机构、业务流程、人员、管理、硬件、软件及通讯设施等各个方面。

## 5.2. 脆弱性识别方法

采用对照法识别。

## 5.3. 脆弱性赋值标准

|5|很高|特别重大损害|
|---|---|

## 5.4. 脆弱性识别结果

脆弱性识别结果见附录D脆弱性识别表。

## 6. 风险计算与评价

按相乘法计算风险。
"""


class FX11ExtractorTests(unittest.TestCase):
    def test_extracts_section_by_heading(self) -> None:
        result = extract_fx_11_text(HEADING_REPORT)
        self.assertEqual(result["章节定位方式"], "标题")
        # 整章包含章引言和子节，不越界到下一章。
        self.assertIn("资产或资产组中能被威胁所利用的弱点", result["脆弱性识别章节文本"])
        self.assertIn("网络结构等方面", result["脆弱性识别章节文本"])
        self.assertNotIn("风险计算与评价", result["脆弱性识别章节文本"])

    def test_same_markdown_level_keeps_numbered_subsections(self) -> None:
        result = extract_fx_11_text(REAL_SHAPE_REPORT)
        self.assertEqual(result["章节定位方式"], "标题")
        section = result["脆弱性识别章节文本"]
        self.assertIn("物理环境", section)
        self.assertIn("识别方法", section)
        self.assertIn("赋值标准", section)
        self.assertIn("脆弱性识别结果见附录D", section)
        self.assertNotIn("风险计算与评价", section)

    def test_extracts_flattened_section_by_keyword_ignoring_toc(self) -> None:
        result = extract_fx_11_text(FLATTENED_REPORT)
        self.assertEqual(result["章节定位方式"], "关键词")
        self.assertIn("能被威胁所利用的弱点", result["脆弱性识别章节文本"])
        self.assertNotIn("..", result["脆弱性识别章节文本"].splitlines()[0])

    def test_appendix_records_are_not_extracted(self) -> None:
        result = extract_fx_11_text(FLATTENED_REPORT)
        self.assertNotIn("附录D脆弱性记录", result)
        self.assertNotIn("补丁未及时安装", result["脆弱性识别章节文本"])
        self.assertNotIn("已有安全措施有效性分析", result["脆弱性识别章节文本"])

    def test_missing_appendix_is_not_fatal(self) -> None:
        result = extract_fx_11_text(HEADING_REPORT)
        self.assertIn("网络结构", result["脆弱性识别章节文本"])

    def test_no_fixed_chapter_or_overview_required(self) -> None:
        text = "# 8. 脆弱性识别\n物理环境\n## 8.3 识别结果\n网络结构\n# 9. 总结\n不应包含"
        section = extract_fx_11_text(text)["脆弱性识别章节文本"]
        self.assertIn("网络结构", section)
        self.assertNotIn("不应包含", section)

    def test_unnumbered_chapter(self) -> None:
        section = extract_fx_11_text("# 脆弱性识别\n物理环境\n## 识别结果\n网络结构\n# 风险计算\n不应包含")["脆弱性识别章节文本"]
        self.assertIn("网络结构", section)
        self.assertNotIn("不应包含", section)

    def test_toc_and_body_mentions_are_not_chapters(self) -> None:
        for text in ("[5. 脆弱性识别](#chapter)\n# 其他\n物理环境", "# 其他\n脆弱性识别概述参见附录。", "5. 脆弱性识别........24"):
            with self.subTest(text=text), self.assertRaises(TargetSectionNotFoundError):
                extract_fx_11_text(text)

    def test_flattened_next_chapter_in_same_cell_is_excluded(self) -> None:
        section = extract_fx_11_text("| 5. 脆弱性识别 物理环境 6. 风险计算 网络结构 | ")["脆弱性识别章节文本"]
        self.assertIn("物理环境", section)
        self.assertNotIn("网络结构", section)

    def test_rejects_report_without_chapter(self) -> None:
        with self.assertRaises(TargetSectionNotFoundError):
            extract_fx_11_text("# 风险评估报告\n\n只有风险计算内容。")

    def test_wrapper_stages_fields_without_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parsed = root / "parsed"
            parsed.mkdir()
            report = parsed / "风险评估报告.md"
            report.write_text(HEADING_REPORT, encoding="utf-8")

            extract_fx_11(report)

            staged = root / "audits" / "FX-11" / "FX-11.json"
            self.assertTrue(staged.is_file())
            payload = json.loads(staged.read_text(encoding="utf-8"))
            self.assertEqual(payload["输入文档"], ["parsed/风险评估报告.md"])
            self.assertNotIn("原始输入图片", payload)


if __name__ == "__main__":
    unittest.main()
