from __future__ import annotations

import unittest

from audit_rules.fx_04 import audit_fx_04


def fields(*, topology_count: int, image_lines: list[int]) -> dict[str, object]:
    evidence = [
        {
            "Markdown行号": 10,
            "文件": "风险评估报告.md",
            "拓扑图文字": "- 拓扑图",
            "关联IMAGE行号": image_lines,
        }
        for _ in range(topology_count)
    ]
    return {
        "风险评估报告文件数": 1,
        "可稽核报告文件数": 1,
        "解析异常报告数": 0,
        "拓扑图文字数量": topology_count,
        "关联IMAGE标识数量": len(set(image_lines)),
        "拓扑图证据": evidence,
        "报告检查结果": [
            {"文件": "风险评估报告.md", "解析异常": False}
        ],
    }


class FX04RuleTests(unittest.TestCase):
    def test_passes_when_topology_has_adjacent_image_marker(self) -> None:
        result = audit_fx_04(fields(topology_count=1, image_lines=[11]))
        self.assertTrue(result["passed"])

    def test_fails_when_topology_has_no_adjacent_image_marker(self) -> None:
        result = audit_fx_04(fields(topology_count=1, image_lines=[]))
        self.assertFalse(result["passed"])
        self.assertIn("没有IMAGE标识", result["issues"][0])

    def test_fails_when_topology_text_is_missing(self) -> None:
        result = audit_fx_04(fields(topology_count=0, image_lines=[]))
        self.assertFalse(result["passed"])
        self.assertIn("未找到“拓扑图”", result["issues"][0])

    def test_rejects_inconsistent_evidence_count(self) -> None:
        invalid = fields(topology_count=1, image_lines=[11])
        invalid["关联IMAGE标识数量"] = 0
        with self.assertRaisesRegex(ValueError, "IMAGE标识数量不一致"):
            audit_fx_04(invalid)

    def test_passes_with_parse_warning_when_another_copy_has_image(self) -> None:
        values = fields(topology_count=1, image_lines=[11])
        values["风险评估报告文件数"] = 2
        values["解析异常报告数"] = 1
        values["报告检查结果"].append(
            {"文件": "风险评估报告（2）.md", "解析异常": True}
        )
        result = audit_fx_04(values)
        self.assertTrue(result["passed"])
        self.assertEqual(result["parse_error_count"], 1)
        self.assertEqual(result["warnings"], ["1份风险评估报告副本解析异常"])


if __name__ == "__main__":
    unittest.main()
