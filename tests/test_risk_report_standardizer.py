from __future__ import annotations

import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from parser.risk_report_standardizer import backup_risk_outputs, standardize_risk_reports
from extractors.registry import get_extractor_spec, select_input_documents
from extractors.errors import RequiredDocumentNotFoundError
from extractors.fx_02 import extract_fx_02
from audit_rules.fx_02 import audit_fx_02


def report(date: str) -> str:
    return f"# 检验结论\n| 检验来源 | 委托 | 检验结束 | {date} |\n# 总体评价\n正文\n"


class RiskReportStandardizerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "system"
        self.root.mkdir()
        self.analysis = {"files": []}

    def add(self, name, text, status="success"):
        (self.root / name).write_text(text, encoding="utf-8")
        record = {"category_code": "2.7", "source": name + ".docx", "output": name,
                  "status": status, "warnings": []}
        self.analysis["files"].append(record)
        return record

    def test_reverse_input_dates_swap_names_without_overwrite(self):
        old = self.add("风险评估报告.md", report("2024.8.15"))
        new = self.add("风险评估报告（2）.md", report("2025.7.31"))
        self.assertTrue(standardize_risk_reports(self.root, self.analysis))
        self.assertEqual(old["output"], "上一份风险评估报告.md")
        self.assertEqual(new["output"], "风险评估报告.md")
        self.assertIn("2025.7.31", (self.root / new["output"]).read_text())

    def test_three_reports_in_same_year_sort_by_full_date(self):
        for i, date in enumerate(["2025.1.1", "2025.9.2", "2025.8.3"]):
            self.add(f"风险评估报告（{i+1}）.md", report(date))
        standardize_risk_reports(self.root, self.analysis)
        self.assertIn("2025.9.2", (self.root / "风险评估报告.md").read_text())
        self.assertIn("2025.8.3", (self.root / "上一份风险评估报告.md").read_text())
        self.assertTrue((self.root / "历史风险评估报告_2025-01-01.md").is_file())

    def test_images_and_relative_links_remain_intact(self):
        self.add("风险评估报告.md", report("2024.8.15"))
        image = self.root / "附件/风险评估报告（2）/a.png"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"image fixture")
        content = report("2025.7.31") + '![图](附件/风险评估报告（2）/a.png)'
        record = self.add("风险评估报告（2）.md", content)
        record["asset_directory"] = "附件/风险评估报告（2）"
        standardize_risk_reports(self.root, self.analysis)
        self.assertEqual((self.root / "风险评估报告.md").read_text(), content)
        self.assertEqual(image.read_bytes(), b"image fixture")
        self.assertTrue((self.root / record["asset_directory"]).is_dir())

    def test_unknown_date_does_not_silently_select_older_report(self):
        self.add("风险评估报告.md", "无可定位日期")
        self.add("风险评估报告（2）.md", report("2024.8.15"))
        self.assertFalse(standardize_risk_reports(self.root, self.analysis))
        self.assertFalse((self.root / "风险评估报告.md").exists())
        self.assertEqual(len(list(self.root.glob("待确认*.md"))), 2)
        self.assertEqual(self.analysis["risk_report_ordering"]["status"], "ambiguous")

    def test_same_date_is_not_two_assessments(self):
        self.add("风险评估报告.md", report("2025.7.31"))
        self.add("风险评估报告（2）.md", report("2025.7.31"))
        self.assertFalse(standardize_risk_reports(self.root, self.analysis))
        self.assertFalse((self.root / "上一份风险评估报告.md").exists())

    def test_failed_report_blocks_multireport_ordering(self):
        self.add("风险评估报告.md", "<!-- PARSE_ERROR -->", "failed")
        self.add("风险评估报告（2）.md", report("2025.7.31"))
        self.assertFalse(standardize_risk_reports(self.root, self.analysis))

    def test_single_undated_report_is_kept_for_other_audits(self):
        self.add("风险评估报告.md", "没有日期但有其他内容")
        self.assertTrue(standardize_risk_reports(self.root, self.analysis))
        self.assertTrue((self.root / "风险评估报告.md").is_file())
        self.assertFalse((self.root / "上一份风险评估报告.md").exists())

    def test_rejects_unmanaged_destination_collision(self):
        self.add("风险评估报告.md", report("2024.8.15"))
        self.add("风险评估报告（2）.md", report("2025.7.31"))
        p = self.root / "上一份风险评估报告.md"
        p.write_text("用户文件")
        with self.assertRaises(FileExistsError):
            standardize_risk_reports(self.root, self.analysis)
        self.assertEqual(p.read_text(), "用户文件")

    def test_rename_failure_rolls_back_original_files(self):
        self.add("风险评估报告.md", report("2024.8.15"))
        self.add("风险评估报告（2）.md", report("2025.7.31"))
        original = Path.rename
        calls = 0
        def rename(path, target):
            nonlocal calls
            calls += 1
            if calls == 4:
                raise OSError("模拟移动失败")
            return original(path, target)
        with patch.object(Path, "rename", rename), self.assertRaises(OSError):
            standardize_risk_reports(self.root, self.analysis)
        self.assertEqual((self.root / "风险评估报告.md").read_text(), report("2024.8.15"))
        self.assertEqual((self.root / "风险评估报告（2）.md").read_text(), report("2025.7.31"))

    def test_category_rerun_backups_are_outside_system(self):
        self.add("风险评估报告.md", report("2025.7.31"))
        (self.root / "上一份风险评估报告.md").write_text("old")
        other = self.root / "定级报告.md"
        other.write_text("untouched")
        backup = Path(self.tmp.name) / "backup"
        backup.mkdir()
        with patch("parser.risk_report_standardizer.mkdtemp", return_value=str(backup)):
            backup_risk_outputs(self.root)
        self.assertFalse((self.root / "上一份风险评估报告.md").exists())
        self.assertEqual(other.read_text(), "untouched")
        self.assertEqual((backup / "上一份风险评估报告.md").read_text(), "old")

    def test_all_current_report_items_select_exact_name(self):
        primary = self.root / "风险评估报告.md"
        for name in [primary.name, "上一份风险评估报告.md", "历史风险评估报告_2023-01-01.md", "风险评估报告（2）.md"]:
            (self.root / name).write_text("fixture")
        (self.root / "资产清单.md").write_text("fixture")
        (self.root / "定级报告.md").write_text("fixture")
        for number in [1,3,4,5,6,7,8,9,10,11,13,14,15,16,17,18,19]:
            spec = get_extractor_spec(f"FX-{number:02d}")
            with self.subTest(item=spec.item_id):
                selected = select_input_documents(spec, self.root)
                self.assertIn(primary, selected)
                self.assertNotIn(self.root / "上一份风险评估报告.md", selected)

    def test_missing_current_does_not_fall_back_to_history(self):
        (self.root / "上一份风险评估报告.md").write_text("fixture")
        with self.assertRaises(RequiredDocumentNotFoundError):
            select_input_documents(get_extractor_spec("FX-11"), self.root)

    def test_fx02_uses_sorted_outputs_with_350_day_interval(self):
        self.add("风险评估报告.md", report("2024.8.15"))
        self.add("风险评估报告（2）.md", report("2025.7.31"))
        standardize_risk_reports(self.root, self.analysis)
        grading = self.root / "定级报告.md"
        grading.write_text("## 2. 采用定级方法及选择权重理由\n安全等级为第3级。\n## 3. 安全保护等级\n")
        fields = extract_fx_02(grading)
        self.assertEqual(fields["时间间隔天数"], 350)
        self.assertTrue(audit_fx_02(fields)["passed"])

    def test_parser_finalizes_names_after_all_conversions(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
        self.addCleanup(sys.path.pop, 0)
        import parse_folder
        records = []
        for source in ["older风险评估报告.docx", "newer风险评估报告.docx"]:
            records.append({"category_code": "2.7", "source": source, "status": "ready",
                            "extension": ".docx", "warnings": []})
        analysis = {"files": records}
        def convert(input_dir, output_dir, record, category, md_path, structured_path):
            date = "2024.8.15" if record["source"].startswith("older") else "2025.7.31"
            md_path.write_text(report(date))
            record.update(status="success", output=md_path.name)
        with patch.object(parse_folder, "convert_record", side_effect=convert), \
             patch.object(parse_folder, "write_analysis"):
            code = parse_folder.convert_files(self.root, self.root, analysis, False)
        self.assertEqual(code, 0)
        self.assertEqual(records[0]["output"], "上一份风险评估报告.md")
        self.assertEqual(records[1]["output"], "风险评估报告.md")
