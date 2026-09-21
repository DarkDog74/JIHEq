from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.batch_process import format_batch_summary, run_batch
from core.pipeline import run_pipeline


_PASS_DOCUMENTS = {
    "DCZ-01": {
        "备案信息表.md": "# 备案信息表\n",
        "定级报告.md": "# 定级报告\n",
        "资产清单.md": "# 资产清单\n",
    },
    "DCZ-02": {
        "应急预案.md": "# 应急预案\n",
        "演练方案.md": "# 演练方案\n",
        "应急演练报告.md": "# 应急演练报告\n",
    },
    "DCZ-03": {
        "应急预案.md": (
            "# 编制目的\n# 事件分类\n# 应急响应\n# 应急组织架构\n"
        ),
        "演练方案.md": "# 演练概况\n# 演练安排\n# 演练流程\n",
        "应急演练报告.md": (
            "# 演练名称\n# 演练时间、地点、人员\n# 演练过程\n"
            "# 演练达到的效果\n"
        ),
    },
    "DCZ-04": {
        "符合性评测表.md": "# 符合性评测表\n",
        "符合性评测报告.md": "# 符合性评测报告\n",
    },
}


def _write_documents(unit: Path, documents: dict[str, str]) -> None:
    unit.mkdir(parents=True, exist_ok=True)
    for filename, content in documents.items():
        (unit / filename).write_text(content, encoding="utf-8")


class DCZPipelineBatchTests(unittest.TestCase):
    def test_each_item_single_unit_pipeline_writes_fields_and_audit_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for item_id, documents in _PASS_DOCUMENTS.items():
                with self.subTest(item_id=item_id):
                    unit = root / item_id / "unit"
                    _write_documents(unit, documents)
                    output = root / item_id / "pipeline"

                    result = run_pipeline(
                        item_id=item_id,
                        input_unit_dir=unit,
                        extracted_output_path=output / f"{item_id}.json",
                        audit_output_path=output / "稽核结果.json",
                    )

                    self.assertTrue(result["audit"]["passed"])
                    self.assertEqual(result["source_document"], str(unit))
                    self.assertTrue((output / f"{item_id}.json").is_file())
                    self.assertTrue((output / "稽核结果.json").is_file())
                    persisted = json.loads(
                        (output / "稽核结果.json").read_text(encoding="utf-8")
                    )
                    self.assertTrue(persisted["passed"])

    def test_each_item_batch_persists_pass_and_business_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for item_id, documents in _PASS_DOCUMENTS.items():
                with self.subTest(item_id=item_id):
                    dataset = root / item_id / "dataset"
                    passing = dataset / "部门" / "通过系统"
                    failing = dataset / "部门" / "缺材料系统"
                    _write_documents(passing, documents)
                    failing.mkdir(parents=True)
                    output = root / item_id / "batch"

                    summary = run_batch(
                        dataset_dir=dataset,
                        item_id=item_id,
                        output_dir=output,
                        unit_depth=2,
                    )
                    terminal = format_batch_summary(summary, output)

                    self.assertEqual(summary["status_counts"], {"success": 2})
                    self.assertEqual(
                        summary["audit_counts"],
                        {"passed": 1, "failed": 1, "not_applicable": 0},
                    )
                    self.assertIn(f"[{item_id}] ✅ 通过 | 部门/通过系统", terminal)
                    self.assertIn(f"[{item_id}] ❌ 不通过 | 部门/缺材料系统", terminal)
                    self.assertIn("原因：", terminal)
                    for unit_name in ("通过系统", "缺材料系统"):
                        item_output = output / "部门" / unit_name / item_id
                        self.assertTrue((item_output / f"{item_id}.json").is_file())
                        self.assertTrue((item_output / "稽核结果.json").is_file())


if __name__ == "__main__":
    unittest.main()
