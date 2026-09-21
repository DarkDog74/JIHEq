from __future__ import annotations

import unittest

from audit_rules.ylbg_02 import audit_ylbg_02
from extractors.ylbg_02 import extract_ylbg_02_data


class YLBG02Tests(unittest.TestCase):
    def test_each_supported_subject_title_is_accepted(self) -> None:
        for title in ("演练科目内容", "演练科目及内容", "演练项目"):
            with self.subTest(title=title):
                fields = extract_ylbg_02_data(f"# 六、{title}\n网页篡改\n# 七、其他")
                self.assertTrue(audit_ylbg_02(fields)["passed"])

    def test_empty_subject_section_fails(self) -> None:
        fields = extract_ylbg_02_data("# 六、演练科目内容\n# 七、其他")
        self.assertFalse(audit_ylbg_02(fields)["passed"])

    def test_toc_title_does_not_count(self) -> None:
        markdown = "目录\n[演练科目内容](#_Toc1)\n# 其他\n正文"
        fields = extract_ylbg_02_data(markdown)
        self.assertFalse(fields["演练科目章节存在"])


if __name__ == "__main__":
    unittest.main()
