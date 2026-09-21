from __future__ import annotations

import unittest

from extractors.fhx_05 import FHX05ExtractionError, extract_fhx_05_data


LEVEL_ONE_REPORT = """## 2. 采用定级方法及选择权重理由
经过计算，k=1.4，确定某系统的安全等级为第1级。
## 3. 安全保护等级
后续章节写第3级不应被提取。
"""


class FHX05ExtractorTests(unittest.TestCase):
    def test_reuses_dj_02_level_and_marks_readable_risk_report(self) -> None:
        result = extract_fhx_05_data(
            LEVEL_ONE_REPORT,
            {
                "files": [
                    {
                        "source": "风险评估报告.docx",
                        "category_name": "风险评估报告",
                        "size_bytes": 1024,
                        "status": "reserved",
                        "error": None,
                    },
                    {
                        "source": "定级报告.docx",
                        "category_name": "定级报告",
                        "size_bytes": 2048,
                    },
                ]
            },
        )

        self.assertEqual(result["声明安全等级"], 1)
        self.assertEqual(len(result["风险评估报告"]), 1)
        self.assertTrue(result["风险评估报告"][0]["可读取"])

    def test_keeps_unreadable_report_and_reason_as_evidence(self) -> None:
        result = extract_fhx_05_data(
            LEVEL_ONE_REPORT,
            {
                "files": [
                    {
                        "source": "损坏的风险评估报告.pdf",
                        "category_name": "风险评估报告",
                        "size_bytes": 0,
                        "status": "failed",
                        "error": "无法打开",
                    }
                ]
            },
        )

        report = result["风险评估报告"][0]
        self.assertFalse(report["可读取"])
        self.assertIn("文件大小无效", report["不可读取原因"])
        self.assertIn("文件分析记录错误：无法打开", report["不可读取原因"])

    def test_extracts_empty_list_when_report_is_absent(self) -> None:
        result = extract_fhx_05_data(
            LEVEL_ONE_REPORT,
            {"files": [{"source": "定级报告.doc", "category_name": "定级报告"}]},
        )
        self.assertEqual(result["风险评估报告"], [])

    def test_rejects_level_outside_method_section(self) -> None:
        with self.assertRaisesRegex(FHX05ExtractionError, "未找到声明安全等级"):
            extract_fhx_05_data("## 3. 安全保护等级\n系统为第1级。", {"files": []})

    def test_rejects_file_analysis_without_files_array(self) -> None:
        with self.assertRaisesRegex(FHX05ExtractionError, "files 数组"):
            extract_fhx_05_data(LEVEL_ONE_REPORT, {})


if __name__ == "__main__":
    unittest.main()
