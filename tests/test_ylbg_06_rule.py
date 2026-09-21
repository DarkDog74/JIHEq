from __future__ import annotations

import unittest

from audit_rules.ylbg_06 import audit_ylbg_06


def fields(revision_month: str, drill_month: str) -> dict[str, object]:
    return {
        "最新修订月份": revision_month,
        "最新演练月份": drill_month,
        "配对依据": "test",
    }


class YLBG06RuleTests(unittest.TestCase):
    def test_same_month_passes(self) -> None:
        result = audit_ylbg_06(fields("2024-03", "2024-03"))
        self.assertTrue(result["passed"])
        self.assertEqual(result["delta_months"], 0)

    def test_next_month_passes(self) -> None:
        result = audit_ylbg_06(fields("2024-03", "2024-04"))
        self.assertTrue(result["passed"])
        self.assertEqual(result["delta_months"], 1)

    def test_next_year_month_rollover_passes(self) -> None:
        result = audit_ylbg_06(fields("2024-12", "2025-01"))
        self.assertTrue(result["passed"])
        self.assertEqual(result["delta_months"], 1)

    def test_two_months_later_fails(self) -> None:
        result = audit_ylbg_06(fields("2024-03", "2024-05"))
        self.assertFalse(result["passed"])
        self.assertEqual(result["reason_code"], "business_rule_failed")

    def test_earlier_month_fails(self) -> None:
        result = audit_ylbg_06(fields("2024-03", "2024-02"))
        self.assertFalse(result["passed"])
        self.assertIn("早于", result["issues"][0])

    def test_invalid_month_format_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            audit_ylbg_06(fields("2024-13", "2024-03"))


if __name__ == "__main__":
    unittest.main()
