from __future__ import annotations

import unittest

from audit_rules.fx_13 import audit_fx_13


def fields(
    *,
    type_column_exists: bool = True,
    evidence_count: int = 1,
) -> dict[str, object]:
    return {
        "目标表标题": "附录C 已有安全措施分析表",
        "目标表标题行": 10,
        "定位方式": "Markdown标题",
        "类型列存在": type_column_exists,
        "保护性安全措施证据": [
            {
                "关键词": "保护性安全措施",
                "Markdown行号": 12 + index,
                "来源": "Markdown表格类型列",
            }
            for index in range(evidence_count)
        ],
    }


class FX13RuleTests(unittest.TestCase):
    def test_passes_when_type_column_contains_protective_measure(self) -> None:
        result = audit_fx_13(fields(evidence_count=1))
        self.assertTrue(result["passed"])
        self.assertEqual(result["matched_measure_count"], 1)

    def test_fails_when_type_column_has_no_protective_measure(self) -> None:
        result = audit_fx_13(fields(evidence_count=0))
        self.assertFalse(result["passed"])
        self.assertEqual(
            result["issues"],
            ["已有安全措施分析表“类型”字段未包含“保护性安全措施”"],
        )

    def test_reports_missing_type_column(self) -> None:
        result = audit_fx_13(fields(type_column_exists=False, evidence_count=0))
        self.assertFalse(result["passed"])
        self.assertEqual(result["issues"], ["已有安全措施分析表缺少“类型”字段"])

    def test_accepts_flattened_table_evidence(self) -> None:
        payload = fields()
        payload["定位方式"] = "压平表格行"
        payload["保护性安全措施证据"] = [
            {
                "关键词": "保护性安全措施",
                "Markdown行号": 10,
                "来源": "压平表格类型字段",
            }
        ]
        self.assertTrue(audit_fx_13(payload)["passed"])

    def test_accepts_table_caption_location(self) -> None:
        payload = fields()
        payload["定位方式"] = "表格内独立表题"
        self.assertTrue(audit_fx_13(payload)["passed"])


if __name__ == "__main__":
    unittest.main()
