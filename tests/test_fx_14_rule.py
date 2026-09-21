from __future__ import annotations

import unittest

from audit_rules.fx_14 import audit_fx_14


def fields(keyword: str | None = "预防性安全措施") -> dict[str, object]:
    evidence = []
    if keyword is not None:
        evidence.append(
            {
                "关键词": keyword,
                "Markdown行号": 12,
                "来源": "Markdown表格类型列",
            }
        )
    return {
        "目标表标题": "附录C 已有安全措施分析表",
        "目标表标题行": 10,
        "定位方式": "Markdown标题",
        "类型列存在": True,
        "预防性安全措施证据": evidence,
    }


class FX14RuleTests(unittest.TestCase):
    def test_passes_when_type_column_contains_preventive_measure(self) -> None:
        self.assertTrue(audit_fx_14(fields())["passed"])

    def test_fails_when_preventive_measure_is_missing(self) -> None:
        result = audit_fx_14(fields(None))
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["issues"],
            ["已有安全措施分析表“类型”字段未包含“预防性安全措施”"],
        )


if __name__ == "__main__":
    unittest.main()
