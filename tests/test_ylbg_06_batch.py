from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.batch_process import format_batch_summary, run_batch


def plan(day: str | None) -> str:
    value = day or "未填写"
    return f"""# 预案管理名录

| 版本 | 修订记录 |
|---|---|
| V1.0 | {value} |
"""


def report(day: str) -> str:
    return f"# 时间、地点、人员\n\n（一）时间：{day}\n"


def write_unit(root: Path, name: str, revision: str | None, drill: str | None) -> None:
    unit = root / name
    unit.mkdir()
    records: list[dict[str, object]] = []
    (unit / "应急预案.md").write_text(plan(revision), encoding="utf-8")
    records.append(
        {
            "用途": "应急预案",
            "解析状态": "success",
            "源文件": "plan.docx",
            "标准Markdown": "应急预案.md",
        }
    )
    if drill is not None:
        (unit / "应急演练报告.md").write_text(report(drill), encoding="utf-8")
        records.append(
            {
                "用途": "应急演练报告",
                "解析状态": "success",
                "源文件": "report.docx",
                "标准Markdown": "应急演练报告.md",
            }
        )
    (unit / "应急材料分析.json").write_text(
        json.dumps({"files": records}, ensure_ascii=False),
        encoding="utf-8",
    )


class YLBG06BatchTests(unittest.TestCase):
    def test_batch_separates_pass_fail_missing_and_revision_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            output = root / "output"
            dataset.mkdir()
            write_unit(dataset, "pass", "2024年3月1日", "2024年3月1日")
            write_unit(dataset, "fail", "2024年3月1日", "2024年5月1日")
            write_unit(dataset, "missing", "2024年3月1日", None)
            write_unit(dataset, "bad-revision", None, "2024年3月1日")

            summary = run_batch(
                dataset_dir=dataset,
                item_id="YLBG-06",
                output_dir=output,
                unit_depth=1,
            )

            self.assertEqual(summary["status_counts"], {"missing_document": 1, "missing_revision_history": 1, "success": 2})
            self.assertEqual(
                summary["audit_counts"],
                {"passed": 1, "failed": 3, "not_applicable": 0},
            )
            terminal = format_batch_summary(summary, output)
            self.assertIn("修订月份=2024-03", terminal)
            self.assertIn("缺少目标文档：应急演练报告", terminal)
            self.assertIn("不存在修订历史", terminal)


if __name__ == "__main__":
    unittest.main()
