from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.batch_process import format_batch_summary, run_batch


def _valid_plan(rows: str) -> str:
    return f"""# 基础安全演练计划

| 序号 | 归属单位 | 参演系统 | 恶意程序类 |  | 网络攻击类 |  | 安全隐患类 |  | 计划演练时间 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|
|  |  |  | 恶意程序感染 | 勒索软件 | 拒绝服务攻击 | 网页篡改 | 弱口令 | 配置错误 |  |  |
{rows}

# 接报演练计划

| 参演系统 | 网络攻击类 | 计划演练时间 |
|---|---|---|
| 其他系统 | √ | 2026年7月 |
"""


def _write_unit(root: Path, name: str, markdown: str | None) -> None:
    unit = root / name
    unit.mkdir()
    if markdown is not None:
        (unit / "2026年演练计划.md").write_text(markdown, encoding="utf-8")


class YLP01BatchTests(unittest.TestCase):
    def test_batch_and_terminal_separate_all_business_and_input_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            output = root / "output"
            dataset.mkdir()
            _write_unit(
                dataset,
                "pass",
                _valid_plan(
                    "\n".join(
                        [
                            "| 1 | 单位甲 | 通过系统甲 | √ |  |  |  |  |  | 2026年7月 |  |",
                            "| 2 | 单位乙 | 通过系统乙 |  |  |  | √ |  |  | 2026年8月 |  |",
                        ]
                    )
                ),
            )
            _write_unit(
                dataset,
                "fail",
                _valid_plan(
                    "\n".join(
                        [
                            "| 1 | 单位甲 | 已勾选系统 |  |  |  |  | √ |  | 2026年7月 |  |",
                            "| 2 | 单位乙 | 未勾选系统 |  |  |  |  |  |  | √ | √ |",
                        ]
                    )
                ),
            )
            _write_unit(dataset, "missing-document", None)
            _write_unit(
                dataset,
                "missing-section",
                "# 接报演练计划\n\n| 参演系统 | 网络攻击类 |\n|---|---|\n| 系统甲 | √ |\n",
            )
            _write_unit(
                dataset,
                "bad-structure",
                "# 基础安全演练计划\n\n| 参演系统 | 演练事件 |\n|---|---|\n| 系统甲 | √ |\n",
            )

            summary = run_batch(
                dataset_dir=dataset,
                item_id="YLP-01",
                output_dir=output,
                unit_depth=1,
            )

            self.assertEqual(
                summary["status_counts"],
                {
                    "extraction_error": 1,
                    "missing_document": 1,
                    "missing_target_section": 1,
                    "success": 2,
                },
            )
            self.assertEqual(
                summary["audit_counts"],
                {"passed": 1, "failed": 4, "not_applicable": 0},
            )
            runs = {record["unit"]: record for record in summary["runs"]}
            self.assertTrue(runs["pass"]["audit_passed"])
            self.assertFalse(runs["fail"]["audit_passed"])
            self.assertEqual(runs["fail"]["checked_rows"], 2)
            self.assertEqual(runs["fail"]["complete_row_count"], 1)
            self.assertEqual(runs["fail"]["incomplete_row_count"], 1)
            self.assertIn("未勾选系统", runs["fail"]["audit_issues"][0])

            terminal = format_batch_summary(summary, output)
            self.assertIn("[YLP-01] ✅ 通过 | pass | 计划行=2 | 已勾选=2 | 未勾选=0", terminal)
            self.assertIn("[YLP-01] ❌ 不通过 | fail | 计划行=2 | 已勾选=1 | 未勾选=1", terminal)
            self.assertIn("未勾选系统", terminal)
            self.assertIn("缺少目标文档：演练计划.md", terminal)
            self.assertIn("不存在目标章节", terminal)
            self.assertIn("未找到包含三类事件", terminal)
            self.assertIn("提取失败：1", terminal)


if __name__ == "__main__":
    unittest.main()
