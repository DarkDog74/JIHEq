from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.batch_process import format_batch_summary, run_batch
from extractors.fx_17 import extract_fx_17_text


class FX17BatchTests(unittest.TestCase):
    def test_recognizes_mainstream_title_forms(self) -> None:
        markdown = (
            "[6.3.2. 系统资产风险分析 25](#toc)\n"
            "### 6.3.2. 系 统 资 产 风 险 分 析\n"
            "正文。\n"
        )
        result = extract_fx_17_text(markdown)
        self.assertEqual(len(result["系统资产风险分析标题证据"]), 2)
        self.assertEqual(
            [item["识别方式"] for item in result["系统资产风险分析标题证据"]],
            ["目录标题项", "Markdown标题"],
        )

    def test_recognizes_pdf_flattened_table_title(self) -> None:
        markdown = "|6.3.2.| 融合边缘云平台 系统资产风险分析 资产风险划分准则如下表：|其他|"
        result = extract_fx_17_text(markdown)
        self.assertEqual(len(result["系统资产风险分析标题证据"]), 1)
        self.assertEqual(
            result["系统资产风险分析标题证据"][0]["识别方式"],
            "PDF压平表格标题",
        )

    def test_accepts_numbered_toc_but_rejects_body_mentions(self) -> None:
        markdown = (
            "[6.3.2. 系统资产风险分析 25](#toc)\n"
            "6.3.2. 系统资产风险分析................ 25\n"
            "|本次风险识别对应的系统资产风险分析结果如下表所示：|6.3.3.|\n"
        )
        result = extract_fx_17_text(markdown)
        self.assertEqual(len(result["系统资产风险分析标题证据"]), 2)
        self.assertEqual(
            {item["识别方式"] for item in result["系统资产风险分析标题证据"]},
            {"目录标题项"},
        )

    def test_batch_reports_pass_fail_and_missing_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "测试集"
            passed_unit = dataset / "部门A" / "存在标题"
            failed_unit = dataset / "部门A" / "缺少标题"
            missing_unit = dataset / "部门A" / "缺风险报告"
            for unit in (passed_unit, failed_unit, missing_unit):
                unit.mkdir(parents=True)
            (passed_unit / "风险评估报告.md").write_text(
                "## 6.3.2. 系统资产风险分析\n", encoding="utf-8"
            )
            (failed_unit / "风险评估报告.md").write_text(
                "目录：系统资产风险分析\n正文只有系统资产风险分析结果。\n", encoding="utf-8"
            )
            output = Path(directory) / "output"

            summary = run_batch(
                dataset_dir=dataset,
                item_id="FX-17",
                output_dir=output,
            )
            terminal = format_batch_summary(summary, output)

        self.assertIn(
            "[FX-17] ✅ 通过 | 部门A/存在标题 | 存在“系统资产风险分析”标题（1处）",
            terminal,
        )
        self.assertIn(
            "[FX-17] ❌ 不通过 | 部门A/缺少标题 | 缺少“系统资产风险分析”标题",
            terminal,
        )
        self.assertIn("原因：缺少“系统资产风险分析”标题", terminal)
        self.assertIn(
            "[FX-17] ❌ 不通过 | 部门A/缺风险报告 | 原因：缺少目标文档：风险评估报告.md",
            terminal,
        )


if __name__ == "__main__":
    unittest.main()
