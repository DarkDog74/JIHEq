from __future__ import annotations

import unittest

from audit_rules.yjya_03 import audit_yjya_03
from extractors.yjya_03 import extract_yjya_03_data


class YJYA03Tests(unittest.TestCase):
    def test_two_different_response_levels_pass(self) -> None:
        fields = extract_yjya_03_data("# 应急响应\n一级响应：处置A\nII级响应：处置B")
        result = audit_yjya_03(fields)
        self.assertTrue(result["passed"])
        self.assertEqual(result["response_levels"], [1, 2])

    def test_one_response_level_fails(self) -> None:
        fields = extract_yjya_03_data("# 应急响应\n三级响应：处置")
        self.assertFalse(audit_yjya_03(fields)["passed"])

    def test_toc_levels_do_not_count(self) -> None:
        markdown = "目录\n[一级响应](#_Toc1)\n[二级响应](#_Toc2)\n# 应急响应\n正文"
        fields = extract_yjya_03_data(markdown)
        self.assertEqual(fields["响应级别"], [])


if __name__ == "__main__":
    unittest.main()
