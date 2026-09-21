from __future__ import annotations

import unittest

from audit_rules.dj_05 import audit_dj_05


VALID_FIELDS = {
    "封面版本": "1.5",
    "封面日期": "2025-05",
    "最新修订版本": "1.5",
    "最新修订日期": "2025-05-31",
}


class DJ05RuleTests(unittest.TestCase):
    def test_passes_when_version_and_year_month_match(self) -> None:
        result = audit_dj_05(VALID_FIELDS)
        self.assertTrue(result["passed"])
        self.assertTrue(result["version_matches"])
        self.assertTrue(result["month_matches"])

    def test_revision_day_does_not_need_to_match_the_cover(self) -> None:
        fields = {**VALID_FIELDS, "最新修订日期": "2025-05-01"}
        self.assertTrue(audit_dj_05(fields)["passed"])

    def test_reports_version_mismatch(self) -> None:
        fields = {**VALID_FIELDS, "封面版本": "1.4"}
        result = audit_dj_05(fields)
        self.assertFalse(result["passed"])
        self.assertIn("封面版本为1.4，最新修订版本为1.5", result["issues"])

    def test_reports_month_mismatch(self) -> None:
        fields = {**VALID_FIELDS, "封面日期": "2025-04"}
        result = audit_dj_05(fields)
        self.assertFalse(result["passed"])
        self.assertIn("封面日期为2025-04，最新修订日期为2025-05-31", result["issues"])

    def test_reports_missing_fields(self) -> None:
        fields = {**VALID_FIELDS, "封面版本": None}
        result = audit_dj_05(fields)
        self.assertFalse(result["passed"])
        self.assertIn("缺少字段：封面版本", result["issues"])


if __name__ == "__main__":
    unittest.main()
