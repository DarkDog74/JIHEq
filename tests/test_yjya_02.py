from __future__ import annotations

import unittest

from audit_rules.yjya_02 import audit_yjya_02
from extractors.yjya_02 import extract_yjya_02_data


class YJYA02Tests(unittest.TestCase):
    def test_actual_heading_with_any_chapter_number_passes(self) -> None:
        fields = extract_yjya_02_data("# 7.3 安全事件定级\n正文")
        self.assertTrue(audit_yjya_02(fields)["passed"])

    def test_toc_only_match_fails(self) -> None:
        markdown = "目录\n[安全事件定级](#_Toc1)\n# 总则\n正文"
        fields = extract_yjya_02_data(markdown)
        self.assertFalse(audit_yjya_02(fields)["passed"])

    def test_plain_paragraph_keyword_is_not_a_markdown_heading(self) -> None:
        fields = extract_yjya_02_data("这里说明安全事件定级，但不是标题。")
        self.assertFalse(audit_yjya_02(fields)["passed"])


if __name__ == "__main__":
    unittest.main()
