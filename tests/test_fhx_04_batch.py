from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.batch_process import format_batch_summary, run_batch


class FHX04BatchTests(unittest.TestCase):
    def test_batch_preserves_pass_fail_missing_document_and_extraction_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "材料"
            header = "| 序号 | 检查点 | 答案 | 答案说明 |\n"
            cases = {
                "非空说明": header + "| 1 | A | 是 | 见机房文档 |",
                "有空项有文件": header + "| 1 | A | 是 | |",
                "有空项无文件": header + "| 1 | A | 是 | |",
                "结构异常": "# 只有标题，没有评测表",
                "缺评测表": None,
            }
            for name, markdown in cases.items():
                unit = dataset / "部门" / name
                unit.mkdir(parents=True)
                if markdown is not None:
                    (unit / "符合性评测表.md").write_text(markdown, encoding="utf-8")
                if name == "有空项有文件":
                    (unit / "符合性评测表证明材料（2）.md").touch()
            output = root / "结果"
            summary = run_batch(dataset_dir=dataset, item_id="FHX-04", output_dir=output)
            self.assertEqual(
                summary["audit_counts"],
                {"passed": 2, "failed": 3, "not_applicable": 0},
            )
            runs = {row["unit"].split("/")[-1]: row for row in summary["runs"]}
            self.assertEqual(runs["缺评测表"]["status"], "missing_document")
            self.assertEqual(runs["结构异常"]["status"], "extraction_error")
            self.assertTrue((output / "部门" / "有空项有文件" / "FHX-04" / "FHX-04.json").is_file())
            self.assertTrue((output / "部门" / "有空项有文件" / "FHX-04" / "稽核结果.json").is_file())
            terminal = format_batch_summary(summary, output)
            self.assertIn("答案说明存在空项，证明材料文件存在", terminal)
            self.assertIn("答案说明存在空项，证明材料文件不存在", terminal)
            self.assertNotIn("修订历史行数", terminal)


if __name__ == "__main__":
    unittest.main()
