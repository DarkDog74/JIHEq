from __future__ import annotations

import unittest

from audit_rules.fx_08 import audit_fx_08


def fields(*, missing_columns: tuple[str, ...] = (), missing_value: str | None = None):
    values = {"保密性": "3", "完整性": "3", "可用性": "4"}
    missing = [] if missing_value is None else [missing_value]
    if missing_value is not None:
        values[missing_value] = ""
    return {
        "目标表标题": "A.3 系统组件和单元资产赋值表",
        "目标表标题行": 10,
        "列检查": [
            {"列名": column, "存在": column not in missing_columns, "Markdown行号": [11]}
            for column in ("保密性", "完整性", "可用性")
        ],
        "赋值记录": [
            {
                "赋值行号": 1,
                "Markdown行号": 12,
                "记录编号": "C1",
                **values,
                "缺少赋值": missing,
                "是否完整赋值": not missing,
            }
        ],
    }


class FX08RuleTests(unittest.TestCase):
    def test_passes_when_three_columns_and_every_row_are_assigned(self) -> None:
        result = audit_fx_08(fields())

        self.assertTrue(result["passed"])
        self.assertEqual(result["checked_rows"], 1)
        self.assertEqual(result["complete_row_count"], 1)

    def test_fails_when_required_column_is_missing(self) -> None:
        result = audit_fx_08(fields(missing_columns=("可用性",)))

        self.assertFalse(result["passed"])
        self.assertIn("目标表缺少列：可用性", result["issues"])

    def test_fails_and_reports_record_with_missing_assignment(self) -> None:
        result = audit_fx_08(fields(missing_value="完整性"))

        self.assertFalse(result["passed"])
        self.assertEqual(result["incomplete_row_count"], 1)
        self.assertEqual(result["problem_field_counts"], {"完整性": 1})
        self.assertIn("记录C1", result["issues"][0])

    def test_fails_when_target_table_has_no_records(self) -> None:
        value = fields()
        value["赋值记录"] = []
        result = audit_fx_08(value)

        self.assertFalse(result["passed"])
        self.assertEqual(result["issues"], ["目标表中没有可检查的资产赋值记录"])


if __name__ == "__main__":
    unittest.main()
