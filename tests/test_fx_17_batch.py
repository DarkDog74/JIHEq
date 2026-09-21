from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.batch_process import format_batch_summary, run_batch


class FX17BatchTests(unittest.TestCase):
    def test_reports_pass_fail_and_missing_document_per_unit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "测试集"
            passed = dataset / "部门A" / "标题存在"
            failed = dataset / "部门A" / "标题缺失"
            missing = dataset / "部门A" / "缺少报告"
            for unit in (passed, failed, missing):
                unit.mkdir(parents=True)
            (passed / "风险评估报告.md").write_text(
                "### 6.3.2. 系统资产风险分析\n", encoding="utf-8"
            )
            (failed / "风险评估报告.md").write_text(
                "正文提到了系统资产风险分析结果，但不是标题。\n", encoding="utf-8"
            )
            output = Path(directory) / "output"
            summary = run_batch(dataset_dir=dataset, item_id="FX-17", output_dir=output)
            terminal = format_batch_summary(summary, output)
            self.assertIn(
                "[FX-17] ✅ 通过 | 部门A/标题存在 | 存在“系统资产风险分析”标题（1处）",
                terminal,
            )
            self.assertIn(
                "[FX-17] ❌ 不通过 | 部门A/标题缺失 | 缺少“系统资产风险分析”标题",
                terminal,
            )
            self.assertIn("缺少“系统资产风险分析”标题", terminal)
            self.assertIn(
                "[FX-17] ❌ 不通过 | 部门A/缺少报告 | 原因：缺少目标文档：风险评估报告.md",
                terminal,
            )
            self.assertTrue(
                (output / "部门A" / "标题存在" / "FX-17" / "FX-17.json").is_file()
            )


if __name__ == "__main__":
    unittest.main()
