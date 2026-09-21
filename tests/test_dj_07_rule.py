from __future__ import annotations

import unittest

from audit_rules.dj_07 import audit_dj_07


class DJ07RuleTests(unittest.TestCase):
    def test_passes_when_both_requirements_exist(self) -> None:
        result = audit_dj_07(
            {
                "网络拓扑结构章节存在": True,
                "网络拓扑图数量": 1,
                "网络边界划分章节存在": True,
            }
        )
        self.assertTrue(result["passed"])

    def test_fails_when_topology_image_is_outside_the_section(self) -> None:
        result = audit_dj_07(
            {
                "网络拓扑结构章节存在": True,
                "网络拓扑图数量": 0,
                "网络边界划分章节存在": True,
            }
        )
        self.assertFalse(result["passed"])
        self.assertIn("标题章节中缺少IMAGE标识", result["message"])

    def test_reports_each_missing_section(self) -> None:
        result = audit_dj_07(
            {
                "网络拓扑结构章节存在": False,
                "网络拓扑图数量": 0,
                "网络边界划分章节存在": False,
            }
        )
        self.assertFalse(result["passed"])
        self.assertEqual(len(result["issues"]), 2)
        self.assertIn("缺少标题中包含“拓扑”的章节", result["issues"])
        self.assertIn(
            "缺少标题中包含“网络边界划分”的章节", result["issues"]
        )


if __name__ == "__main__":
    unittest.main()
