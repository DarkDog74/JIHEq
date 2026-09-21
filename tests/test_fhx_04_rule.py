from __future__ import annotations

import unittest

from audit_rules.fhx_04 import audit_fhx_04


def fields(*, required: int, proof_exists: bool) -> dict[str, object]:
    return {
        "评测项总数": 2,
        "答案说明非空项数": 2 - required,
        "答案说明为空项": [
            {
                "评测项行号": index,
                "序号": f"0{index}",
                "需证明材料原因": "答案说明为空",
            }
            for index in range(1, required + 1)
        ],
        "证明材料存在": proof_exists,
    }


class FHX04RuleTests(unittest.TestCase):
    def test_passes_without_proof_when_all_explanations_are_nonempty(self) -> None:
        result = audit_fhx_04(fields(required=0, proof_exists=False))
        self.assertTrue(result["passed"])
        self.assertEqual(result["message"], "答案说明均非空，无需检查证明材料")

    def test_passes_when_required_proof_exists(self) -> None:
        result = audit_fhx_04(fields(required=1, proof_exists=True))
        self.assertTrue(result["passed"])
        self.assertEqual(result["proof_required_count"], 1)
        self.assertEqual(result["message"], "答案说明存在空项，证明材料文件存在")

    def test_fails_when_empty_explanation_has_no_proof(self) -> None:
        result = audit_fhx_04(fields(required=1, proof_exists=False))
        self.assertFalse(result["passed"])
        self.assertEqual(result["issues"], ["答案说明存在空项，证明材料文件不存在"])

    def test_does_not_compare_proof_ids(self) -> None:
        data = fields(required=2, proof_exists=True)
        data["证明材料全部编号"] = []
        result = audit_fhx_04(data)
        self.assertTrue(result["passed"])
        self.assertEqual(result["issues"], [])

    def test_rejects_invalid_or_inconsistent_fields(self) -> None:
        for key, value in [("评测项总数", True), ("答案说明非空项数", -1),
                           ("答案说明非空项数", 5), ("答案说明为空项", None),
                           ("答案说明为空项", [None]), ("证明材料存在", "是")]:
            with self.subTest(key=key, value=value):
                data = fields(required=1, proof_exists=True)
                data[key] = value
                with self.assertRaises(ValueError):
                    audit_fhx_04(data)


if __name__ == "__main__":
    unittest.main()
