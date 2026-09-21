from __future__ import annotations

import unittest

from audit_rules.fx_10 import audit_fx_10


def fields(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "目标表标题": "附录B 威胁识别及赋值表",
        "目标表标题行": 10,
        "检查规则": "附录B每条记录行至少出现一个赋值数字",
        "赋值记录": rows,
    }


def row(number: int, values: list[str]) -> dict[str, object]:
    return {
        "赋值行号": number,
        "Markdown行号": 10 + number,
        "记录来源": "Markdown表格",
        "原文摘要": f"第{number}条记录",
        "数字": values,
        "是否有数字": bool(values),
    }


class FX10RuleTests(unittest.TestCase):
    def test_passes_when_every_record_line_has_a_number(self) -> None:
        result = audit_fx_10(fields([row(1, ["2"]), row(2, ["0"])]))
        self.assertTrue(result["passed"])
        self.assertEqual(result["complete_row_count"], 2)

    def test_fails_when_appendix_has_no_records(self) -> None:
        result = audit_fx_10(fields([]))
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["issues"], ["附录 B 威胁识别及赋值表没有可检查的记录行"]
        )

    def test_reports_each_record_line_without_a_number(self) -> None:
        result = audit_fx_10(fields([row(1, []), row(2, ["1"])]))
        self.assertFalse(result["passed"])
        self.assertEqual(result["incomplete_row_count"], 1)
        self.assertEqual(result["problem_field_counts"], {"未找到赋值数字": 1})
        self.assertIn("Markdown第11行", result["issues"][0])


if __name__ == "__main__":
    unittest.main()
