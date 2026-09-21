from __future__ import annotations

import unittest

from audit_rules.ylp_01 import audit_ylp_01


def _record(
    row_number: int,
    system: str,
    selected_events: list[str],
) -> dict[str, object]:
    return {
        "计划行号": row_number,
        "Markdown行号": row_number + 4,
        "归属单位": f"单位{row_number}",
        "参演系统": system,
        "勾选数量": len(selected_events),
        "已勾选事件": selected_events,
        "勾选单元格": [],
    }


class YLP01RuleTests(unittest.TestCase):
    def test_passes_when_every_effective_row_has_at_least_one_checked_event(self) -> None:
        result = audit_ylp_01(
            {
                "演练计划记录": [
                    _record(1, "系统甲", ["恶意程序类/勒索软件"]),
                    _record(
                        2,
                        "系统乙",
                        ["网络攻击类/网页篡改", "安全隐患类/弱口令"],
                    ),
                ]
            }
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["checked_rows"], 2)
        self.assertEqual(result["complete_row_count"], 2)
        self.assertEqual(result["incomplete_row_count"], 0)
        self.assertEqual(result["未勾选计划行"], [])

    def test_fails_when_any_effective_row_has_no_checked_event(self) -> None:
        result = audit_ylp_01(
            {
                "演练计划记录": [
                    _record(1, "系统甲", ["恶意程序类/恶意程序感染"]),
                    _record(2, "系统乙", []),
                    _record(3, "系统丙", ["安全隐患类/配置错误"]),
                ]
            }
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["checked_rows"], 3)
        self.assertEqual(result["complete_row_count"], 2)
        self.assertEqual(result["incomplete_row_count"], 1)
        self.assertEqual(result["未勾选计划行"][0]["参演系统"], "系统乙")
        self.assertIn("第2行（系统乙）", result["issues"][0])
        self.assertIn("未勾选任何演练事件", result["issues"][0])

    def test_rejects_empty_or_inconsistent_extraction_payload(self) -> None:
        invalid_payloads = [
            {},
            {"演练计划记录": []},
            {
                "演练计划记录": [
                    {
                        "计划行号": 1,
                        "参演系统": "系统甲",
                        "勾选数量": 2,
                        "已勾选事件": ["网络攻击类/网页篡改"],
                    }
                ]
            },
        ]

        for fields in invalid_payloads:
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                audit_ylp_01(fields)


if __name__ == "__main__":
    unittest.main()
