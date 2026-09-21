from __future__ import annotations

import unittest

from audit_rules.fx_15 import audit_fx_15


def fields(*methods: str) -> dict[str, object]:
    return {
        "风险计算方法章节标题": "6.1 风险计算方法",
        "风险计算方法章节标题行": 10,
        "命中的风险计算方法": list(methods),
        "方法检查": [
            {
                "方法": method,
                "存在": method in methods,
                "Markdown行号": [12] if method in methods else [],
            }
            for method in ("矩阵法", "相乘法")
        ],
    }


class FX15RuleTests(unittest.TestCase):
    def test_passes_and_outputs_matrix_method(self) -> None:
        result = audit_fx_15(fields("矩阵法"))
        self.assertTrue(result["passed"])
        self.assertEqual(result["matched_methods"], ["矩阵法"])

    def test_passes_when_both_methods_exist(self) -> None:
        result = audit_fx_15(fields("矩阵法", "相乘法"))
        self.assertTrue(result["passed"])
        self.assertEqual(result["message"], "风险计算方法章节命中：矩阵法、相乘法")

    def test_fails_when_neither_method_exists(self) -> None:
        result = audit_fx_15(fields())
        self.assertFalse(result["passed"])
        self.assertEqual(result["issues"], ["风险计算方法章节未包含矩阵法或相乘法"])


if __name__ == "__main__":
    unittest.main()
