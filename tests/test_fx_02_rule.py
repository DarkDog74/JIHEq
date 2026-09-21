from __future__ import annotations

import unittest

from audit_rules.fx_02 import audit_fx_02


def fields(level: int, current: str, previous: str, days: int) -> dict[str, object]:
    return {
        "声明安全等级": level,
        "当前风险评估检验结束日期": current,
        "上一份风险评估检验结束日期": previous,
        "时间间隔天数": days,
    }


class FX02RuleTests(unittest.TestCase):
    def test_level_three_exactly_one_calendar_year_passes_across_leap_year(self) -> None:
        result = audit_fx_02(fields(3, "2025-01-01", "2024-01-01", 366))
        self.assertTrue(result["passed"])

    def test_level_three_over_one_year_fails(self) -> None:
        result = audit_fx_02(fields(3, "2025-01-02", "2024-01-01", 367))
        self.assertFalse(result["passed"])
        self.assertIn("超过1年", result["issues"][0])

    def test_level_two_allows_two_years(self) -> None:
        result = audit_fx_02(fields(2, "2026-05-20", "2024-05-20", 730))
        self.assertTrue(result["passed"])
        self.assertEqual(result["maximum_years"], 2)

    def test_current_date_before_previous_fails(self) -> None:
        result = audit_fx_02(fields(2, "2024-01-01", "2025-01-01", -366))
        self.assertFalse(result["passed"])
        self.assertIn("早于", result["issues"][0])

    def test_level_one_is_not_applicable(self) -> None:
        result = audit_fx_02(
            {
                "声明安全等级": 1,
                "当前风险评估检验结束日期": None,
                "上一份风险评估检验结束日期": None,
                "时间间隔天数": None,
            }
        )
        self.assertTrue(result["passed"])
        self.assertFalse(result["applicable"])


if __name__ == "__main__":
    unittest.main()
