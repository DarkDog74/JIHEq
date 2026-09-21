from __future__ import annotations

import unittest

from extractors.fx_02 import FX02ExtractionError, extract_fx_02_data, extract_inspection_end_date


def grading_report(level: int) -> str:
    return f"""## 2. 采用定级方法及选择权重理由
经过计算，确定某系统的安全等级为第 {level} 级。
## 3. 安全保护等级
后续章节。
"""


def standard_risk_report(end_date: str) -> str:
    return f"""目录中的检验结束：2099-01-01
# 检验结论
| 检验类别 | 委托检验 | 检验开始 | 2025.1.1 |
| --- | --- | --- | --- |
| 检验来源 | 委托方提供 | 检验结束 | {end_date} |
# 总体评价
后续内容。
"""


class FX02ExtractorTests(unittest.TestCase):
    def test_extracts_end_date_only_from_conclusion_section(self) -> None:
        self.assertEqual(
            extract_inspection_end_date(standard_risk_report("2025.7.31")).isoformat(),
            "2025-07-31",
        )

    def test_extracts_end_date_from_flattened_anydoc_table(self) -> None:
        markdown = """|||检验结论||||
|---|---|---|---|---|---|
||系统名称|系统A|检验开始 检验结束 到样日期 签发日期：[A]|2025.09.30 2025.10.13 2025.9.30||
#### 总体评价
"""
        self.assertEqual(extract_inspection_end_date(markdown).isoformat(), "2025-10-13")

    def test_rejects_invalid_calendar_date(self) -> None:
        with self.assertRaisesRegex(FX02ExtractionError, "日期无效"):
            extract_inspection_end_date(standard_risk_report("2025.9.31"))

    def test_extracts_level_and_two_dates(self) -> None:
        result = extract_fx_02_data(
            grading_report(3),
            standard_risk_report("2025-07-31"),
            standard_risk_report("2024-07-31"),
        )
        self.assertEqual(result["声明安全等级"], 3)
        self.assertEqual(result["当前风险评估检验结束日期"], "2025-07-31")
        self.assertEqual(result["上一份风险评估检验结束日期"], "2024-07-31")
        self.assertEqual(result["时间间隔天数"], 365)

    def test_level_one_does_not_require_risk_reports(self) -> None:
        result = extract_fx_02_data(grading_report(1))
        self.assertEqual(result["声明安全等级"], 1)
        self.assertIsNone(result["当前风险评估检验结束日期"])


if __name__ == "__main__":
    unittest.main()
