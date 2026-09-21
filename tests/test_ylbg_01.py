from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from audit_rules.ylbg_01 import audit_ylbg_01
from extractors.emergency_audit_common import EmergencyAuditExtractionError
from extractors.ylbg_01 import extract_ylbg_01, extract_ylbg_01_data


def grading_report(level: int | None) -> str:
    conclusion = "未填写" if level is None else f"确定系统的安全等级为第 {level} 级。"
    return f"## 2. 采用定级方法及选择权重理由\n{conclusion}\n## 3. 后续章节\n"


class YLBG01Tests(unittest.TestCase):
    def test_level_one_is_not_applicable_without_report(self) -> None:
        fields = extract_ylbg_01_data(grading_report(1))
        result = audit_ylbg_01(fields)
        self.assertTrue(result["passed"])
        self.assertFalse(result["applicable"])

    def test_level_two_without_report_fails(self) -> None:
        fields = extract_ylbg_01_data(grading_report(2))
        self.assertFalse(audit_ylbg_01(fields)["passed"])

    def test_level_three_with_current_report_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            grading = root / "定级报告.md"
            grading.write_text(grading_report(3), encoding="utf-8")
            (root / "应急演练报告.md").write_text("# 报告", encoding="utf-8")
            fields = extract_ylbg_01(grading)
            self.assertTrue(audit_ylbg_01(fields)["passed"])

    def test_previous_report_does_not_satisfy_current_report_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            grading = root / "定级报告.md"
            grading.write_text(grading_report(2), encoding="utf-8")
            (root / "上一份应急演练报告.md").write_text("# 旧报告", encoding="utf-8")
            fields = extract_ylbg_01(grading)
            self.assertFalse(audit_ylbg_01(fields)["passed"])

    def test_missing_declared_level_is_extraction_error(self) -> None:
        with self.assertRaises(EmergencyAuditExtractionError):
            extract_ylbg_01_data(grading_report(None))


if __name__ == "__main__":
    unittest.main()
