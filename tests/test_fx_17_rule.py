from __future__ import annotations

import unittest

from audit_rules.fx_17 import audit_fx_17


def fields(*, exists: bool) -> dict[str, object]:
    evidence = (
        [
            {
                "文件": "风险评估报告.md",
                "Markdown行号": 100,
                "标题文本": "## 6.3.2. 系统资产风险分析",
                "识别方式": "Markdown标题",
            }
        ]
        if exists
        else []
    )
    return {
        "系统资产风险分析标题存在": exists,
        "系统资产风险分析标题证据": evidence,
        "检查报告数": 1,
        "解析异常报告数": 0,
    }


class FX17RuleTests(unittest.TestCase):
    def test_passes_when_title_exists(self) -> None:
        result = audit_fx_17(fields(exists=True))
        self.assertTrue(result["passed"])
        self.assertTrue(result["title_exists"])
        self.assertEqual(result["matched_title_count"], 1)
        self.assertEqual(result["title_report_count"], 1)
        self.assertEqual(result["checked_report_count"], 1)

    def test_fails_when_title_is_missing(self) -> None:
        result = audit_fx_17(fields(exists=False))
        self.assertFalse(result["passed"])
        self.assertEqual(result["issues"], ["缺少“系统资产风险分析”标题"])

    def test_rejects_presence_that_disagrees_with_evidence(self) -> None:
        invalid = fields(exists=True)
        invalid["系统资产风险分析标题证据"] = []
        with self.assertRaisesRegex(ValueError, "存在性与证据不一致"):
            audit_fx_17(invalid)


if __name__ == "__main__":
    unittest.main()
