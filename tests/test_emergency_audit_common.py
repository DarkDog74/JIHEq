from __future__ import annotations

import unittest

from extractors.emergency_audit_common import (
    extract_latest_plan_drill_subjects,
    extract_report_drill_subjects,
    extract_response_levels,
    find_body_markdown_heading,
    find_named_section,
    section_has_image_marker,
)


class EmergencyAuditCommonTests(unittest.TestCase):
    def test_toc_is_excluded_but_actual_markdown_heading_is_kept(self) -> None:
        markdown = """目录
[1.4. 安全事件定级 3](#_Toc1)
# 第1章 总则
## 2.7 安全事件定级
正文
"""
        evidence = find_body_markdown_heading(markdown, "安全事件定级")
        self.assertEqual([item["行号"] for item in evidence], [4])

    def test_toc_only_keyword_is_not_body_heading(self) -> None:
        markdown = """# 目录
[安全事件定级](#_Toc1)
# 总则
正文
"""
        self.assertEqual(find_body_markdown_heading(markdown, "安全事件定级"), [])

    def test_subject_titles_are_tried_in_declared_order(self) -> None:
        markdown = """**（二）演练项目：**
模拟场景一：旧科目
# 六、演练科目及内容
新科目
# 七、其他
"""
        result = extract_report_drill_subjects(markdown)
        assert result is not None
        self.assertEqual(result["演练科目章节"]["使用标题别名"], "演练科目及内容")
        self.assertEqual(result["演练科目"], ["新科目"])

    def test_image_contaminated_heading_is_recognized(self) -> None:
        markdown = """# [[IMAGE id=IMG-001 file="topology.png" type="image/png"]] ![x](topology.png)六、演练科目内容
网页篡改攻击
# 七、其他
"""
        result = extract_report_drill_subjects(markdown)
        assert result is not None
        self.assertEqual(result["演练科目"], ["网页篡改攻击"])

    def test_latest_plan_record_is_selected_by_date(self) -> None:
        markdown = """# 预案管理名录
| 序号 | 演练记录 | 修订记录 |
|---|---|---|
| 1 | 2023年5月12日<br>开展演练：旧科目 | 2023年5月 |
| 2 | 2024年3月1日<br>开展演练：域名劫持、网页篡改 | 2024年3月 |
"""
        result = extract_latest_plan_drill_subjects(markdown)
        assert result is not None
        self.assertEqual(result["记录日期"], "2024-03-01")
        self.assertEqual(result["演练科目"], ["域名劫持", "网页篡改"])

    def test_combined_roman_response_levels_are_counted_outside_toc(self) -> None:
        markdown = """目录
[Ⅰ级响应](#_Toc1)
[Ⅱ级响应](#_Toc2)
# 应急响应
- Ⅰ级响应
- Ⅱ级响应
- Ⅲ级、Ⅳ级响应
"""
        self.assertEqual(extract_response_levels(markdown)["响应级别"], [1, 2, 3, 4])

    def test_photo_marker_on_next_heading_line_belongs_to_photo_section(self) -> None:
        markdown = """# 十三、演练现场照片记录
# [[IMAGE id=IMG-005 file="photo.png" type="image/png"]] ![p](photo.png)十四、总结
"""
        section = find_named_section(markdown, ("演练现场照片记录",))
        assert section is not None
        self.assertEqual(section_has_image_marker(section), (True, [2]))


if __name__ == "__main__":
    unittest.main()
