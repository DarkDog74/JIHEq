from __future__ import annotations

import unittest

from audit_rules.fx_09 import audit_fx_09


def fields(*, missing: str | None = None) -> dict[str, object]:
    return {
        "目标表标题": "附录B 威胁识别及赋值表",
        "目标表标题行": 10,
        "表头来源": "Markdown表格",
        "表头行": 12,
        "列检查": [
            {
                "列名": column,
                "存在": column != missing,
                "Markdown行号": [] if column == missing else [12],
            }
            for column in ("威胁来源", "威胁种类", "威胁行为")
        ],
    }


class FX09RuleTests(unittest.TestCase):
    def test_passes_when_all_columns_exist(self) -> None:
        result = audit_fx_09(fields())
        self.assertTrue(result["passed"])

    def test_reports_exact_missing_column(self) -> None:
        result = audit_fx_09(fields(missing="威胁种类"))
        self.assertFalse(result["passed"])
        self.assertEqual(result["issues"], ["威胁识别及赋值表缺少列：威胁种类"])

    def test_accepts_flattened_header_on_same_line_as_plain_title(self) -> None:
        payload = fields()
        payload["表头来源"] = "扁平化表头文本"
        payload["目标表标题行"] = 12
        payload["表头行"] = 12
        for item in payload["列检查"]:  # type: ignore[union-attr]
            item["Markdown行号"] = [12]
        self.assertTrue(audit_fx_09(payload)["passed"])


if __name__ == "__main__":
    unittest.main()
