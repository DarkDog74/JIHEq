from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.batch_process import format_batch_summary, run_batch


def plan(subject: str = "网页篡改") -> str:
    return f"""# 预案管理名录
| 序号 | 演练记录 |
|---|---|
| 1 | 2025年1月1日<br>开展演练：{subject} |
# 安全事件定级
# 应急响应
一级响应：处置A
二级响应：处置B
"""


def report(subject: str = "网页篡改", *, photo: bool = True) -> str:
    marker = (
        '[[IMAGE id=IMG-001 file="photo.jpg" type="image/jpeg"]] ![p](photo.jpg)'
        if photo
        else "（附上照片）"
    )
    return f"""# 二、演练目标
# 六、演练科目内容
{subject}
# 七、演练时间、地点、人员
## （三）参加演练人员名单
# 九、演练过程
# 十三、演练现场照片记录
{marker}
# 十四、总结
"""


def write_manifest_unit(
    root: Path,
    name: str,
    *,
    plan_text: str | None,
    report_text: str | None,
) -> None:
    unit = root / name
    unit.mkdir()
    records: list[dict[str, object]] = []
    if plan_text is not None:
        (unit / "应急预案.md").write_text(plan_text, encoding="utf-8")
        records.append(
            {"用途": "应急预案", "解析状态": "success", "标准Markdown": "应急预案.md"}
        )
    if report_text is not None:
        (unit / "应急演练报告.md").write_text(report_text, encoding="utf-8")
        records.append(
            {"用途": "应急演练报告", "解析状态": "success", "标准Markdown": "应急演练报告.md"}
        )
    (unit / "应急材料分析.json").write_text(
        json.dumps({"files": records}, ensure_ascii=False), encoding="utf-8"
    )


def grading_report(level: int) -> str:
    return f"""## 2. 采用定级方法及选择权重理由
确定系统的安全等级为第 {level} 级。
## 3. 后续章节
"""


class EmergencyAuditBatchTests(unittest.TestCase):
    def test_yjya_01_batch_classifies_pass_fail_missing_and_missing_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            write_manifest_unit(dataset, "pass", plan_text=plan(), report_text=report())
            write_manifest_unit(dataset, "fail", plan_text=plan(), report_text=report("网页篡改攻击"))
            write_manifest_unit(dataset, "missing", plan_text=plan(), report_text=None)
            write_manifest_unit(dataset, "no-target", plan_text="# 普通预案", report_text=report())
            summary = run_batch(
                dataset_dir=dataset,
                item_id="YJYA-01",
                output_dir=root / "output",
                unit_depth=1,
            )
            self.assertEqual(
                summary["status_counts"],
                {"missing_document": 1, "missing_target_section": 1, "success": 2},
            )
            self.assertEqual(
                summary["audit_counts"],
                {"passed": 1, "failed": 3, "not_applicable": 0},
            )
            terminal = format_batch_summary(summary, root / "output")
            self.assertIn("缺少目标文档", terminal)
            self.assertIn("不存在目标章节", terminal)

    def test_single_document_items_batch_pass_fail_and_missing(self) -> None:
        cases = {
            "YJYA-02": (
                "# 安全事件定级\n正文",
                "目录\n[安全事件定级](#_Toc1)\n# 总则\n正文",
                "plan",
            ),
            "YJYA-03": (
                "# 应急响应\n一级响应\nⅡ级响应",
                "# 应急响应\n一级响应",
                "plan",
            ),
            "YLBG-02": (report(), report("").replace("\n\n# 七", "\n# 七"), "report"),
            "YLBG-03": (report("勒索软件攻击"), report("网页篡改"), "report"),
            "YLBG-04": (report(), report(photo=False), "report"),
        }
        for item_id, (passing, failing, purpose) in cases.items():
            with self.subTest(item_id=item_id), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                dataset = root / "dataset"
                dataset.mkdir()
                if purpose == "plan":
                    write_manifest_unit(dataset, "pass", plan_text=passing, report_text=None)
                    write_manifest_unit(dataset, "fail", plan_text=failing, report_text=None)
                    write_manifest_unit(dataset, "missing", plan_text=None, report_text=report())
                else:
                    write_manifest_unit(dataset, "pass", plan_text=None, report_text=passing)
                    write_manifest_unit(dataset, "fail", plan_text=None, report_text=failing)
                    write_manifest_unit(dataset, "missing", plan_text=plan(), report_text=None)
                summary = run_batch(
                    dataset_dir=dataset,
                    item_id=item_id,
                    output_dir=root / "output",
                    unit_depth=1,
                )
                self.assertEqual(summary["status_counts"], {"missing_document": 1, "success": 2})
                self.assertEqual(
                    summary["audit_counts"],
                    {"passed": 1, "failed": 2, "not_applicable": 0},
                )

    def test_ylbg_01_batch_handles_level_one_pass_and_required_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            level_one = dataset / "level-one"
            level_one.mkdir()
            (level_one / "定级报告.md").write_text(grading_report(1), encoding="utf-8")
            level_two_pass = dataset / "level-two-pass"
            level_two_pass.mkdir()
            (level_two_pass / "定级报告.md").write_text(grading_report(2), encoding="utf-8")
            (level_two_pass / "应急演练报告.md").write_text(report(), encoding="utf-8")
            level_two_fail = dataset / "level-two-fail"
            level_two_fail.mkdir()
            (level_two_fail / "定级报告.md").write_text(grading_report(2), encoding="utf-8")

            summary = run_batch(
                dataset_dir=dataset,
                item_id="YLBG-01",
                output_dir=root / "output",
                unit_depth=1,
            )
            self.assertEqual(summary["status_counts"], {"success": 3})
            self.assertEqual(
                summary["audit_counts"],
                {"passed": 1, "failed": 1, "not_applicable": 1},
            )
            runs = {item["unit"]: item for item in summary["runs"]}
            self.assertFalse(runs["level-one"]["applicable"])
            self.assertEqual(runs["level-one"]["classification"], "not_applicable")
            self.assertEqual(runs["level-two-pass"]["classification"], "passed")
            self.assertEqual(runs["level-two-fail"]["classification"], "failed")


if __name__ == "__main__":
    unittest.main()
