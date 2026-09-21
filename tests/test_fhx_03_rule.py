from __future__ import annotations

import unittest

from audit_rules.fhx_03 import audit_fhx_03


class FHX03RuleTests(unittest.TestCase):
    def test_passes_for_standard_type_with_dash_variant(self) -> None:
        result = audit_fhx_03(
            {
                "评测表标题": "通信网络安全防护符合性评测表－增值业务网—消息网",
                "网络类型": "增值业务网—消息网",
                "Markdown行号": 3,
                "备案网络/系统单元类型": "增值业务网-消息网",
            }
        )
        self.assertTrue(result["passed"])
        self.assertTrue(result["filing_match"])
        self.assertEqual(result["match_relation"], "值一致")

    def test_fails_for_system_name_instead_of_standard_type(self) -> None:
        result = audit_fhx_03(
            {
                "评测表标题": "通信网络安全防护符合性评测表－某某系统",
                "网络类型": "某某系统",
                "Markdown行号": 3,
                "备案网络/系统单元类型": "某某系统",
            }
        )
        self.assertFalse(result["passed"])
        self.assertIsNone(result["filing_match"])
        self.assertEqual(result["issues"], ["网络类型“某某系统”不在标准类型表中"])

    def test_fails_when_standard_type_differs_from_filing_type(self) -> None:
        result = audit_fhx_03(
            {
                "评测表标题": "通信网络安全防护符合性评测表－融合网络",
                "网络类型": "融合网络",
                "Markdown行号": 3,
                "备案网络/系统单元类型": "电路域核心交换网",
            }
        )
        self.assertFalse(result["passed"])
        self.assertTrue(result["catalog_match"])
        self.assertFalse(result["filing_match"])
        self.assertEqual(
            result["issues"],
            ["符合性评测表网络类型“融合网络”与备案信息表“电路域核心交换网”不一致"],
        )

    def test_passes_when_assessment_is_parent_of_filing_type(self) -> None:
        result = audit_fhx_03(
            {
                "评测表标题": "通信网络安全防护符合性评测表－信息服务业务系统",
                "网络类型": "信息服务业务系统",
                "Markdown行号": 3,
                "备案网络/系统单元类型": "其他信息服务业务系统",
            }
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["match_relation"], "父子类")

    def test_passes_when_assessment_is_child_of_filing_type(self) -> None:
        result = audit_fhx_03(
            {
                "评测表标题": "通信网络安全防护符合性评测表－5G核心网",
                "网络类型": "5G核心网",
                "Markdown行号": 3,
                "备案网络/系统单元类型": "移动通信网",
            }
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["match_relation"], "父子类")

    def test_passes_for_hierarchical_filing_path(self) -> None:
        result = audit_fhx_03(
            {
                "评测表标题": "通信网络安全防护符合性评测表－支撑网",
                "网络类型": "支撑网",
                "Markdown行号": 3,
                "备案网络/系统单元类型": "支撑网-业务支撑网网管系统-省内网管系统",
            }
        )
        self.assertTrue(result["passed"])
        self.assertEqual(result["match_relation"], "父子类")

    def test_does_not_pass_for_sibling_types(self) -> None:
        result = audit_fhx_03(
            {
                "评测表标题": "通信网络安全防护符合性评测表－融合网络",
                "网络类型": "融合网络",
                "Markdown行号": 3,
                "备案网络/系统单元类型": "移动通信网电路域核心交换网",
            }
        )
        self.assertFalse(result["passed"])
        self.assertIsNone(result["match_relation"])


if __name__ == "__main__":
    unittest.main()
