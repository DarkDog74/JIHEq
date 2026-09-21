from __future__ import annotations

import unittest

from audit_rules.ylbg_03 import audit_ylbg_03
from extractors.ylbg_03 import extract_ylbg_03_data


class YLBG03Tests(unittest.TestCase):
    def test_ransomware_family_keyword_in_subject_section_passes(self) -> None:
        fields = extract_ylbg_03_data("# 六、演练项目\n勒索病毒攻击\n# 七、其他")
        self.assertTrue(audit_ylbg_03(fields)["passed"])

    def test_ransomware_keyword_outside_subject_section_does_not_count(self) -> None:
        markdown = "# 六、演练科目内容\n网页篡改\n# 七、总结\n防范勒索攻击"
        fields = extract_ylbg_03_data(markdown)
        self.assertFalse(audit_ylbg_03(fields)["passed"])

    def test_missing_subject_section_fails(self) -> None:
        fields = extract_ylbg_03_data("# 总结\n开展勒索攻击演练")
        self.assertFalse(audit_ylbg_03(fields)["passed"])


if __name__ == "__main__":
    unittest.main()
