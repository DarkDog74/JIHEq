from __future__ import annotations

import unittest

from audit_rules.fhx_02 import audit_fhx_02


def answer_row(row: int, answer: str, sequence: str = "01") -> dict[str, object]:
    return {"题目行号": row, "Markdown行号": row + 10, "序号": sequence, "检查点": "是否符合要求？", "答案": answer}


class FHX02RuleTests(unittest.TestCase):
    def test_passes_only_yes_and_not_applicable(self) -> None:
        result = audit_fhx_02({"答案记录": [answer_row(1, "是"), answer_row(2, "不 适 用")]})
        self.assertTrue(result["passed"])
        self.assertEqual(result["invalid_answer_count"], 0)

    def test_rejects_no_blank_other_text_and_multiple_answers(self) -> None:
        result = audit_fhx_02(
            {
                "答案记录": [
                    answer_row(1, "否"),
                    answer_row(2, ""),
                    answer_row(3, "请选择..."),
                    answer_row(4, "是、否"),
                    answer_row(5, "不适用/是"),
                ]
            }
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["invalid_answer_count"], 5)
        self.assertEqual(
            result["invalid_reason_counts"],
            {"答案为否": 1, "答案空白": 1, "答案为其他文字": 1, "同时选择多个互斥答案": 2},
        )
        self.assertIn("第2题（序号01）答案“（空白）”不合法", result["issues"][1])


if __name__ == "__main__":
    unittest.main()
