from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.batch_process import format_batch_summary, run_batch


class FX19BatchTests(unittest.TestCase):
    def test_batch_reports_pass_fail_and_missing_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "测试集"
            passed_unit = dataset / "部门A" / "存在标题"
            failed_unit = dataset / "部门A" / "缺少标题"
            missing_unit = dataset / "部门A" / "缺风险报告"
            for unit in (passed_unit, failed_unit, missing_unit):
                unit.mkdir(parents=True)
            (passed_unit / "风险评估报告.md").write_text(
                "## 7.5. 残余风险处置\n", encoding="utf-8"
            )
            (failed_unit / "风险评估报告.md").write_text(
                "目录：残余风险处置\n正文无目标标题。\n", encoding="utf-8"
            )
            output = Path(directory) / "output"

            summary = run_batch(
                dataset_dir=dataset,
                item_id="FX-19",
                output_dir=output,
            )
            terminal = format_batch_summary(summary, output)

        self.assertIn(
            "[FX-19] ✅ 通过 | 部门A/存在标题 | 存在“残余风险处置”标题（1处）",
            terminal,
        )
        self.assertIn(
            "[FX-19] ❌ 不通过 | 部门A/缺少标题 | 缺少“残余风险处置”标题",
            terminal,
        )
        self.assertIn("原因：缺少“残余风险处置”标题", terminal)
        self.assertIn(
            "[FX-19] ❌ 不通过 | 部门A/缺风险报告 | 原因：缺少目标文档：风险评估报告.md",
            terminal,
        )


if __name__ == "__main__":
    unittest.main()
