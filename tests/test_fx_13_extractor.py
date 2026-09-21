from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extractors.fx_13 import (
    FX13IncompleteMarkdownError,
    FX13TableNotFoundError,
    extract_fx_13_documents,
    extract_fx_13_text,
)


class FX13ExtractorTests(unittest.TestCase):
    def test_extracts_type_values_only_from_named_table(self) -> None:
        result = extract_fx_13_text(
            """# 4. 已有安全措施有效性分析
正文提到保护性安全措施，但不能作为表格字段证据。
# 附录C 已有安全措施分析表
| 各安全层面 | 已有安全措施 | 类型 | 功能描述 |
| --- | --- | --- | --- |
| 技术层面 | 防火墙 | 预防性安全措施 | 网络防护 |
| 管理层面 | 备份 | 保护性安全措施 | 数据恢复 |
# 附录D 脆弱性识别及赋值表
| 类型 | 说明 |
| --- | --- |
| 预防性安全措施 | 无关表格 |
"""
        )
        self.assertEqual(result["定位方式"], "Markdown标题")
        self.assertTrue(result["类型列存在"])
        self.assertEqual(
            [item["关键词"] for item in result["保护性安全措施证据"]],
            ["保护性安全措施"],
        )
        self.assertEqual(
            [item["Markdown行号"] for item in result["保护性安全措施证据"]], [7]
        )

    def test_reports_missing_type_column_as_objective_field(self) -> None:
        result = extract_fx_13_text(
            """# 附录C 已有安全措施分析表
| 各安全层面 | 已有安全措施 | 功能描述 |
| --- | --- | --- |
| 技术层面 | 防火墙 | 网络防护 |
# 附录D 下一表
"""
        )
        self.assertFalse(result["类型列存在"])
        self.assertEqual(result["保护性安全措施证据"], [])

    def test_does_not_use_keyword_from_intro_or_other_table(self) -> None:
        result = extract_fx_13_text(
            """保护性安全措施是常见措施。
# 附录C 已有安全措施分析表
| 已有安全措施 | 类型 |
| --- | --- |
| 数据备份 | 预防性安全措施 |
# 附录D 其他表
| 类型 |
| --- |
| 保护性安全措施 |
"""
        )
        self.assertEqual(result["保护性安全措施证据"], [])

    def test_supports_standalone_plain_table_title(self) -> None:
        result = extract_fx_13_text(
            """附录 C 已有安全措施分析表
| 已有安全措施 | 类型 |
| --- | --- |
| 访问控制 | 保护性安全措施 |
附录 D 脆弱性识别及赋值表
"""
        )
        self.assertEqual(result["定位方式"], "独占行表题")
        self.assertEqual(
            result["保护性安全措施证据"][0]["关键词"], "保护性安全措施"
        )

    def test_supports_pdf_table_flattened_into_one_row(self) -> None:
        result = extract_fx_13_text(
            """附录 C 已有安全措施分析表................................ 39
正文引用见附录 C 已有安全措施分析表。
|26|附录 C 已有安全措施分析表 各安全层面|已有安全措施 内网登录|非恶意 类型 保护性安全措施|功能描述 数据保护|
|---|---|---|---|---|
##### 附录 D 脆弱性识别及赋值表
"""
        )
        self.assertEqual(result["定位方式"], "压平表格行")
        self.assertTrue(result["类型列存在"])
        self.assertEqual(result["保护性安全措施证据"][0]["Markdown行号"], 3)

    def test_supports_table_caption_in_its_own_cell(self) -> None:
        result = extract_fx_13_text(
            """|附录 C 已有安全措施分析表|||||
|---|---|---|---|---|
|各安全层面||已有安全措施|类型|功能描述|
|技术层面||访问控制|保护性安全措施|网络防护|
##### 附录 D 脆弱性识别及赋值表
"""
        )
        self.assertEqual(result["定位方式"], "表格内独立表题")
        self.assertEqual(
            result["保护性安全措施证据"][0]["关键词"], "保护性安全措施"
        )

    def test_rejects_toc_and_body_reference_without_target_table(self) -> None:
        with self.assertRaises(FX13TableNotFoundError):
            extract_fx_13_text(
                "[附录C 已有安全措施分析表 36](#附录c)\n"
                "分析结果见附录C已有安全措施分析表。\n"
                + "报告正文。\n" * 10
            )

    def test_reports_explicit_parse_error_as_extraction_error(self) -> None:
        with self.assertRaises(FX13IncompleteMarkdownError):
            extract_fx_13_text(
                '<!-- PARSE_ERROR source="报告.docx" reason="malformed" -->\n'
                "# 风险评估报告解析失败\n"
            )

    def test_failed_current_report_does_not_use_numbered_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            primary = root / "风险评估报告.md"
            primary.write_text(
                '<!-- PARSE_ERROR source="报告.docx" reason="malformed" -->\n'
                "# 风险评估报告解析失败\n",
                encoding="utf-8",
            )
            (root / "风险评估报告（2）.md").write_text(
                """# 附录C 已有安全措施分析表
| 已有安全措施 | 类型 |
| --- | --- |
| 访问控制 | 保护性安全措施 |
# 附录D 下一表
""",
                encoding="utf-8",
            )

            with self.assertRaises(FX13IncompleteMarkdownError):
                extract_fx_13_documents(primary)


if __name__ == "__main__":
    unittest.main()
