from __future__ import annotations

import unittest

from audit_rules.fhx_01 import audit_fhx_01


def item(item_id: str) -> dict[str, str]:
    return {"编号": item_id}


class FHX01RuleTests(unittest.TestCase):
    def test_passes_when_five_part_id_sets_are_equal(self) -> None:
        result = audit_fhx_01(
            {
                "评测表全部编号": [item("04-05-01-01-01")],
                "需证明材料编号": [item("04-05-01-01-01")],
                "证明材料全部编号": [
                    item("04-05-01-01-01"),
                    item("04-05-01-01-01"),
                ],
            }
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["evaluation_id_count"], 1)
        self.assertEqual(result["proof_id_count"], 1)

    def test_includes_not_applicable_evaluation_ids_in_comparison(self) -> None:
        result = audit_fhx_01(
            {
                "评测表全部编号": [
                    item("04-01-01-01-01"),
                    item("04-05-01-01-01"),
                ],
                "需证明材料编号": [
                    item("04-01-01-01-01"),
                    item("04-05-01-01-01"),
                ],
                "证明材料全部编号": [
                    item("04-01-01-01-01"),
                    item("04-05-01-01-01"),
                ],
            }
        )
        self.assertTrue(result["passed"])

    def test_fails_for_both_missing_and_extra_proof_ids(self) -> None:
        result = audit_fhx_01(
            {
                "评测表全部编号": [
                    item("04-05-01-01-01"),
                    item("04-05-01-02-01"),
                ],
                "需证明材料编号": [
                    item("04-05-01-01-01"),
                    item("04-05-01-02-01"),
                ],
                "证明材料全部编号": [
                    item("04-05-01-01-01"),
                    item("05-05-01-01-01"),
                ],
            }
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["differences"]["证明材料缺少"], ["04-05-01-02-01"])
        self.assertEqual(result["differences"]["证明材料多出"], ["05-05-01-01-01"])

    def test_substantive_explanation_id_does_not_need_proof(self) -> None:
        result = audit_fhx_01(
            {
                "评测表全部编号": [
                    item("04-05-01-01-01"),
                    item("04-05-01-02-01"),
                ],
                "需证明材料编号": [item("04-05-01-02-01")],
                "证明材料全部编号": [item("04-05-01-02-01")],
            }
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["substantive_explanation_count"], 1)
        self.assertEqual(result["proof_required_id_count"], 1)

    def test_proof_for_substantive_explanation_id_is_not_extra(self) -> None:
        result = audit_fhx_01(
            {
                "评测表全部编号": [
                    item("04-05-01-01-01"),
                    item("04-05-01-02-01"),
                ],
                "需证明材料编号": [item("04-05-01-02-01")],
                "证明材料全部编号": [
                    item("04-05-01-01-01"),
                    item("04-05-01-02-01"),
                ],
            }
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["differences"]["证明材料多出"], [])

    def test_all_substantive_explanations_pass_without_inspecting_proof(self) -> None:
        result = audit_fhx_01(
            {
                "评测表全部编号": [item("29-01-01-01-01")],
                "需证明材料编号": [],
                "证明材料全部编号": [item("02-06-01-01-01")],
            }
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["missing_in_proof_count"], 0)
        self.assertEqual(result["extra_in_proof_count"], 0)
        self.assertEqual(result["differences"]["证明材料多出"], [])
        self.assertIn("无需证明材料", result["message"])


if __name__ == "__main__":
    unittest.main()
