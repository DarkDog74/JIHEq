from __future__ import annotations

import unittest

from audit_rules.ylbg_04 import audit_ylbg_04
from extractors.ylbg_04 import extract_ylbg_04_data


def complete_report(photo_line: str) -> str:
    return f"""# 二、演练目标
# 三、演练时间、地点、人员
## （三）参加演练人员名单
# 九、演练过程
# 十三、演练现场照片记录
{photo_line}
# 十四、总结
"""


class YLBG04Tests(unittest.TestCase):
    def test_five_elements_with_photo_marker_pass(self) -> None:
        markdown = complete_report(
            '[[IMAGE id=IMG-001 file="photo.jpg" type="image/jpeg"]] ![p](photo.jpg)'
        )
        fields = extract_ylbg_04_data(markdown)
        self.assertTrue(audit_ylbg_04(fields)["passed"])

    def test_photo_title_without_image_marker_fails(self) -> None:
        fields = extract_ylbg_04_data(complete_report("（附上演练现场照片）"))
        result = audit_ylbg_04(fields)
        self.assertFalse(result["passed"])
        self.assertIn("演练现场照片记录", result["missing_elements"])

    def test_image_in_other_section_does_not_satisfy_photo(self) -> None:
        markdown = """# 演练目标
[[IMAGE id=IMG-001 file="topology.png" type="image/png"]]
# 演练时间
# 参加演练人员名单
# 演练过程
# 演练现场照片记录
# 总结
"""
        fields = extract_ylbg_04_data(markdown)
        self.assertFalse(fields["要素命中"]["演练现场照片记录"])

    def test_supported_aliases_pass(self) -> None:
        markdown = """（二）演练目标：
# 三、演练时间、地点、人员
（三）参演人员
# 五、演练过程
# 九、演练现场
[[IMAGE id=IMG-019 file="photo.jpg" type="image/jpeg"]]
"""
        fields = extract_ylbg_04_data(markdown)
        self.assertTrue(audit_ylbg_04(fields)["passed"])


if __name__ == "__main__":
    unittest.main()
