from __future__ import annotations

import unittest

from audit_rules.dj_02 import REQUIRED_FIELDS, audit_dj_02, level_for_k, round_one


def valid_fields() -> dict[str, int | float]:
    return {
        "社会影响力赋值": 1,
        "规模和服务范围赋值": 1,
        "提供服务的重要性赋值": 1,
        "权重α": 1 / 3,
        "权重β": 1 / 3,
        "权重γ": 1 / 3,
        "声明k值": 1.0,
        "声明安全等级": 1,
    }


class DJ02RuleTests(unittest.TestCase):
    def test_calculates_k_and_compares_declared_level(self) -> None:
        result = audit_dj_02(valid_fields())
        self.assertTrue(result["passed"])
        self.assertEqual(result["calculation"]["calculated_k"], 1.0)
        self.assertEqual(result["calculation"]["calculated_level"], 1)
        self.assertTrue(result["level_matches"])
        self.assertTrue(result["k_matches"])
        self.assertTrue(result["weights_valid"])

    def test_calculates_values_two_three_two_as_level_two(self) -> None:
        fields = valid_fields() | {
            "社会影响力赋值": 2,
            "规模和服务范围赋值": 3,
            "提供服务的重要性赋值": 2,
            "声明安全等级": 2,
            "声明k值": 2.4,
        }
        result = audit_dj_02(fields)
        self.assertTrue(result["passed"])
        self.assertEqual(result["calculation"]["calculated_k"], 2.4)
        self.assertEqual(result["calculation"]["calculated_level"], 2)

    def test_fails_when_declared_level_does_not_match(self) -> None:
        fields = valid_fields()
        fields["声明安全等级"] = 2
        result = audit_dj_02(fields)
        self.assertFalse(result["passed"])
        self.assertFalse(result["level_matches"])
        self.assertTrue(result["k_matches"])

    def test_uses_report_weights_instead_of_equal_weights(self) -> None:
        fields = valid_fields() | {
            "社会影响力赋值": 3,
            "规模和服务范围赋值": 4,
            "提供服务的重要性赋值": 3,
            "权重α": 0.5,
            "权重β": 0.3,
            "权重γ": 0.2,
            "声明k值": 3.4,
            "声明安全等级": 3,
        }
        result = audit_dj_02(fields)
        self.assertTrue(result["passed"])
        self.assertEqual(result["calculation"]["weighted_sum"], 10.4)
        self.assertEqual(result["calculation"]["calculated_k"], 3.4)
        # 等权重也会得到3.4，因此另换一组权重，确保没有暗中使用等权重。
        fields.update({"权重α": 0.1, "权重β": 0.8, "权重γ": 0.1, "声明k值": 3.8})
        result = audit_dj_02(fields)
        self.assertTrue(result["passed"])
        self.assertAlmostEqual(result["calculation"]["weighted_sum"], 14.4)
        self.assertEqual(result["calculation"]["calculated_k"], 3.8)

    def test_incorrect_k_fails_even_when_level_is_correct(self) -> None:
        fields = valid_fields() | {
            "社会影响力赋值": 3,
            "规模和服务范围赋值": 4,
            "提供服务的重要性赋值": 3,
            "权重α": 0.5,
            "权重β": 0.3,
            "权重γ": 0.2,
            "声明k值": 3,
            "声明安全等级": 3,
        }
        result = audit_dj_02(fields)
        self.assertFalse(result["passed"])
        self.assertFalse(result["k_matches"])
        self.assertTrue(result["level_matches"])
        self.assertEqual(result["declared_k"], 3)
        self.assertIn("公式计算k值为3.4，报告声明k值为3", result["issues"])

    def test_fails_when_both_declared_k_and_level_are_wrong(self) -> None:
        result = audit_dj_02(valid_fields() | {"声明k值": 2, "声明安全等级": 2})
        self.assertFalse(result["passed"])
        self.assertFalse(result["k_matches"])
        self.assertFalse(result["level_matches"])
        self.assertEqual(len(result["issues"]), 2)

    def test_declared_k_is_rounded_before_comparison(self) -> None:
        for declared_k in (1.01, 1.04, 1.000000001):
            with self.subTest(declared_k=declared_k):
                result = audit_dj_02(valid_fields() | {"声明k值": declared_k})
                self.assertTrue(result["passed"])
                self.assertTrue(result["k_matches"])
                self.assertTrue(result["level_matches"])
                self.assertEqual(result["declared_k"], declared_k)
                self.assertEqual(result["rounded_declared_k"], 1.0)

    def test_report_341_passes_but_345_and_3_fail_for_calculated_34(self) -> None:
        fields = valid_fields() | {
            "社会影响力赋值": 3, "规模和服务范围赋值": 4,
            "提供服务的重要性赋值": 3, "声明安全等级": 3,
            "权重α": 0.5, "权重β": 0.3, "权重γ": 0.2,
        }
        for declared, passed, rounded in ((3.41, True, 3.4), (3.4, True, 3.4), (3.378511, True, 3.4), (3.45, False, 3.5), (3.0, False, 3.0)):
            with self.subTest(declared=declared):
                result = audit_dj_02(fields | {"声明k值": declared})
                self.assertEqual(result["passed"], passed)
                self.assertEqual(result["rounded_declared_k"], rounded)
                self.assertTrue(result["level_matches"])
        result = audit_dj_02(fields | {"声明k值": 3.41, "声明安全等级": 2})
        self.assertTrue(result["k_matches"])
        self.assertFalse(result["level_matches"])
        self.assertFalse(result["passed"])

    def test_out_of_range_declared_k_fails_without_rounding_error(self) -> None:
        for value in (0, -1, 6, 1e300, 10**400):
            with self.subTest(value=value):
                result = audit_dj_02(valid_fields() | {"声明k值": value})
                self.assertFalse(result["passed"])
                self.assertIn("声明k值", result["invalid_fields"])

    def test_declared_integer_k_and_decimal_k_are_numerically_equal(self) -> None:
        result = audit_dj_02(valid_fields() | {"声明k值": 1})
        self.assertTrue(result["passed"])

    def test_missing_or_null_required_fields_fail_without_defaults(self) -> None:
        for field in REQUIRED_FIELDS:
            for null in (False, True):
                with self.subTest(field=field, null=null):
                    fields = valid_fields()
                    if null:
                        fields[field] = None
                    else:
                        fields.pop(field)
                    result = audit_dj_02(fields)
                    self.assertFalse(result["passed"])
                    self.assertIn(field, result["missing_fields"])
                    self.assertIsNone(result["calculation"]["calculated_k"])

    def test_invalid_values_are_rejected_without_exceptions(self) -> None:
        for field in REQUIRED_FIELDS:
            for value in (True, False, "1", float("nan"), float("inf"), -float("inf")):
                with self.subTest(field=field, value=value):
                    result = audit_dj_02(valid_fields() | {field: value})
                    self.assertFalse(result["passed"])
                    self.assertIn(field, result["invalid_fields"])

    def test_value_and_level_fields_require_integer_one_to_five(self) -> None:
        for field in ("社会影响力赋值", "规模和服务范围赋值", "提供服务的重要性赋值", "声明安全等级"):
            for value in (0, 6, 1.5, 1.0):
                with self.subTest(field=field, value=value):
                    result = audit_dj_02(valid_fields() | {field: value})
                    self.assertFalse(result["passed"])
                    self.assertIn(field, result["invalid_fields"])

    def test_weight_range_and_sum_validation(self) -> None:
        for field in ("权重α", "权重β", "权重γ"):
            for value in (-0.1, 1.1, 10**400):
                with self.subTest(field=field, value=value):
                    result = audit_dj_02(valid_fields() | {field: value})
                    self.assertFalse(result["passed"])
                    self.assertFalse(result["weights_valid"])
                    self.assertIn(field, result["invalid_fields"])
        for weights in ((0, 0, 0), (0.5, 0.5, 0.5), (0.3, 0.3, 0.399998)):
            with self.subTest(weights=weights):
                fields = valid_fields() | dict(zip(("权重α", "权重β", "权重γ"), weights))
                result = audit_dj_02(fields)
                self.assertFalse(result["passed"])
                self.assertFalse(result["weights_valid"])
                self.assertTrue(any("权重之和应为1" in issue for issue in result["issues"]))

    def test_weight_sum_allows_only_small_float_error(self) -> None:
        fields = valid_fields() | {"权重α": 0.3, "权重β": 0.3, "权重γ": 0.4000005}
        self.assertTrue(audit_dj_02(fields)["passed"])

    def test_zero_and_one_weights_are_valid(self) -> None:
        fields = valid_fields() | {"权重α": 0, "权重β": 1, "权重γ": 0}
        self.assertTrue(audit_dj_02(fields)["passed"])

    def test_rounding_is_half_up_and_precedes_level_mapping(self) -> None:
        self.assertEqual(round_one(2.45), 2.5)
        self.assertEqual(round_one(4.45), 4.5)
        fields = valid_fields() | {
            "社会影响力赋值": 2,
            "规模和服务范围赋值": 3,
            "权重α": 0.6,
            "权重β": 0.4,
            "权重γ": 0,
            "声明k值": 2.5,
            "声明安全等级": 3,
        }
        result = audit_dj_02(fields)
        self.assertTrue(result["passed"])
        self.assertLess(result["calculation"]["raw_k"], 2.5)
        self.assertEqual(result["calculation"]["calculated_k"], 2.5)
        self.assertEqual(result["calculation"]["calculated_level"], 3)

    def test_level_mapping_boundaries(self) -> None:
        cases = {
            1.0: 1,
            1.49: 1,
            1.5: 2,
            2.49: 2,
            2.5: 3,
            3.99: 3,
            4.0: 4,
            4.49: 4,
            4.5: 5,
            5.0: 5,
            0.9: None,
            5.1: None,
        }
        for k_value, expected in cases.items():
            with self.subTest(k_value=k_value):
                self.assertEqual(level_for_k(k_value), expected)


if __name__ == "__main__":
    unittest.main()
